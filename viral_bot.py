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
        
        # v8.1: Aleatoriedad real para evitar bucles de "mismo video"
        import random
        random.shuffle(response['items'])
        video = response['items'][0]
        video_title = video['snippet']['title']
        video_id = video['id']['videoId']
        
        logger.info(f"✅ VIDEO VIRAL ELEGIDO (v8.1): {video_title} (https://youtu.be/{video_id})")
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
    
    Basándote en el título y la descripción, infiere qué momento del video 
    sería el MÁS VIRAL para un Short de 15-58 segundos. 
    Busca un "HOOK" (gancho) potente para que el video empiece con mucha energía.
    IMPORTANTE: Evita los primeros 15-30 segundos si son intros o música.
    El título debe ser muy corto (2-4 palabras) para los subtítulos dinámicos.
    
    Responde EXCLUSIVAMENTE en JSON:
    {{
        "start_time": (número en segundos),
        "end_time": (número en segundos),
        "viral_title": (título clickbait muy corto),
        "summary": (por qué este momento es viral)
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

def get_direct_video_url(youtube_url):
    """
    v13.0: DOWNLOAD + CATBOX UPLOAD ENGINE.
    Descarga el vídeo con yt-dlp+ffmpeg y lo sube a catbox.moe (host temporal
    gratuito). Creatomate recibe una URL pública estable que siempre funciona.
    Mucho más fiable que intentar extraer URLs directas de YouTube (IP-locked).
    """
    import tempfile
    import glob
    import shutil

    logger.info(f"⬇️ Descargando vídeo: {youtube_url} (Motor v13.0 Download+Upload)...")

    tmpdir = tempfile.mkdtemp()
    output_template = os.path.join(tmpdir, 'video.%(ext)s')
    cookie_file = None

    try:
        import yt_dlp

        # --- Cookies ---
        cookies_content = os.environ.get("YOUTUBE_COOKIES", "")
        if cookies_content:
            tmp = tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False, encoding='utf-8')
            if not cookies_content.strip().startswith("# Netscape HTTP Cookie File"):
                tmp.write("# Netscape HTTP Cookie File\n")
            tmp.write(cookies_content)
            tmp.flush()
            cookie_file = tmp.name
            tmp.close()
            logger.info("🍪 Usando cookies de YouTube del entorno.")
        else:
            logger.warning("⚠️ YOUTUBE_COOKIES no encontrado. Intentando sin autenticación...")

        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            # 720p máx para limitar tamaño del archivo; ffmpeg combina vídeo+audio
            'format': 'bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<=720]+bestaudio/best[height<=720]/best',
            'outtmpl': output_template,
            'merge_output_format': 'mp4',
        }
        if cookie_file:
            ydl_opts['cookiefile'] = cookie_file

        logger.info("📥 Iniciando descarga (máx 720p)...")
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([youtube_url])

        # Buscar el archivo descargado
        files = glob.glob(os.path.join(tmpdir, 'video.*'))
        if not files:
            logger.error("❌ No se encontró archivo descargado en el directorio temporal.")
            return None

        video_path = files[0]
        file_size_mb = os.path.getsize(video_path) / (1024 * 1024)
        logger.info(f"✅ Descarga completada ({file_size_mb:.1f} MB). Subiendo a host temporal...")

        # --- Subida a catbox.moe (gratis, sin registro, URL permanente) ---
        with open(video_path, 'rb') as f:
            upload_resp = requests.post(
                'https://catbox.moe/user/api.php',
                data={'reqtype': 'fileupload'},
                files={'fileToUpload': ('video.mp4', f, 'video/mp4')},
                timeout=300
            )

        if upload_resp.status_code == 200 and upload_resp.text.strip().startswith('https://'):
            hosted_url = upload_resp.text.strip()
            logger.info(f"✅ ¡ÉXITO! Vídeo alojado públicamente en: {hosted_url}")
            return hosted_url
        else:
            logger.warning(f"⚠️ catbox.moe falló ({upload_resp.status_code}): {upload_resp.text[:100]}. Probando 0x0.st...")

        # --- Fallback: 0x0.st ---
        with open(video_path, 'rb') as f:
            upload_resp2 = requests.post('https://0x0.st', files={'file': ('video.mp4', f, 'video/mp4')}, timeout=300)

        if upload_resp2.status_code == 200 and upload_resp2.text.strip().startswith('https://'):
            hosted_url = upload_resp2.text.strip()
            logger.info(f"✅ ¡ÉXITO (fallback 0x0.st)! Vídeo en: {hosted_url}")
            return hosted_url
        else:
            logger.error(f"❌ 0x0.st también falló ({upload_resp2.status_code}): {upload_resp2.text[:100]}")

    except ImportError:
        logger.error("❌ yt-dlp no instalado. Añade 'yt-dlp' a requirements.txt")
    except Exception as e:
        logger.error(f"❌ Error en Motor v13.0: {e}")
    finally:
        if cookie_file:
            try:
                os.unlink(cookie_file)
            except Exception:
                pass
        try:
            shutil.rmtree(tmpdir, ignore_errors=True)
        except Exception:
            pass

    logger.error("🚨 Fallo total. Creatomate no podrá procesar la fuente.")
    return None

