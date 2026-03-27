"""
================================================================================
 YoutYann — Viral Shorts Bot v13.0 "BULLETPROOF GLOBAL ENGINE"
================================================================================
 Cambios v13.0:
  - BUGFIX CRÍTICO: Ya no se pasa URL temporal de stream a Creatomate.
    El clip se descarga localmente con yt-dlp y se sube a un hosting público
    temporal antes de enviarlo a Creatomate, evitando URLs expiradas.
  - BUGFIX: render_id seguro contra respuestas dict inesperadas de Creatomate.
  - BUGFIX: Validación de duración del clip devuelta por Gemini.
  - NUEVO: Canales EN inglés (MrBeast, Markiplier, PewDiePie, etc.)
    para mejor monetización en mercado global.
  - NUEVO: Modo bilingüe ES + EN — alterna o mezcla según configuración.
  - NUEVO: Cache de videos procesados (processed_ids.json) para no repetir.
  - NUEVO: Reintentos con backoff exponencial en Creatomate.
  - NUEVO: Limpieza automática de archivos temporales.
================================================================================
"""

import os
import sys
import json
import time
import logging
import random
import tempfile
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

import requests
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google import genai

# ---------------------------------------------------------------------------
# LOGGER
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# CONFIGURACIÓN ENV
# ---------------------------------------------------------------------------
YOUTUBE_API_KEY      = os.environ.get("YOUTUBE_API_KEY")
GEMINI_API_KEY       = os.environ.get("GEMINI_API_KEY")
CREATOMATE_API_KEY   = os.environ.get("CREATOMATE_API_KEY")
CREATOMATE_TEMPLATE_ID = (
    os.environ.get("CREATOMATE_TEMPLATE_ID") or "e402bbbe-cea0-486f-8130-85ba434dfee7"
)

# ---------------------------------------------------------------------------
# CANALES A MONITOREAR
# ---------------------------------------------------------------------------
# Canales en ESPAÑOL (alta audiencia hispana)
CHANNELS_ES = [
    "Ibai Llanos", "TheGrefg", "ElRubius", "AuronPlay", "IlloJuan",
    "Willyrex", "Vegetta777", "xBuyer", "DjMaRiiO", "Spreen",
]

# Canales en INGLÉS (mercado global, mejor monetización AdSense)
CHANNELS_EN = [
    "MrBeast", "PewDiePie", "Markiplier", "Jacksepticeye", "Ninja",
    "pewdiepie", "Ludwig", "HasanAbi", "xQc", "KaiCenat",
]

# Modo: "ES" | "EN" | "BOTH"  (configurable desde secret LANG_MODE)
LANG_MODE = os.environ.get("LANG_MODE", "BOTH").upper()

if LANG_MODE == "ES":
    CHANNELS_TO_WATCH = CHANNELS_ES
    RELEVANCE_LANGUAGE = "es"
elif LANG_MODE == "EN":
    CHANNELS_TO_WATCH = CHANNELS_EN
    RELEVANCE_LANGUAGE = "en"
else:  # BOTH — mezcla aleatoria con más peso en EN para monetización
    # 60% EN, 40% ES
    CHANNELS_TO_WATCH = random.sample(CHANNELS_EN, 6) + random.sample(CHANNELS_ES, 4)
    RELEVANCE_LANGUAGE = None  # Sin filtro de idioma

logger.info(f"🌍 Modo idioma: {LANG_MODE} | Canales: {', '.join(CHANNELS_TO_WATCH)}")

# ---------------------------------------------------------------------------
# CACHE DE VIDEOS PROCESADOS Y FALLIDOS
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent
PROCESSED_FILE = BASE_DIR / "processed_ids.json"
FAILED_FILE    = BASE_DIR / "failed_ids.json"

