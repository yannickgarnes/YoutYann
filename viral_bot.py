
import os
import json
import time
import requests
import yt_dlp
import sys
import logging
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.oauth2.credentials import Credentials
# IMPORTANTE: Nueva librería cliente oficial de Google GenAI (v1.0+)
from google import genai
from google.genai import types
from datetime import datetime, timedelta

# --- CONFIGURACIÓN DE LOGGER ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- CONFIGURACIÓN ENV ---
YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY") 
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
CREATOMATE_API_KEY = os.environ.get("CREATOMATE_API_KEY") 
CREATOMATE_TEMPLATE_ID = os.environ.get("CREATOMATE_TEMPLATE_ID") or "c023d838-8e6d-4786-8dce-09695d8f6d3f"
YOUTUBE_TOKEN_JSON = os.environ.get("YOUTUBE_TOKEN_JSON") 

# Canales a monitorear
CHANNELS_TO_WATCH = ["Ibai Llanos", "TheGrefg", "ElRubius", "AuronPlay", "IlloJuan"]

# Inicializar clientes (GLOBALMENTE)
youtube = None 
client_gemini = None # Cliente GenAI nuevo

try:
    if YOUTUBE_API_KEY:
        youtube = build('youtube', 'v3', developerKey=YOUTUBE_API_KEY)
    else:
        logger.error("❌ FALTA LA API KEY DE YOUTUBE en GitHub Secrets.")
    
    if GEMINI_API_KEY:
        # Nueva sintaxis para la librería google-genai v1.0
        client_gemini = genai.Client(api_key=GEMINI_API_KEY)
    else:
         logger.error("❌ FALTA LA API KEY DE GEMINI en GitHub Secrets.")
    
    if not CREATOMATE_API_KEY:
        logger.warning("⚠️ OJO: No veo la API Key de Creatomate. El renderizado fallará.")

except Exception as e:
    logger.error(f"Error grave al iniciar clientes: {e}")
    sys.exit(1)

def search_trending_video():
    """Busca el video más reciente y viral de los canales top"""
    if not youtube:
        logger.error("❌ No puedo buscar videos porque falta YOUTUBE_API_KEY.")
        return None

    yesterday = (datetime.utcnow() - timedelta(days=1)).isoformat("T") + "Z"
    
    query = "|".join(CHANNELS_TO_WATCH)
    logger.info(f"🔍 Buscando videos recientes de: {query}...")
    
    try:
        request = youtube.search().list(
            part="snippet",
            q=query,
            type="video",
            order="date", 
            publishedAfter=yesterday,
            maxResults=1,
            videoDuration="long" 
        )
        response = request.execute()
        
        if not response['items']:
            logger.warning("⚠️ No se encontraron videos nuevos hoy.")
            return None
        
        video = response['items'][0]
        video_title = video['snippet']['title']
        video_id = video['id']['videoId']
        
        logger.info(f"✅ VIDEO ENCONTRADO: {video_title} (https://youtu.be/{video_id})")
        return {
            "id": video_id,
            "title": video_title,
            "url": f"https://www.youtube.com/watch?v={video_id}",
            "channel": video['snippet']['channelTitle']
        }
    except Exception as e:
        logger.error(f"❌ Error buscando en YouTube: {e}")
        return None

def download_audio_and_transcribe(video_url):
    """
    Descarga el audio y lo sube via File API (válido para Gemini y GenAI SDK).
    """
    logger.info("⬇️ Descargando audio del video...")
    
    ydl_opts = {
        'format': 'bestaudio/best',
        'postprocessors': [{'key': 'FFmpegExtractAudio','preferredcodec': 'mp3'}],
        'outtmpl': 'temp_audio',
        'quiet': True,
        'no_warnings': True
    }
    
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([video_url])
            
        logger.info("🧠 Subiendo audio a Google GenAI para análisis...")
        
        if not client_gemini:
             raise ValueError("Cliente Gemini no iniciado (Falta API Key)")

        # Subida con la nueva librería
        # Ojo: la librería nueva puede pedir 'mime_type' explícito
        with open("temp_audio.mp3", "rb") as f:
            upload_response = client_gemini.files.upload(
                file=f,
                config={'mime_type': 'audio/mp3', 'display_name': 'Audio Viral Analysis'}
            )
        
        logger.info(f"Subido con ID: {upload_response.name}. Esperando procesamiento...")

        # Esperar estado ACTIVE
        while True:
            file_meta = client_gemini.files.get(name=upload_response.name)
            if file_meta.state == "ACTIVE":
                break
            elif file_meta.state == "FAILED":
                raise ValueError("Fallo al procesar audio en Google AI")
            time.sleep(2)
            
        return upload_response
        
    except Exception as e:
        logger.error(f"❌ Error en descarga/análisis: {e}")
        return None