def render_viral_video(video_id, analysis):

    """
    v9.0: BULLETPROOF RENDER ENGINE
    Si falla el render con subtítulos, intenta uno básico para no perder el video.
    """
    logger.info(f"🎨 INICIANDO MOTOR v9.0 (Clip: {analysis['viral_title']})...")
    
    url = "https://api.creatomate.com/v1/renders"
    headers = {
        "Authorization": f"Bearer {CREATOMATE_API_KEY}",
        "Content-Type": "application/json"
    }

    # v10.0: Extraer URL directa para máxima fiabilidad
    direct_url = get_direct_video_url(f"https://www.youtube.com/watch?v={video_id}")

    def create_payload(with_subtitles=True):
        duration = float(min(analysis['end_time'] - analysis['start_time'], 58))
        
        elements = [
            # FONDO DIFUMINADO (Blurred background para formato 9:16)
            {
                "id": "background-blur",
                "type": "video",
                "source": direct_url,
                "trim_start": float(analysis['start_time']),
                "duration": duration,
                "width": 1080,
                "height": 1920,
                "x": "50%",
                "y": "50%",
                "fit": "cover",
                "volume": "0%",  
                "filters": [
                    {
                        "type": "blur",
                        "radius": "45 px"
                    },
                    {
                        "type": "brightness",
                        "level": "70%" 
                    }
                ]
            },
            # VIDEO PRINCIPAL (En caja sobre el fondo)
            {
                "id": "video-base",
                "type": "video",
                "source": direct_url,
                "trim_start": float(analysis['start_time']),
                "duration": duration,
                "width": "100%", 
                "height": "auto", 
                "x": "50%",
                "y": "50%",
                "fit": "contain", 
                "audio": True
            },
            # TÍTULO HOOK (Arriba, llamativo)
            {
                "id": "hook-text",
                "type": "text",
                "text": analysis['viral_title'].upper(),
                "width": "85%",
                "height": "auto",
                "x": "50%",
                "y": "16%",
                "text_alignment": "center",
                "y_alignment": "center",
                "font_family": "Montserrat", 
                "font_weight": "900",
                "font_size": "95 px",
                "color": "#ffffff",
                "background_color": "#e50914",
                "background_padding": "30 px 50 px",
                "background_border_radius": "20 px",
                "shadow_color": "rgba(0,0,0,0.8)",
                "shadow_blur": "25 px",
                "animations": [
                    {"type": "scale", "time": "start", "duration": "0.4 s", "easing": "elastic-out", "start_scale": "0%"},
                    {"type": "pulse", "time": "start", "duration": "loop", "interval": "2.5 s", "scale": "105%"}
                ]
            }
        ]
        
        if with_subtitles:
            elements.append({
                "type": "text",
                "text": "[transcript]",
                "transcript_source": "video-base",
                "width": "90%",
                "height": "auto",
                "x": "50%",
                "y": "82%",
                "text_alignment": "center",
                "font_family": "Montserrat", 
                "font_weight": "900",
                "font_size": "90 px",
                "text_transform": "uppercase",
                "color": "#ffff00",
                "stroke_color": "#000000",
                "stroke_width": "8 px",
                "shadow_color": "rgba(0,0,0,1)",
                "shadow_blur": "10 px",
                "shadow_y": "8 px",
                "animations": [{"type": "text-appearance", "scope": "word", "duration": "0.1 s"}]
            })
            
        return {"source": {"output_format": "mp4", "width": 1080, "height": 1920, "frame_rate": 30, "elements": elements}}

    # INTENTO 1: Con Subtítulos (Vizard Style)
    logger.info("🎬 Intento 1: Renderizado completo con subtítulos dinámicos...")
    payload_full = create_payload(True)
    
    # URL base para estado de renders (sin la colección)
    renders_status_url = "https://api.creatomate.com/v1/renders"

    # Abortar si no tenemos URL directa
    if not direct_url:
        logger.error("❌ ABORTANDO render: no se pudo obtener URL directa del vídeo.")
        return None

    try:
        res = requests.post(url, headers=headers, json=payload_full)
        logger.info(f"📡 Respuesta Creatomate (Intento 1): {res.status_code} - {res.text[:200]}")
        if res.status_code in [200, 202]:
            render_id = res.json()[0]['id']
            logger.info(f"⏳ Procesando Intento 1 ({render_id})...")
            
            # Esperar resultado
            start_poll = time.time()
            while (time.time() - start_poll) < 300: # 5 mins
                time.sleep(10)
                status_res = requests.get(f"{renders_status_url}/{render_id}", headers=headers).json()
                current_status = status_res.get('status')
                logger.info(f"   Estado render: {current_status}")
                if current_status == 'succeeded':
                    logger.info(f"✨ ¡VICTORIA! Video completo: {status_res['url']}")
                    return status_res['url']
                elif current_status == 'failed':
                    logger.warning(f"⚠️ Falló Intento 1: {status_res.get('errorMessage')}")
                    break
        else:
            logger.warning(f"⚠️ Error API en Intento 1 ({res.status_code}): {res.text[:300]}")
            
        # INTENTO 2: Fallback Básico (Solo Video, sin transcripción)
        logger.info("🔄 Intento 2: Renderizado básico de emergencia (sin subtítulos)...")
        payload_safe = create_payload(False)
        res_safe = requests.post(url, headers=headers, json=payload_safe)
        logger.info(f"📡 Respuesta Creatomate (Intento 2): {res_safe.status_code} - {res_safe.text[:200]}")
        
        if res_safe.status_code in [200, 202]:
            render_id_safe = res_safe.json()[0]['id']
            logger.info(f"⏳ Procesando Intento 2 ({render_id_safe})...")
            
            start_poll = time.time()
            while (time.time() - start_poll) < 300:
                time.sleep(10)
                status_res = requests.get(f"{renders_status_url}/{render_id_safe}", headers=headers).json()
                current_status = status_res.get('status')
                logger.info(f"   Estado render: {current_status}")
                if current_status == 'succeeded':
                    logger.info(f"✨ ¡ÉXITO (Rescate)! Video básico: {status_res['url']}")
                    return status_res['url']
                elif current_status == 'failed':
                    logger.error(f"❌ Falló hasta el intento de rescate: {status_res.get('errorMessage')}")
                    break

        return None
    except Exception as e:
        logger.error(f"❌ Error Crítico en Motor v9.0: {e}")
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
    logger.info("🎬 INICIANDO 'VIRAL CLIPPER v10.0 (DIRECT STREAM ENGINE)'...")
    
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
