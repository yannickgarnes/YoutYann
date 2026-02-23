import os
import json
import time
import requests
import sys
import logging
from pathlib import Path
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google.oauth2.credentials import Credentials
from google import genai
from datetime import datetime, timedelta

# --- CONFIGURACIÓN DE LOGGER ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- CONFIGURACIÓN ENV ---
YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
CREATOMATE_API_KEY = os.environ.get("CREATOMATE_API_KEY") 
# v7.0: Plantilla vertical profesional con subtítulos dinámicos (Default ID)
CREATOMATE_TEMPLATE_ID = os.environ.get("CREATOMATE_TEMPLATE_ID") or "e402bbbe-cea0-486f-8130-85ba434dfee7"

# Canales a monitorear
CHANNELS_TO_WATCH = ["Ibai Llanos", "TheGrefg", "ElRubius", "AuronPlay", "IlloJuan"]

# Inicializar clientes
youtube = None 
client_gemini = None 

try:
    if YOUTUBE_API_KEY:
        youtube = build('youtube', 'v3', developerKey=YOUTUBE_API_KEY)
        logger.info("✅ YouTube Client OK")
    else:
        logger.error("❌ ERROR: YOUTUBE_API_KEY no encontrada en Secrets.")
    
    if GEMINI_API_KEY:
        # v6.1: Usando el SDK moderno con configuración robusta
        client_gemini = genai.Client(api_key=GEMINI_API_KEY)
        logger.info("✅ Gemini Client OK (v6.1 Security Hardened)")
    else:
         logger.error("❌ ERROR: GEMINI_API_KEY no encontrada en Secrets.")

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
        
        # v8.0: Elegimos uno al azar de los top 5 para evitar repetir siempre el mismo
        import random
        video = random.choice(response['items'])
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

def get_transcript_via_api(video_id):
    """
    Obtiene el transcript del video usando la YouTube Data API oficial.
    No requiere descarga ni cookies.
    """
    logger.info("📝 Intentando obtener transcript oficial de YouTube...")
    
    try:
        # Obtener captions disponibles
        captions_response = youtube.captions().list(
            part="snippet",
            videoId=video_id
        ).execute()
        
        items = captions_response.get('items', [])
        if not items:
            logger.warning("⚠️ No hay captions disponibles. Usando título/descripción como contexto.")
            return None
            
        # Buscar caption en español o el primero disponible
        caption_id = None
        for item in items:
            lang = item['snippet']['language']
            if lang.startswith('es'):
                caption_id = item['id']
                break
        if not caption_id:
            caption_id = items[0]['id']
        
        logger.info(f"✅ Caption encontrado (ID: {caption_id})")
        return caption_id
        
    except Exception as e:
        logger.error(f"❌ Error obteniendo transcript: {e}")
        return None

def get_video_details(video_id):
    """Obtiene título, descripción y duración del video para el análisis."""
    try:
        response = youtube.videos().list(
            part="snippet,contentDetails,statistics",
            id=video_id
        ).execute()
        
        if not response.get('items'):
            return None
            
        item = response['items'][0]
        return {
            'title': item['snippet']['title'],
            'description': item['snippet']['description'][:2000],
            'duration': item['contentDetails']['duration'],
            'views': item['statistics'].get('viewCount', '0'),
            'likes': item['statistics'].get('likeCount', '0'),
        }
    except Exception as e:
        logger.error(f"❌ Error obteniendo detalles: {e}")
        return None

def analyze_video_for_clipper(video_data):
    """
    Usa Gemini para inferir el mejor clip basándose en
    título, descripción y estadísticas. Sin audio necesario (v5.0).
    """
    logger.info("🧠 Gemini analizando metadatos del video...")
    
    details = get_video_details(video_data['id'])
    if not details:
        return None
    
    prompt = f"""
    Actúa como un editor experto de videos virales para TikTok/YouTube Shorts.
    
    Tienes este video de YouTube:
    - Título: {details['title']}
    - Canal: {video_data['channel']}
    - Vistas: {details['views']}
    - Likes: {details['likes']}
    - Duración ISO: {details['duration']}
    - Descripción: {details['description']}
    
    sería el MÁS VIRAL para un Short de 15-58 segundos. 
    Busca un "HOOK" (gancho) potente para que el video empiece con mucha energía.
    El título debe ser corto y llamativo para los subtítulos dinámicos.
    
    Responde EXCLUSIVAMENTE en JSON:
    {{
        "start_time": (número en segundos, inicio estimado del momento más viral),
        "end_time": (número en segundos, fin del clip),
        "viral_title": (título clickbait corto con emojis para Shorts),
        "summary": (por qué este momento sería viral)
    }}
    """
    
    # v6.2: Auto-descubrimiento de modelos
    logger.info("🔍 Descubriendo modelos disponibles para esta API Key...")
    
    try:
        available_models = [m.name for m in client_gemini.models.list()]
        # Filtramos solo los que soportan generación de contenido
        flash_models = [m for m in available_models if 'flash' in m.lower()]
        other_models = [m for m in available_models if m not in flash_models]
        
        # Prioridad: Flash models primero (más baratos/rápidos)
        model_names = flash_models + other_models
        logger.info(f"📋 Modelos encontrados: {model_names}")
    except Exception as e:
        logger.warning(f"⚠️ No se pudo listar modelos: {e}")
        # Fallback a lista estática v6.1 si el listado falla
        model_names = ['gemini-1.5-flash', 'gemini-2.0-flash', 'gemini-1.5-pro']

    last_error = None
    for name in model_names:
        # Limpiar prefijo 'models/' si existe para el SDK moderno (el SDK ya lo gestiona)
        clean_name = name.split('/')[-1]
        
        try:
            logger.info(f"Probando Gemini (v6.2): {clean_name}...")
            response = client_gemini.models.generate_content(
                model=clean_name,
                contents=prompt,
                config={'response_mime_type': 'application/json'}
            )
            
            result = json.loads(response.text)
            logger.info(f"✅ ¡ÉXITO! Modelo utilizado: '{clean_name}'")
            logger.info(f"💡 Clip inferido: '{result['viral_title']}' ({result['start_time']}s - {result['end_time']}s)")
            return result
            
        except Exception as e:
            last_error = str(e)
            if "429" in last_error:
                logger.warning(f"⏳ Modelo '{clean_name}' sin cuota (429). Probando siguiente...")
            elif "404" in last_error:
                logger.warning(f"❓ Modelo '{clean_name}' no encontrado (404).")
            else:
                logger.warning(f"⚠️ Falló modelo '{clean_name}': {e}")
            continue
            
    # Si llegamos aquí, todos fallaron
    logger.error(f"❌ MISIÓN FALLIDA: Ningún modelo Gemini funcionó. Último error: {last_error}")
    return None