def analyze_transcript_for_clipper(audio_file_obj):
    """Usa Gemini 1.5 Flash para encontrar el clip viral escuchando el audio"""
    logger.info("🧠 Gemini está escuchando el audio para encontrar el clip...")
    
    prompt = """
    Actúa como un editor experto de videos virales para TikTok.
    Escucha este audio atentamente. Tu misión es identificar el segmento MÁS DIVERTIDO, IMPACTANTE O VIRAL.
    
    Reglas:
    1. Duración: Entre 30 y 50 segundos.
    2. Debe tener un inicio claro (gancho) y un final coherente.
    3. Retorna la respuesta EXCLUSIVAMENTE en formato JSON.
    
    Formato JSON esperado:
    {
        "start_time": (número en segundos, ej: 120.5),
        "end_time": (número en segundos, ej: 165.2),
        "viral_title": (título clickbait corto con emojis),
        "summary": (breve explicación de por qué es viral)
    }
    """
    
    try:
        if not client_gemini:
            raise ValueError("Modelo Gemini no iniciado")

        response = client_gemini.models.generate_content(
            model='gemini-1.5-flash',
            contents=[
                types.Content(
                    role="user",
                    parts=[
                        types.Part.from_text(text=prompt),
                        types.Part.from_uri(
                            file_uri=audio_file_obj.uri,
                            mime_type=audio_file_obj.mime_type
                        )
                    ]
                )
            ],
            config=types.GenerateContentConfig(
                response_mime_type="application/json"
            )
        )
        
        result = json.loads(response.text)
        logger.info(f"💡 Clip detectado: '{result['viral_title']}' ({result['start_time']}s - {result['end_time']}s)")
        
        # Limpieza
        try:
             client_gemini.files.delete(name=audio_file_obj.name)
             os.remove("temp_audio.mp3") 
        except:
            pass

        return result
        
    except Exception as e:
        logger.error(f"❌ Error en análisis AI: {e}")
        return None

def render_viral_video(video_id, analysis):
    """Manda a renderizar a Creatomate"""
    logger.info("🎨 Renderizando video con subtítulos dinámicos en Creatomate...")
    
    url = "https://api.creatomate.com/v1/renders"
    headers = {
        "Authorization": f"Bearer {CREATOMATE_API_KEY}",
        "Content-Type": "application/json"
    }
    
    modifications = {
        "Video": f"https://www.youtube.com/watch?v={video_id}", 
        "TrimStart": analysis['start_time'],
        "TrimDuration": analysis['end_time'] - analysis['start_time'],
        "Text": analysis['viral_title'], 
    }
    
    payload = {
        "template_id": CREATOMATE_TEMPLATE_ID,
        "modifications": modifications
    }
    
    if not CREATOMATE_API_KEY:
        logger.error("❌ FALTA CREATOMATE_API_KEY. No puedo renderizar.")
        return None

    try:
        response = requests.post(url, headers=headers, json=payload)
        response.raise_for_status() 
        
        render_data = response.json()
        render_id = render_data[0]['id']
        logger.info(f"⏳ Procesando render ({render_id})... Esperando resultado...")
        
        attempts = 0
        while attempts < 60: 
            time.sleep(5)
            status_res = requests.get(f"{url}/{render_id}", headers=headers).json()
            status = status_res['status']
            
            if status == 'succeeded':
                video_url = status_res['url']
                logger.info(f"✨ ¡Video Renderizado!: {video_url}")
                return video_url
            elif status == 'failed':
                logger.error(f"❌ Render falló: {status_res.get('errorMessage')}")
                return None
            attempts += 1
            
        return None

    except Exception as e:
        logger.error(f"❌ Error conectando con Creatomate: {e}")
        if response.status_code == 400:
            logger.error("⚠️ Consejo: Revisa que el ID de la plantilla sea correcto.")
        return None

def upload_to_youtube_shorts(video_url, title, description):
    """Sube el video final a YouTube Shorts"""
    logger.info("🚀 Preparando subida a YouTube Shorts...")
    
    if not YOUTUBE_TOKEN_JSON:
        logger.error("❌ NO HAY TOKEN.JSON: No se puede subir el video automáticamente.")
        return

    try:
        r = requests.get(video_url)
        with open("final_short.mp4", "wb") as f:
            f.write(r.content)

        token_data = json.loads(YOUTUBE_TOKEN_JSON)
        # Importante: refrescar token si ha caducado (Google Auth lo hace solo si tiene refresh token)
        creds = Credentials.from_authorized_user_info(token_data, ['https://www.googleapis.com/auth/youtube.upload'])
        
        service = build('youtube', 'v3', credentials=creds)
        
        body = {
            'snippet': {
                'title': title, 
                'description': description,
                'tags': ['shorts', 'viral', 'clip', 'español'],
                'categoryId': '24' 
            },
            'status': {
                'privacyStatus': 'public', 
                'selfDeclaredMadeForKids': False
            }
        }
        
        media_body = MediaFileUpload("final_short.mp4", chunksize=-1, resumable=True)
        
        logger.info("📡 Subiendo bytes a YouTube...")
        request = service.videos().insert(
            part="snippet,status",
            body=body,
            media_body=media_body
        )
        response = request.execute()
        
        logger.info(f"🎉 ÉXITO TOTAL: Video publicado en https://youtube.com/shorts/{response['id']}")
        return response['id']
        
    except Exception as e:
        logger.error(f"❌ Error subiendo a YouTube: {e}")

def main():
    logger.info("🎬 INICIANDO 'VIRAL CLIIP v2.3 (GenAI Upgrade)'...")
    
    # 1. Buscar
    video_data = search_trending_video()
    if not video_data:
        return # Ya hay log de error dentro

    # 2. Descargar y subir audio (Nuevo cliente GenAI)
    audio_file = download_audio_and_transcribe(video_data['url'])
    if not audio_file:
         return

    # 3. Analizar
    analysis = analyze_transcript_for_clipper(audio_file)
    if not analysis:
         return

    # 4. Renderizar
    final_video_url = render_viral_video(video_data['id'], analysis)
    if not final_video_url:
         return

    # 5. Subir
    full_description = f"{analysis['viral_title']}\n\n#shorts #viral #clips\n\nCréditos: {video_data['channel']}"
    upload_to_youtube_shorts(final_video_url, analysis['viral_title'], full_description)

    logger.info("😴 Ciclo terminado.")

if __name__ == "__main__":
    main()
