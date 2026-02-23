import os
import json
import time
import requests
import yt_dlp
import sys
import logging
from pathlib import Path
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.oauth2.credentials import Credentials
import google.generativeai as genai
from datetime import datetime, timedelta

# --- CONFIGURACIÓN DE LOGGER ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- CONFIGURACIÓN ENV (HARDCODED) ---
ENV_YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY")
ENV_GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

YOUTUBE_API_KEY = ENV_YOUTUBE_API_KEY if ENV_YOUTUBE_API_KEY else "AIzaSyAtgNFFZvAp0C0BZpl57IVVcvShPR1V6cw"
GEMINI_API_KEY = ENV_GEMINI_API_KEY if ENV_GEMINI_API_KEY else "AIzaSyAtgNFFZvAp0C0BZpl57IVVcvShPR1V6cw"

CREATOMATE_API_KEY = os.environ.get("CREATOMATE_API_KEY") 
CREATOMATE_TEMPLATE_ID = os.environ.get("CREATOMATE_TEMPLATE_ID") or "c023d838-8e6d-4786-8dce-09695d8f6d3f"

# Canales a monitorear
CHANNELS_TO_WATCH = ["Ibai Llanos", "TheGrefg", "ElRubius", "AuronPlay", "IlloJuan"]

# Inicializar clientes
youtube = None 
client_gemini = None 

try:
    if YOUTUBE_API_KEY:
        youtube = build('youtube', 'v3', developerKey=YOUTUBE_API_KEY)
        logger.info("✅ YouTube Client OK (Key Hardcoded/Env)")
    else:
        logger.error("❌ FALTA LA API KEY DE YOUTUBE.")
    
    if GEMINI_API_KEY:
        genai.configure(api_key=GEMINI_API_KEY)
        client_gemini = True
        logger.info("✅ Gemini Client OK (Key Hardcoded/Env)")
    else:
         logger.error("❌ FALTA LA API KEY DE GEMINI.")

except Exception as e:
    logger.error(f"Error grave al iniciar clientes: {e}")
    sys.exit(1)

def get_youtube_credentials():
    """Carga credenciales desde la variable de entorno o desde token.json localmente"""
    # pathlib asegura que la ruta sea agnóstica al SO y siempre busque junto a este script
    base_dir = Path(__file__).resolve().parent
    token_file = base_dir / "token.json"
    
    token_data = None
    
    # 1. Intentar desde variable de entorno (Prioridad para producción/GitHub Actions)
    env_token = os.environ.get("YOUTUBE_TOKEN_JSON")
    if env_token:
        try:
            token_data = json.loads(env_token)
            logger.info("✅ Credenciales cargadas desde la variable de entorno YOUTUBE_TOKEN_JSON.")
        except json.JSONDecodeError:
            logger.warning("⚠️ YOUTUBE_TOKEN_JSON en entorno no es JSON válido. ¿Pusiste una ruta? Ignorando y pasando a archivo...")

    # 2. Si no hay variable de entorno válida, intentar desde archivo local
    if not token_data:
        try:
            if not token_file.exists():
                logger.error(f"❌ No se encontró el archivo de credenciales en: {token_file}")
                return None
            
            with open(token_file, 'r', encoding='utf-8') as f:
                token_data = json.load(f)
            logger.info("✅ Credenciales cargadas exitosamente desde token.json local.")
            
        except FileNotFoundError as e:
            logger.error(f"❌ [Error de Archivo] {e}")
            return None
        except json.JSONDecodeError as e:
            logger.error(f"❌ [Error JSON] El archivo {token_file.name} está mal formado: {e}")
            return None
        except PermissionError as e:
            logger.error(f"❌ [Error de Permisos] No se puede leer {token_file.name}: {e}")
            return None
        except Exception as e:
            logger.error(f"❌ [Error Inesperado] al leer token: {e}")
            return None

    # 3. Validación de Esquema (Keys de Google Cloud)
    expected_keys = ['client_id', 'client_secret', 'refresh_token']
    missing = [k for k in expected_keys if k not in token_data]
    if missing:
        logger.error(f"❌ [Error de Esquema] El JSON es inválido. Faltan los campos requeridos: {missing}")
        logger.error("Asegúrate de haber generado el token.json con auth_youtube.py")
        return None

    try:
        # Construir y retornar objeto Credentials de Google
        return Credentials.from_authorized_user_info(
            token_data, 
            scopes=['https://www.googleapis.com/auth/youtube.upload']
        )
    except ValueError as e:
        logger.error(f"❌ [Error Google Auth] El token es incompatible: {e}")
        return None