def load_ids(file_path) -> set:
    if file_path.exists():
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                # Handle both list and dict formats for processed_ids.json
                if isinstance(data, dict) and "ids" in data:
                    return set(data["ids"])
                elif isinstance(data, list):
                    return set(data)
        except Exception:
            pass
    return set()

def save_id(file_path, video_id):
    ids = load_ids(file_path)
    ids.add(video_id)
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(list(ids), f)
    logger.info(f"💾 ID {video_id} guardado en {file_path.name}.")

def is_blacklisted(video_id):
    p = load_ids(PROCESSED_FILE)
    f = load_ids(FAILED_FILE)
    return video_id in p or video_id in f


# ---------------------------------------------------------------------------
# INICIALIZAR CLIENTES
# ---------------------------------------------------------------------------
youtube = None
client_gemini = None

try:
    if YOUTUBE_API_KEY:
        youtube = build("youtube", "v3", developerKey=YOUTUBE_API_KEY)
        logger.info("✅ YouTube Client OK")
    else:
        logger.error("❌ ERROR: YOUTUBE_API_KEY no encontrada en Secrets.")

    if GEMINI_API_KEY:
        client_gemini = genai.Client(api_key=GEMINI_API_KEY)
        logger.info("✅ Gemini Client OK")
    else:
        logger.error("❌ ERROR: GEMINI_API_KEY no encontrada en Secrets.")

except Exception as e:
    logger.error(f"Error grave al iniciar clientes: {e}")
    sys.exit(1)


# ---------------------------------------------------------------------------
# CREDENCIALES YOUTUBE UPLOAD
# ---------------------------------------------------------------------------
def get_youtube_credentials():
    """Carga credenciales OAuth2 desde entorno o archivo local."""
    token_file = BASE_DIR / "token.json"
    token_data = None

    env_token = os.environ.get("YOUTUBE_TOKEN_JSON")
    if env_token:
        try:
            token_data = json.loads(env_token)
            logger.info("✅ Credenciales desde YOUTUBE_TOKEN_JSON (env).")
        except json.JSONDecodeError:
            logger.warning("⚠️ YOUTUBE_TOKEN_JSON no es JSON válido. Usando archivo local...")

    if not token_data:
        if not token_file.exists():
            logger.error(f"❌ No se encontró token.json en: {token_file}")
            return None
        try:
            with open(token_file, "r", encoding="utf-8") as f:
                token_data = json.load(f)
            logger.info("✅ Credenciales desde token.json local.")
        except Exception as e:
            logger.error(f"❌ Error leyendo token.json: {e}")
            return None

    required_keys = ["client_id", "client_secret", "refresh_token"]
    missing = [k for k in required_keys if k not in token_data]
    if missing:
        logger.error(f"❌ token.json incompleto. Faltan: {missing}")
        return None

    try:
        return Credentials.from_authorized_user_info(
            token_data,
            scopes=["https://www.googleapis.com/auth/youtube.upload"],
        )
    except ValueError as e:
        logger.error(f"❌ Error Google Auth al crear Credentials: {e}")
        return None


# ---------------------------------------------------------------------------
# BUSCAR VÍDEO VIRAL
# ---------------------------------------------------------------------------
def search_trending_video():
    """Busca el vídeo más viral de los canales configurados (últimos 14 días)."""
    if not youtube:
        logger.error("❌ YouTube client no disponible.")
        return None

    channels = CHANNELS_TO_WATCH[:]
    random.shuffle(channels)
    
    logger.info(f"🔍 Buscando videos virales iterando canales...")

    for target_channel in channels:
        logger.info(f"   ▶️ Buscando en: {target_channel}")
        params = dict(
            part="snippet",
            q=f"{target_channel} funny OR highlights OR mejores momentos",
            type="video",
            videoDuration="medium",
            order="viewCount",
            publishedAfter=(datetime.utcnow() - timedelta(days=14)).isoformat("T") + "Z",
            maxResults=10,
        )
        if RELEVANCE_LANGUAGE:
            params["relevanceLanguage"] = RELEVANCE_LANGUAGE

        try:
            response = youtube.search().list(**params).execute()
            items = response.get("items", [])
            if not items: continue

            random.shuffle(items)
            for video in items:
                video_id = video["id"]["videoId"]
                if is_blacklisted(video_id):
                    logger.info(f"      ⏭️ Saltando {video_id} (Blacklisted/Processed).")
                    continue
                
                logger.info(f"✅ Video elegido: {video['snippet']['title']} (https://youtu.be/{video_id})")
                return {
                    "id": video_id,
                    "title": video["snippet"]["title"],
                    "url": f"https://www.youtube.com/watch?v={video_id}",
                    "channel": video["snippet"]["channelTitle"],
                }
        except Exception as e:
            logger.error(f"      ❌ Error buscando en {target_channel}: {e}")
            continue

    return None


