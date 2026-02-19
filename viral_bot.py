
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
from openai import OpenAI
from datetime import datetime, timedelta

# --- CONFIGURACIÓN DE LOGGER (Para verlo todo clarito) ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- CONFIGURACIÓN ENV (GitHub Secrets) ---
YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY") 
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
CREATOMATE_API_KEY = os.environ.get("CREATOMATE_API_KEY") # Clave para renderizar el vídeo
CREATOMATE_TEMPLATE_ID = os.environ.get("CREATOMATE_TEMPLATE_ID") # ID de tu plantilla vertical
YOUTUBE_TOKEN_JSON = os.environ.get("YOUTUBE_TOKEN_JSON") # Contenido del token.json para subir

# Canales a monitorear (si no hay búsqueda genérica)
CHANNELS_TO_WATCH = ["Ibai Llanos", "TheGrefg", "ElRubius", "AuronPlay", "IlloJuan"]

# Inicializar clientes
try:
    if YOUTUBE_API_KEY:
        youtube = build('youtube', 'v3', developerKey=YOUTUBE_API_KEY)
    client = OpenAI(api_key=OPENAI_API_KEY)
except Exception as e:
    logger.error(f"Error al iniciar clientes: {e}")
    sys.exit(1)

def search_trending_video():
    """Busca el video más reciente y viral de los canales top"""
    # Buscar solo videos de las últimas 24 horas
    yesterday = (datetime.utcnow() - timedelta(days=1)).isoformat("T") + "Z"
    
    query = "|".join(CHANNELS_TO_WATCH)
    logger.info(f"🔍 Buscando videos recientes de: {query}...")
    
    try:
        request = youtube.search().list(
            part="snippet",
            q=query,
            type="video",
            order="date", # Lo más nuevo primero
            publishedAfter=yesterday,
            maxResults=1,
            videoDuration="long" # Filtrar videos largos (>20min) para tener "chicha"
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
    """Descarga audio temporal y transcribe con Whisper (optimizado)"""
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
            
        logger.info("👂 Transcribiendo audio con OpenAI Whisper...")
        audio_file = open("temp_audio.mp3", "rb")
        
        # Usamos whisper-1
        transcription = client.audio.transcriptions.create(
            model="whisper-1", 
            file=audio_file,
            response_format="text"
        )
        audio_file.close() # Cerrar archivo
        os.remove("temp_audio.mp3") # Limpiar basura
        
        return transcription
        
    except Exception as e:
        logger.error(f"❌ Error en descarga/transcripción: {e}")
        return None

def analyze_transcript_for_clipper(transcription):
    """Usa GPT-4o para encontrar el clip viral perfecto (Gancho + Desarrollo + Cierre)"""
    logger.info("🧠 El Director AI (GPT-4o) está analizando la transcripción...")
    
    # Acortar texto si es gigante para no quebrar el token limit
    # (Un video de 1 hora son ~9000-10000 palabras)
    transcript_preview = transcription[:25000] 
    
    system_prompt = """
    Eres el mejor editor de TikTok del mundo. Tu trabajo es encontrar el momento MÁS VIRAL en una transcripción.
    
    Reglas de Oro:
    1. Duración: 30 a 55 segundos.
    2. Gancho (0-3s): Debe empezar con una frase fuerte, grito, o declaración polémica.
    3. Cierre: No cortes una frase a medias. Debe sentirse completo.
    
    Formato de Salida JSON:
    {
        "start_time": (float, segundos exactos donde empieza),
        "end_time": (float, segundos exactos donde acaba),
        "viral_title": (string, título clickbait con emojis),
        "captions_highlight": (string, las 3-4 palabras más importantes para resaltar en amarillo)
    }
    """
    
    user_prompt = f"Analiza esta transcripción y dame el clip:\n\n{transcript_preview}"
    
    try:
        response = client.chat.completions.create(
            model="gpt-4o",
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ]
        )
        
        result = json.loads(response.choices[0].message.content)
        logger.info(f"💡 Clip detectado: '{result['viral_title']}' ({result['start_time']}s - {result['end_time']}s)")
        return result
        
    except Exception as e:
        logger.error(f"❌ Error en análisis AI: {e}")
        return None