def search_trending_video():
    """Busca el video más reciente y viral de los canales top"""
    if not youtube:
        logger.error("❌ No puedo buscar videos porque faltan las credenciales de YouTube.")
        return None

    yesterday = (datetime.utcnow() - timedelta(days=1)).isoformat("T") + "Z"
    
    query = "|".join(CHANNELS_TO_WATCH)
    logger.info(f"🔍 Buscando videos ULTRA-VIRALES de: {query}...")
    
    try:
        request = youtube.search().list(
            part="snippet",
            q=f"{query} lo mejor",
            type="video",
            order="viewCount", # <--- BUSCAMOS LO MÁS VISTO
            publishedAfter=(datetime.utcnow() - timedelta(days=7)).isoformat("T") + "Z", # Última semana
            maxResults=5,
            relevanceLanguage="es"
        )
        response = request.execute()
        
        if not response.get('items'):
            logger.warning("⚠️ No se encontraron videos virales nuevos.")
            return None
        
        # Elegimos el primero (el más visto)
        video = response['items'][0]
        video_title = video['snippet']['title']
        video_id = video['id']['videoId']
        
        logger.info(f"✅ VIDEO VIRAL ENCONTRADO: {video_title} (https://youtu.be/{video_id})")
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
    Descarga el audio usando yt-dlp con headers anti-block.
    """
    logger.info("⬇️ Descargando audio del video...")
    
    # Configuración de Bypass Maestro (v3.7: The Claude Special)
    ydl_opts = {
        'format': 'bestaudio/best', 
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '192',
        }],
        'outtmpl': 'temp_audio.%(ext)s',
        'quiet': False, 
        'no_warnings': False,
        'nocheckcertificate': True,
        'ignoreerrors': False,
        'extractor_args': {
            'youtube': {
                'player_client': ['tv_embedded', 'web'], # Basado en sugerencia de Claude para máximo bypass
            }
        },
        'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
    }
    
    # OPCIÓN COOKIES: Autenticación real
    cookies_path = Path(__file__).resolve().parent / "cookies.txt"
    if cookies_path.exists():
        logger.info("🍪 Autenticando con sesión real (cookies.txt)...")
        ydl_opts['cookiefile'] = str(cookies_path)
    
    try:
        # Limpieza previa
        if Path("temp_audio.mp3").exists(): Path("temp_audio.mp3").unlink()
        for f in Path(".").glob("temp_audio.*"): 
            try: f.unlink()
            except: pass
        
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([video_url])
            
        # Verificar si se descargó
        if not Path("temp_audio.mp3").exists():
            # Si yt-dlp descargó algo pero no lo convirtió (ej: temp_audio.m4a o webm)
            for f in Path(".").glob("temp_audio.*"):
                if f.suffix != ".mp3":
                    logger.info(f"Renombrando {f.name} a temp_audio.mp3")
                    f.rename("temp_audio.mp3")
                    break
            
        if not Path("temp_audio.mp3").exists():
            raise ValueError("No se pudo generar el archivo temp_audio.mp3")

        logger.info("🧠 Subiendo audio a Google GenAI para análisis...")
        
        if not client_gemini:
             raise ValueError("Cliente Gemini no iniciado")

        upload_response = genai.upload_file("temp_audio.mp3", mime_type="audio/mp3", display_name="Audio Viral Analysis")
        logger.info(f"Subido con ID: {upload_response.name}. Esperando procesamiento...")

        while True:
            file_meta = genai.get_file(upload_response.name)
            if file_meta.state.name == "ACTIVE":
                break
            elif file_meta.state.name == "FAILED":
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

        model = genai.GenerativeModel('gemini-1.5-flash')
        response = model.generate_content(
            [prompt, audio_file_obj],
            generation_config=genai.GenerationConfig(
                response_mime_type="application/json"
            )
        )
        
        result = json.loads(response.text)
        logger.info(f"💡 Clip detectado: '{result['viral_title']}' ({result['start_time']}s - {result['end_time']}s)")
        
        try:
             genai.delete_file(audio_file_obj.name)
             os.remove("temp_audio.mp3") 
        except Exception as del_e:
            logger.warning(f"No se pudo limpiar el archivo temporal: {del_e}")

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
    
    creds = get_youtube_credentials()
    if not creds:
        logger.error("❌ ABORTANDO: No se pudieron cargar las credenciales de YouTube.")
        return

    try:
        r = requests.get(video_url)
        with open("final_short.mp4", "wb") as f:
            f.write(r.content)

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
    logger.info("🎬 INICIANDO 'VIRAL CLIPPER v3.7 (CLAUDE SPECIAL)'...")
    
    # 1. Buscar
    video_data = search_trending_video()
    if not video_data:
        return 

    # 2. Descargar y subir audio (Nuevo: yt-dlp + headers)
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