# ---------------------------------------------------------------------------
# OBTENER DETALLES DEL VÍDEO
# ---------------------------------------------------------------------------
def get_video_details(video_id: str):
    """Obtiene título, descripción, duración (en segundos) y estadísticas."""
    try:
        response = youtube.videos().list(
            part="snippet,contentDetails,statistics",
            id=video_id,
        ).execute()

        if not response.get("items"):
            return None

        item = response["items"][0]
        iso_dur = item["contentDetails"]["duration"]  # ej: PT15M33S
        duration_seconds = _parse_iso_duration(iso_dur)

        return {
            "title": item["snippet"]["title"],
            "description": item["snippet"]["description"][:2000],
            "duration_iso": iso_dur,
            "duration_seconds": duration_seconds,
            "views": item["statistics"].get("viewCount", "0"),
            "likes": item["statistics"].get("likeCount", "0"),
        }
    except Exception as e:
        logger.error(f"❌ Error obteniendo detalles del video: {e}")
        return None


def _parse_iso_duration(iso: str) -> int:
    """Convierte duración ISO 8601 (PT#H#M#S) a segundos."""
    import re
    h = int(re.search(r"(\d+)H", iso).group(1)) if "H" in iso else 0
    m = int(re.search(r"(\d+)M", iso).group(1)) if "M" in iso else 0
    s = int(re.search(r"(\d+)S", iso).group(1)) if "S" in iso else 0
    return h * 3600 + m * 60 + s