def render_viral_video(video_id, analysis):
    """
    v8.0: PURE JSON SOURCE ENGINE (Bypass Templates)
    Crea un diseño profesional vertical con auto-zoom y subtítulos dinámicos.
    """
    logger.info("🎨 Renderizando video con el Motor 'Pure JSON' (Estilo Vizard)...")
    
    url = "https://api.creatomate.com/v1/renders"
    headers = {
        "Authorization": f"Bearer {CREATOMATE_API_KEY}",
        "Content-Type": "application/json"
    }

    # Definimos la composición profesional desde cero
    source = {
        "output_format": "mp4",
        "width": 1080,
        "height": 1920,
        "frame_rate": 30,
        "elements": [
            {
                # Elemento 1: El Video original con Auto-Zoom (Vizard Style)
                "type": "video",
                "track": 1,
                "source": f"https://www.youtube.com/watch?v={video_id}",
                "time": 0,
                "duration": min(analysis['end_time'] - analysis['start_time'], 58),
                "trim_start": analysis['start_time'],
                "fit": "cover", # Esto hace el zoom vertical para que no se vea como "fotos"
                "x_alignment": "50%",
                "y_alignment": "50%"
            },
            {
                # Elemento 2: Subtítulos dinámicos automáticos (Word-by-word)
                "type": "text",
                "track": 2,
                "transcript_source": "video-1", # Referencia al audio del elemento anterior
                "time": 0,
                "duration": "video-1.duration",
                "y": "80%", # Posición en la parte inferior
                "width": "90%",
                "height": "15%",
                "text_alignment": "center",
                "font_family": "Montserrat",
                "font_weight": "900",
                "font_size": "75 px",
                "text_transform": "uppercase",
                "color": "#ffff00", # Amarillo Vizard
                "stroke_color": "#000000",
                "stroke_width": "3 px",
                "background_color": "rgba(0,0,0,0.4)",
                "padding": "20 px",
                "animations": [
                    {
                        "type": "text-appearance",
                        "scope": "word", # Aparece palabra por palabra
                        "duration": "0.1 s"
                    }
                ]
            }
        ]
    }
    
    # Asignamos IDs para las referencias de transcript
    source["elements"][0]["id"] = "video-1"

    payload = {
        "source": source
    }
    
    if not CREATOMATE_API_KEY:
        logger.error("❌ FALTA CREATOMATE_API_KEY.")
        return None

    try:
        response = requests.post(url, headers=headers, json=payload)
        
        if response.status_code not in [200, 202]:
            logger.error(f"❌ Error de Creatomate ({response.status_code}): {response.text}")
            return None

        render_data = response.json()
        render_id = render_data[0]['id']
        logger.info(f"⏳ Procesando render ({render_id})...")
        
        attempts = 0
        while attempts < 120: # 10 mins max
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
        logger.error(f"❌ Error en Creatomate Engine: {e}")
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
    logger.info("🎬 INICIANDO 'VIRAL CLIPPER v8.0 (PURE AI ENGINE)'...")
    
    # 1. Buscar video viral
    video_data = search_trending_video()
    if not video_data:
        return

    # 2. Analizar con Gemini (SIN descarga, usando metadatos)
    analysis = analyze_video_for_clipper(video_data)
    if not analysis:
        return

    # 3. Renderizar con Creatomate (Offloading clipping a la nube)
    final_video_url = render_viral_video(video_data['id'], analysis)
    if not final_video_url:
        return

    # 4. Subir a YouTube Shorts
    title_with_tag = f"{analysis['viral_title']} #Shorts"
    full_description = f"{analysis['viral_title']}\n\n#shorts #viral #clips #español\n\nCréditos: {video_data['channel']}"
    upload_to_youtube_shorts(final_video_url, title_with_tag, full_description)

    logger.info("😴 Ciclo terminado.")

if __name__ == "__main__":
    main()
