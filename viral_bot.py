
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
import google.generativeai as genai
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
youtube = None # Definir variable global primero
model = None

try:
    if YOUTUBE_API_KEY:
        youtube = build('youtube', 'v3', developerKey=YOUTUBE_API_KEY)
    else:
        logger.error("❌ FALTA LA API KEY DE YOUTUBE: Revisa los secretos de GitHub.")
    
    if GEMINI_API_KEY:
        genai.configure(api_key=GEMINI_API_KEY)
        model = genai.GenerativeModel('gemini-1.5-flash') 
    else:
         logger.error("❌ FALTA LA API KEY DE GEMINI.")

except Exception as e:
    logger.error(f"Error grave al iniciar clientes: {e}")
    sys.exit(1)

def search_trending_video():
    """Busca el video más reciente y viral de los canales top"""
    if not youtube:
        logger.error("❌ No puedo buscar videos porque el cliente de YouTube no se ha iniciado.")
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
    Descarga el audio y lo sube a Gemini para que lo escuche.
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
            
        logger.info("🧠 Subiendo audio a Gemini para análisis directo...")
        
        if not model:
             raise ValueError("Cliente Gemini no iniciado")

        audio_file = genai.upload_file(path="temp_audio.mp3")
        
        while audio_file.state.name == "PROCESSING":
            time.sleep(2)
            audio_file = genai.get_file(audio_file.name)

        if audio_file.state.name == "FAILED":
            raise ValueError("Fallo al procesar audio en Gemini")
            
        return audio_file
        
    except Exception as e:
        logger.error(f"❌ Error en descarga/análisis: {e}")
        return None

def analyze_transcript_for_clipper(audio_file_gemini):
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
        if not model:
            raise ValueError("Modelo Gemini no iniciado")

        response = model.generate_content(
            [prompt, audio_file_gemini],
            generation_config={"response_mime_type": "application/json"}
        )
        
        result = json.loads(response.text)
        logger.info(f"💡 Clip detectado: '{result['viral_title']}' ({result['start_time']}s - {result['end_time']}s)")
        
        # Limpieza
        try:
            genai.delete_file(audio_file_gemini.name)
            os.remove("temp_audio.mp3") 
        except:
            pass

        return result
        
    except Exception as e:
        logger.error(f"❌ Error en análisis AI: {e}")
        return None

def render_viral_video(video_id, analysis):
    """Manda a renderizar a Creatomate usando la plantilla correcta (Auto-Captions)"""
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
            logger.error("⚠️ Consejo: Revisa que el ID de la plantilla sea correcto y acepte 'Video' como modificación.")
        return None

def upload_to_youtube_shorts(video_url, title, description):
    """Sube el video final a YouTube Shorts"""
    logger.info("🚀 Preparando subida a YouTube Shorts...")
    
    if not YOUTUBE_TOKEN_JSON:
        logger.error("❌ NO HAY TOKEN.JSON: No se puede subir el video automáticamente.")
        return

    try:
        # Descargar video
        r = requests.get(video_url)
        with open("final_short.mp4", "wb") as f:
            f.write(r.content)

        token_data = json.loads(YOUTUBE_TOKEN_JSON)
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
    logger.info("🎬 INICIANDO 'VIRAL CLIIP v2.2 (Bug Fix)'...")
    
    # 1. Buscar
    video_data = search_trending_video()
    if not video_data:
        logger.error("❌ No se pudo completar la búsqueda de videos.")
        return

    # 2. Descargar y subir audio a Gemini
    audio_file = download_audio_and_transcribe(video_data['url'])
    if not audio_file:
         logger.error("❌ Fallo en la descarga o subida del audio.")
         return

    # 3. Analizar con Gemini Flash
    analysis = analyze_transcript_for_clipper(audio_file)
    if not analysis:
         logger.error("❌ Fallo en el análisis de Gemini.")
         return

    # 4. Renderizar
    final_video_url = render_viral_video(video_data['id'], analysis)
    if not final_video_url:
         logger.error("❌ Fallo en el renderizado con Creatomate.")
         return

    # 5. Subir
    full_description = f"{analysis['viral_title']}\n\n#shorts #viral #clips\n\nCréditos: {video_data['channel']}"
    upload_to_youtube_shorts(final_video_url, analysis['viral_title'], full_description)

    logger.info("😴 Ciclo terminado.")

if __name__ == "__main__":
    main()