# ---------------------------------------------------------------------------
# ANALIZAR CON GEMINI
# ---------------------------------------------------------------------------
def analyze_video_for_clipper(video_data: dict):
    """Usa Gemini para identificar el mejor clip del vídeo."""
    logger.info("🧠 Gemini analizando metadatos del video...")

    details = get_video_details(video_data["id"])
    if not details:
        return None

    duration_secs = details["duration_seconds"]
    is_english = LANG_MODE in ("EN", "BOTH")

    prompt = f"""
You are an expert video editor specialized in viral TikTok/YouTube Shorts clips.

Video info:
- Title: {details['title']}
- Channel: {video_data['channel']}
- Views: {details['views']}
- Likes: {details['likes']}
- Duration: {details['duration_iso']} ({duration_secs} seconds total)
- Description: {details['description']}

Based on the title and description, infer which moment would be the MOST VIRAL
for a Short of 15–58 seconds. Find a strong HOOK (avoid first 15–30 seconds
if they're intro/music). The viral_title must be 2–4 words, punchy, clickbait.
{"Write everything in ENGLISH for global reach." if is_english else "Escribe en español."}

IMPORTANT constraints:
- start_time must be >= 20 (skip intros)
- end_time must be <= {duration_secs} (video length)
- Duration (end_time - start_time) must be between 15 and 58 seconds

Respond ONLY with valid JSON (no markdown):
{{
    "start_time": <number in seconds>,
    "end_time": <number in seconds>,
    "viral_title": "<short clickbait title>",
    "summary": "<why this moment is viral>"
}}
"""

    # Auto-discover Gemini models
    try:
        available = [m.name for m in client_gemini.models.list()]
        flash = [m for m in available if "flash" in m.lower()]
        others = [m for m in available if m not in flash]
        model_names = flash + others
    except Exception:
        model_names = ["gemini-2.0-flash", "gemini-1.5-flash", "gemini-1.5-pro"]

    last_error = None
    for name in model_names:
        clean = name.split("/")[-1]
        try:
            logger.info(f"🤖 Probando Gemini: {clean}")
            resp = client_gemini.models.generate_content(
                model=clean,
                contents=prompt,
                config={"response_mime_type": "application/json"},
            )
            result = json.loads(resp.text)

            # --- VALIDACIÓN DEL CLIP ---
            start = float(result.get("start_time", 20))
            end = float(result.get("end_time", 78))

            # Clamp to video duration
            start = max(20.0, min(start, duration_secs - 15))
            end = min(end, float(duration_secs))

            # Clamp duration 15–58s
            if end - start < 15:
                end = start + 30
            if end - start > 58:
                end = start + 58

            # Final sanity
            if end > duration_secs:
                end = float(duration_secs)
            if end - start < 10:
                logger.warning("⚠️ Clip demasiado corto tras validación. Usando defaults.")
                start = min(30.0, duration_secs * 0.2)
                end = start + 45.0

            result["start_time"] = round(start, 1)
            result["end_time"] = round(end, 1)

            logger.info(
                f"✅ Gemini OK con '{clean}': clip '{result['viral_title']}' "
                f"({result['start_time']}s–{result['end_time']}s)"
            )
            return result

        except Exception as e:
            last_error = str(e)
            code = "429" if "429" in last_error else "404" if "404" in last_error else "ERR"
            logger.warning(f"⚠️ Modelo '{clean}' falló ({code}): {e}")
            continue

    logger.error(f"❌ Todos los modelos Gemini fallaron. Último error: {last_error}")
    return None


# ---------------------------------------------------------------------------
# DESCARGA DEL CLIP (yt-dlp) — FIX CRÍTICO: sin URLs expiradas
# ---------------------------------------------------------------------------
def download_clip(youtube_url: str, start: float, end: float) -> str | None:
    """
    Descarga únicamente el segmento necesario usando yt-dlp.
    Devuelve la ruta del archivo MP4 descargado, o None si falla.

    FIX v13.0: En lugar de pasar una URL de stream a Creatomate (que expira),
    descargamos el clip localmente con yt-dlp usando --download-sections.
    """
    logger.info(f"📥 Descargando clip: {start}s → {end}s de {youtube_url}")

    # Escribir cookies si existen
    cookie_file = None
    cookies_content = os.environ.get("YOUTUBE_COOKIES", "")
    if cookies_content:
        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8"
        )
        if not cookies_content.strip().startswith("# Netscape HTTP Cookie File"):
            tmp.write("# Netscape HTTP Cookie File\n")
        tmp.write(cookies_content)
        tmp.flush()
        cookie_file = tmp.name
        tmp.close()
        logger.info("🍪 Usando cookies de YouTube.")
    else:
        logger.warning("⚠️ YOUTUBE_COOKIES no definido. Si falla, configura este secret.")