def render_viral_video(video_id, analysis):
    """Manda a renderizar a Creatomate usando la plantilla Hormozi"""
    logger.info("🎨 Renderizando video con subtítulos dinámicos en Creatomate...")
    
    url = "https://api.creatomate.com/v1/renders"
    headers = {
        "Authorization": f"Bearer {CREATOMATE_API_KEY}",
        "Content-Type": "application/json"
    }
    
    # Mapeo de datos para la plantilla (Asegúrate que tu plantilla tenga estos elementos)
    modifications = {
        "Video": f"https://www.youtube.com/watch?v={video_id}", # Creatomate descarga directo de YT
        "TrimStart": analysis['start_time'],
        "TrimDuration": analysis['end_time'] - analysis['start_time'],
        "Text": analysis['viral_title'], # Título superior (si lo tienes en el template)
        # Ojo: Creatomate tiene auto-transcripción en el template si usas el elemento 'Subtitles'
    }
    
    payload = {
        "template_id": CREATOMATE_TEMPLATE_ID,
        "modifications": modifications
    }
    
    try:
        response = requests.post(url, headers=headers, json=payload)
        response.raise_for_status() # Lanza error si falla HTTP
        
        render_data = response.json()
        render_id = render_data[0]['id']
        logger.info(f"⏳ Procesando render ({render_id})... Esperando resultado...")
        
        # Polling (Esperar a que termine)
        attempts = 0
        while attempts < 60: # Max 5 mins
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
        return None

def upload_to_youtube_shorts(video_url, title, description):
    """Sube el video final a YouTube Shorts"""
    logger.info("🚀 Preparando subida a YouTube Shorts...")
    
    # 1. Descargar el video renderizado al disco local para subirlo
    video_filename = "final_viral_short.mp4"
    r = requests.get(video_url)
    with open(video_filename, "wb") as f:
        f.write(r.content)
        
    # 2. Autenticación OAuth (Usando el token.json secreto)
    if not YOUTUBE_TOKEN_JSON:
        logger.error("❌ NO HAY TOKEN.JSON: No se puede subir el video automáticamente.")
        return

    try:
        # Convertir string JSON de env var a diccionario y luego a credenciales
        token_data = json.loads(YOUTUBE_TOKEN_JSON)
        creds = Credentials.from_authorized_user_info(token_data, ['https://www.googleapis.com/auth/youtube.upload'])
        
        service = build('youtube', 'v3', credentials=creds)
        
        body = {
            'snippet': {
                'title': title, # Máx 100 caracteres
                'description': description,
                'tags': ['shorts', 'viral', 'clip', 'español'],
                'categoryId': '24' # Categoría: Entretenimiento
            },
            'status': {
                'privacyStatus': 'public', # ¡DIRECTO A PÚBLICO!
                'selfDeclaredMadeForKids': False
            }
        }
        
        media_body = MediaFileUpload(video_filename, chunksize=-1, resumable=True)
        
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

# --- ORQUESTADOR PRINCIPAL ---
def main():
    logger.info("🎬 INICIANDO 'VIRAL CLIIP ARCHITECT'...")
    
    # 1. Buscar
    video_data = search_trending_video()
    if not video_data:
        return

    # 2. Transcribir
    transcription = download_audio_and_transcribe(video_data['url'])
    if not transcription:
        return

    # 3. Analizar (AI Director)
    analysis = analyze_transcript_for_clipper(transcription)
    if not analysis:
        return

    # 4. Renderizar (Editar)
    final_video_url = render_viral_video(video_data['id'], analysis)
    if not final_video_url:
        return

    # 5. Subir
    full_description = f"{analysis['viral_title']}\n\n#shorts #viral #ibai #clips\n\nCréditos: {video_data['channel']}"
    upload_to_youtube_shorts(final_video_url, analysis['viral_title'], full_description)

    logger.info("😴 Ciclo terminado. A mimir.")

if __name__ == "__main__":
    main()