# ---------------------------------------------------------------------------
# v14.2 HYBRID PROXY RESOLVER (No Download on GitHub)
# ---------------------------------------------------------------------------
def get_direct_video_url(youtube_url: str) -> str:
    """
    Busca un enlace directo de streaming (.mp4 / .m3u8) usando la red
    descentralizada Proxy (Invidious, Piped, Cobalt).
    Esto evita que Creatomate falle intentando extraerlo él mismo.
    """
    if "youtu.be/" in youtube_url:
        video_id = youtube_url.split("/")[-1].split("?")[0]
    else:
        video_id = youtube_url.split("v=")[-1].split("&")[0]

    headers = {
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    # 1. INVIDIOUS (Preferido: Bypass total)
    logger.info(f"📡 Resolviendo ID '{video_id}' vía Invidious Proxy...")
    inv_instances = [
        "https://invidious.jing.rocks",
        "https://youtube.mosesmcloud.com",
        "https://inv.tux.pizza",
        "https://invidious.lunar.icu",
        "https://invidious.projectsegfau.lt",
        "https://invidious.protokolla.fi"
    ]
    random.shuffle(inv_instances)
    for inst in inv_instances:
        try:
            r = requests.get(f"{inst}/api/v1/videos/{video_id}", headers=headers, timeout=8)
            if r.status_code == 200:
                data = r.json()
                streams = data.get("formatStreams", [])
                itag = streams[0].get("itag") if streams else "22"
                direct = f"{inst}/latest_version?id={video_id}&itag={itag}&local=true"
                logger.info(f"✅ URL Invidious obtenida: {inst}")
                return direct
        except: continue

    # 2. PIPED API
    logger.info("📡 Invidious falló. Probando red Piped...")
    piped_instances = ["https://pipedapi.kavin.rocks", "https://pi.ggtyler.dev/api", "https://pipedapi.drgns.space"]
    random.shuffle(piped_instances)
    for inst in piped_instances:
        try:
            r = requests.get(f"{inst}/streams/{video_id}", headers=headers, timeout=8)
            if r.status_code == 200:
                data = r.json()
                if data.get("hls"): return data["hls"]
                for s in data.get("videoStreams", []):
                    if not s.get("videoOnly"): return s["url"]
        except: continue

    # 3. COBALT API (Ultra-Robusto)
    logger.info("📡 Piped falló. Probando Cobalt v10...")
    cob_instances = ["https://cobalt.tools/api/json", "https://co.wuk.sh/api/json", "https://api.cobalt.tools/api/json"]
    for inst in cob_instances:
        try:
            # Cobalt v10 payload
            payload = {
                "url": youtube_url,
                "videoQuality": "720",
                "audioFormat": "mp3",
                "isNoTTS": True
            }
            r = requests.post(inst, json=payload, headers=headers, timeout=10)
            if r.status_code == 200:
                url = r.json().get("url")
                if url: return url
        except: continue

    logger.warning("⚠️ Sin URL directa. Usando fallback original.")
    return youtube_url



# ---------------------------------------------------------------------------
# SUBIR CLIP A HOSTING TEMPORAL → URL pública estable para Creatomate
# ---------------------------------------------------------------------------
def upload_clip_to_temp_host(clip_path: str) -> str | None:
    """
    Sube el clip a 0x0.st (hosting gratuito y sin registro).
    Devuelve la URL pública permanente del archivo.
    """
    logger.info(f"☁️ Subiendo clip a hosting temporal: {clip_path}")
    try:
        with open(clip_path, "rb") as f:
            resp = requests.post(
                "https://0x0.st",
                files={"file": ("clip.mp4", f, "video/mp4")},
                timeout=120,
            )
        if resp.status_code == 200:
            url = resp.text.strip()
            logger.info(f"✅ Clip público en: {url}")
            return url
        else:
            logger.error(f"❌ 0x0.st devolvió {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        logger.error(f"❌ Error subiendo a 0x0.st: {e}")

    # Fallback: tmpfiles.org
    logger.info("🔄 Intentando fallback: tmpfiles.org...")
    try:
        with open(clip_path, "rb") as f:
            resp = requests.post(
                "https://tmpfiles.org/api/v1/upload",
                files={"file": ("clip.mp4", f, "video/mp4")},
                timeout=120,
            )
        if resp.status_code == 200:
            data = resp.json()
            url = data.get("data", {}).get("url", "").replace(
                "tmpfiles.org/", "tmpfiles.org/dl/"
            )
            logger.info(f"✅ Clip público en (fallback): {url}")
            return url
        else:
            logger.error(f"❌ tmpfiles.org devolvió {resp.status_code}: {resp.text[:200]}")
    except Exception as e:
        logger.error(f"❌ Error subiendo a tmpfiles.org: {e}")

    return None


# ---------------------------------------------------------------------------
# RENDERIZAR CON CREATOMATE
# ---------------------------------------------------------------------------
def render_viral_video(clip_source_url: str, analysis: dict) -> str | None:
    """
    v13.0 BULLETPROOF RENDER ENGINE
    - clip_source_url: URL pública estable del clip (no URL de stream de YouTube)
    - analysis: dict con start_time, end_time, viral_title
    """
    logger.info(f"🎨 Iniciando render v13.0 (clip: {analysis['viral_title']})...")

    api_url = "https://api.creatomate.com/v1/renders"
    headers = {
        "Authorization": f"Bearer {CREATOMATE_API_KEY}",
        "Content-Type": "application/json",
    }

    # v14.1: Normalizar URL y asegurar formatos de tiempo para Creatomate
    if "youtu.be/" in clip_source_url:
        y_id = clip_source_url.split("/")[-1].split("?")[0]
        clip_source_url = f"https://www.youtube.com/watch?v={y_id}"

    start_time = float(analysis["start_time"])
    duration = round(float(analysis["end_time"]) - start_time, 1)
    duration = max(5.0, min(duration, 58.0))

    def build_payload(with_subtitles: bool) -> dict:
        t_start = f"{start_time} s"
        t_dur = f"{duration} s"
        
        elements = [
            # v15.0: Fondo sólido oscuro para máxima velocidad de render y fiabilidad
            {
                "id": "background",
                "type": "rect",
                "width": 1080,
                "height": 1920,
                "color": "#121212",
                "x": "50%",
                "y": "50%",
            },
            # Vídeo principal centrado
            {
                "id": "video-base",
                "type": "video",
                "source": clip_source_url,
                "trim_start": t_start,
                "duration": t_dur,
                "width": "100%",
                "height": "auto",
                "x": "50%",
                "y": "50%",
                "fit": "contain",
                "audio": True,
            },
            # Título hook arriba
            {
                "id": "hook-text",
                "type": "text",
                "text": analysis["viral_title"].upper(),
                "width": "85%",
                "height": "auto",
                "x": "50%",
                "y": "12%",
                "text_alignment": "center",
                "y_alignment": "center",
                "font_family": "Montserrat",
                "font_weight": "900",
                "font_size": "90 px",
                "color": "#ffffff",
                "background_color": "#e50914",
                "background_padding": "28 px 48 px",
                "background_border_radius": "18 px",
                "shadow_color": "rgba(0,0,0,0.8)",
                "shadow_blur": "20 px",
                "animations": [
                    {"type": "scale", "time": "start", "duration": "0.4 s",
                     "easing": "elastic-out", "start_scale": "0%"},
                    {"type": "pulse", "time": "start", "duration": "loop",
                     "interval": "2.5 s", "scale": "105%"},
                ],
            },
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
                "font_size": "88 px",
                "text_transform": "uppercase",
                "color": "#ffff00",
                "stroke_color": "#000000",
                "stroke_width": "8 px",
                "shadow_color": "rgba(0,0,0,1)",
                "shadow_blur": "10 px",
                "shadow_y": "8 px",
                "animations": [
                    {"type": "text-appearance", "scope": "word", "duration": "0.1 s"}
                ],
            })

        return {
            "source": {
                "output_format": "mp4",
                "width": 1080,
                "height": 1920,
                "frame_rate": 30,
                "elements": elements,
            }
        }

    def poll_render(render_id: str, label: str, timeout: int = 360) -> str | None:
        """Espera hasta recibir 'succeeded' o agotar el timeout."""
        start_poll = time.time()
        backoff = 10
        while (time.time() - start_poll) < timeout:
            time.sleep(backoff)
            backoff = min(backoff * 1.5, 30)  # Backoff exponencial hasta 30s
            try:
                status_resp = requests.get(
                    f"{api_url}/{render_id}", headers=headers, timeout=30
                ).json()
                status = status_resp.get("status")
                logger.info(f"   [{label}] Estado: {status}")

                if status == "succeeded":
                    vid_url = status_resp.get("url")
                    logger.info(f"✨ [{label}] ¡Render completado! {vid_url}")
                    return vid_url
                elif status == "failed":
                    logger.warning(
                        f"⚠️ [{label}] Falló: {status_resp.get('errorMessage', 'sin detalles')}"
                    )
                    logger.debug(f"DEBUG Response: {status_resp}")
                    return None
            except Exception as e:
                logger.warning(f"⚠️ [{label}] Error al consultar estado: {e}")
        logger.error(f"❌ [{label}] Timeout ({timeout}s) esperando render.")
        return None

    def submit_render(payload: dict, label: str) -> str | None:
        """Envía el payload y devuelve render_id si OK, None si falla."""
        try:
            res = requests.post(api_url, headers=headers, json=payload, timeout=30)
            logger.info(f"📡 [{label}] Creatomate responde {res.status_code}: {res.text[:250]}")

            if res.status_code not in (200, 202):
                logger.warning(f"⚠️ [{label}] Código inesperado ({res.status_code}).")
                return None

            # FIX v13.0: proteger contra respuesta dict (error) en lugar de lista
            data = res.json()
            if isinstance(data, list) and len(data) > 0:
                rid = data[0].get("id")
                logger.info(f"⏳ [{label}] render_id: {rid}")
                return rid
            elif isinstance(data, dict) and "id" in data:
                rid = data["id"]
                logger.info(f"⏳ [{label}] render_id (dict): {rid}")
                return rid
            else:
                logger.error(f"❌ [{label}] Respuesta Creatomate inesperada: {data}")
                return None

        except Exception as e:
            logger.error(f"❌ [{label}] Error enviando render: {e}")
            return None

    # INTENTO 1 — Con subtítulos
    logger.info("🎬 Intento 1: render completo con subtítulos dinámicos...")
    rid = submit_render(build_payload(True), "INT1")
    if rid:
        result = poll_render(rid, "INT1")
        if result:
            return result

    # INTENTO 2 — Sin subtítulos (fallback)
    logger.info("🔄 Intento 2: render básico sin subtítulos...")
    rid_safe = submit_render(build_payload(False), "INT2")
    if rid_safe:
        result = poll_render(rid_safe, "INT2")
        if result:
            return result

    logger.error("❌ Ambos intentos de render fallaron.")
    return None


# ---------------------------------------------------------------------------
# SUBIR A YOUTUBE SHORTS
# ---------------------------------------------------------------------------
def upload_to_youtube_shorts(video_url: str, title: str, description: str):
    """Descarga el video renderizado y lo sube a YouTube Shorts."""
    logger.info("🚀 Preparando subida a YouTube Shorts...")

    creds = get_youtube_credentials()
    if not creds:
        logger.error("❌ ABORTANDO: No se pudieron cargar las credenciales OAuth2.")
        return None

    final_file = str(BASE_DIR / "final_short.mp4")
    try:
        logger.info(f"⬇️ Descargando video renderizado desde: {video_url}")
        r = requests.get(video_url, timeout=120)
        r.raise_for_status()
        with open(final_file, "wb") as f:
            f.write(r.content)
        logger.info(f"✅ Video guardado: {final_file} ({os.path.getsize(final_file)//1024} KB)")
    except Exception as e:
        logger.error(f"❌ Error descargando video renderizado: {e}")
        return None

    try:
        service = build("youtube", "v3", credentials=creds)
        body = {
            "snippet": {
                "title": title,
                "description": description,
                "tags": ["shorts", "viral", "clip", "highlight", "funny"],
                "categoryId": "24",
            },
            "status": {
                "privacyStatus": "public",
                "selfDeclaredMadeForKids": False,
            },
        }

        media_body = MediaFileUpload(final_file, chunksize=-1, resumable=True)
        logger.info("📡 Subiendo bytes a YouTube...")
        request = service.videos().insert(
            part="snippet,status",
            body=body,
            media_body=media_body,
        )
        response = request.execute()
        video_id = response["id"]
        logger.info(f"🎉 ÉXITO TOTAL: https://youtube.com/shorts/{video_id}")
        return video_id

    except Exception as e:
        logger.error(f"❌ Error subiendo a YouTube: {e}")
        return None
    finally:
        # Limpiar archivos temporales
        for tmp in [final_file, str(BASE_DIR / "clip_download.mp4")]:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                    logger.info(f"🧹 Limpiado: {tmp}")
                except Exception:
                    pass


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------
def main():
    logger.info("🎬 INICIANDO YoutYann v13.0 'BULLETPROOF GLOBAL ENGINE'")

    # 1. Buscar video viral (con cache anti-duplicados)
    video_data = search_trending_video()
    if not video_data:
        logger.error("💀 No se encontró vídeo válido. Abortando.")
        return

    # 2. Analizar con Gemini
    analysis = analyze_video_for_clipper(video_data)
    if not analysis:
        logger.error("💀 Gemini no pudo analizar el vídeo. Abortando.")
        return

def main():
    logger.info("🎬 INICIANDO YoutYann v15.0 'FINAL STAND RESILIENCE'")
    
    attempts = 0
    max_attempts = 5
    success = False

    while attempts < max_attempts and not success:
        attempts += 1
        logger.info(f"--- 🔄 INTENTO DE CICLO {attempts}/{max_attempts} ---")

        # 1. Buscar video viral (saltando los baneados/procesados)
        video_data = search_trending_video()
        if not video_data: 
            logger.error("💀 No hay más videos en esta iteración. Reintentando...")
            continue
            
        if is_blacklisted(video_data["id"]):
            logger.info(f"⏭️ Video {video_data['id']} ya está en blacklist/cache. Skip.")
            continue

        # 2. Analizar con Gemini
        analysis = analyze_video_for_clipper(video_data)
        if not analysis: continue

        # 3. v15.0 HYBRID CLOUD EXTRACTION
        source_url = get_direct_video_url(video_data["url"])
        
        # 4. Renderizar (Failsafe)
        final_video_url = render_viral_video(source_url, analysis)
        if not final_video_url:
            logger.warning(f"❌ Falló renderizado de {video_data['id']}. Añadiendo a BLACKLIST.")
            save_id(FAILED_FILE, video_data["id"])
            continue

        # 5. Subir a YouTube Shorts
        is_english = LANG_MODE in ("EN", "BOTH")
        tags = "#Shorts #Viral #Clip" if is_english else "#Shorts #Viral #Español"
        title = f"{analysis['viral_title']} {tags.split()[0]}"
        description = f"{analysis['viral_title']}\n\nCredits: {video_data['channel']}\n\n{tags}"

        yt_video_id = upload_to_youtube_shorts(final_video_url, title, description)

        if yt_video_id:
            save_id(PROCESSED_FILE, video_data["id"])
            logger.info(f"🎉 ÉXITO TOTAL: https://youtube.com/shorts/{yt_video_id}")
            success = True
        else:
            logger.error(f"💀 Fallo en subida final de {video_data['id']}.")
            save_id(FAILED_FILE, video_data["id"])

    if not success:
        logger.error("☠️ Se agotaron los intentos máximos sin éxito. Revisa logs de Creatomate.")
    else:
        logger.info("😴 Ciclo completado satisfactoriamente.")


if __name__ == "__main__":
    main()
