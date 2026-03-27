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
# v17.0: Expandimos a canales de "Nicho" menos protegidos por YouTube
CHANNELS_TO_WATCH = [
    "Satisfying", "Minecraft", "LifeHacks", "GamingFails", "DailyDoseOfInternet",
    "MrBeast", "Markiplier", "PewDiePie", "Jacksepticeye", "Ninja",
    "Ibai Llanos", "AuronPlay", "TheGrefg", "Spreen", "DjMaRiiO"
]

# Modo: "ES" | "EN" | "BOTH"  (configurable desde secret LANG_MODE)
LANG_MODE = os.environ.get("LANG_MODE", "BOTH").upper()

# RELEVANCE_LANGUAGE is no longer directly set by LANG_MODE for CHANNELS_TO_WATCH
# as the list is now mixed. It will be inferred later if needed.
RELEVANCE_LANGUAGE = None

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
    """Busca el clip más viral (SHORTS) de los canales configurados (únicos y variados)."""
    if not youtube: return None

    # v17.0 niche focus
    channels = CHANNELS_TO_WATCH[:]
    random.shuffle(channels)
    
    logger.info(f"🔍 Buscando SHORTS virales (Pivot Nicho v17.0)...")

    for target_channel in channels:
        logger.info(f"   ▶️ Buscando en: {target_channel}")
        params = dict(
            part="snippet",
            q=f"{target_channel} shorts",
            type="video",
            videoDuration="short",
            order="viewCount",
            publishedAfter=(datetime.utcnow() - timedelta(days=14)).isoformat("T") + "Z",
            maxResults=15,
        )

        try:
            response = youtube.search().list(**params).execute()
            items = response.get("items", [])
            if not items: continue

            random.shuffle(items)
            for video in items:
                video_id = video["id"]["videoId"]
                if is_blacklisted(video_id): continue
                
                logger.info(f"✅ Short elegido: {video['snippet']['title']} (https://youtu.be/{video_id})")
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
def get_direct_video_url(youtube_url: str) -> str:
    """
    Busca un enlace directo (.mp4) usando la red Propxy (Invidious, Cobalt).
    Handles short IDs, full URLs, and Shorts URLs.
    """
    video_id = ""
    if "youtu.be/" in youtube_url:
        video_id = youtube_url.split("/")[-1].split("?")[0]
    elif "/shorts/" in youtube_url:
        video_id = youtube_url.split("/shorts/")[-1].split("?")[0]
    elif "v=" in youtube_url:
        video_id = youtube_url.split("v=")[-1].split("&")[0]
    else:
        video_id = youtube_url # Fallback: Assume it's an ID

    headers = {
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }

    # 1. COBALT API (Ultra-Robusto con Headers de Browser)
    logger.info(f"📡 Resolviendo ID '{video_id}' vía Cobalt API...")
    cob_instances = ["https://api.cobalt.tools/api/json", "https://co.wuk.sh/api/json"]
    for inst in cob_instances:
        try:
            # v17.0: Agregamos Origin y Referer para simular el Web UI de Cobalt
            cob_headers = {
                "Accept": "application/json",
                "Content-Type": "application/json",
                "Origin": "https://cobalt.tools",
                "Referer": "https://cobalt.tools/",
                "User-Agent": headers["User-Agent"]
            }
            payload = {"url": youtube_url, "videoQuality": "720"}
            r = requests.post(inst, json=payload, headers=cob_headers, timeout=12)
            if r.status_code == 200:
                url = r.json().get("url")
                if url: return url
        except: continue

    # 2. INVIDIOUS (Preferido: Bypass total)
    logger.info(f"📡 Cobalt falló. Probando ID '{video_id}' vía Invidious...")
    inv_instances = [
        "https://invidious.jing.rocks",
        "https://inv.tux.pizza",
        "https://invidious.lunar.icu",
        "https://invidious.projectsegfau.lt"
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

    logger.warning("⚠️ Sin URL directa. Usando fallback original.")
    return youtube_url


# ---------------------------------------------------------------------------
# RENDERIZAR CON CREATOMATE
# ---------------------------------------------------------------------------
def render_viral_video(clip_source_url: str, analysis: dict) -> str | None:
    """
    v17.0 NUCLEAR RENDER ENGINE
    3-tier failover: 1. Full Styles -> 2. No Subtitles -> 3. Nuclear (Video Only)
    """
    api_key = os.environ.get("CREATOMATE_API_KEY")
    api_url = "https://api.creatomate.com/v1/renders"
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

    start_time = float(analysis.get("start_time", 0))
    duration = float(analysis.get("end_time", 45)) - start_time
    duration = max(5.0, min(duration, 58.0))

    def build_payload(with_subtitles: bool) -> dict:
        elements = [
            {"type": "rect", "width": 1080, "height": 1920, "color": "#121212"},
            {
                "id": "video-base",
                "type": "video",
                "source": clip_source_url,
                "trim_start": f"{start_time} s",
                "duration": f"{duration} s",
                "width": "100%", "height": "auto", "x": "50%", "y": "50%", "fit": "contain"
            }
        ]
        if with_subtitles:
            elements.append({
                "type": "text", "text": "[transcript]", "transcript_source": "video-base",
                "width": "90%", "y": "80%", "font_family": "Montserrat", "font_weight": "900",
                "font_size": "70 px", "text_transform": "uppercase", "color": "#ffff00",
                "stroke_color": "#000000", "stroke_width": "8 px"
            })
        return {"source": {"output_format": "mp4", "width": 1080, "height": 1920, "elements": elements}}

    def poll_render(render_id: str, label: str) -> str | None:
        for _ in range(25):
            time.sleep(12)
            try:
                r = requests.get(f"{api_url}/{render_id}", headers=headers, timeout=20).json()
                status = r.get("status")
                logger.info(f"   [{label}] Estado: {status}")
                if status == "succeeded": return r.get("url")
                if status == "failed": return None
            except: continue
        return None

    # Intento 1: Completo
    logger.info("🎬 Intento 1: render completo con subtítulos...")
    try:
        res1 = requests.post(api_url, json=build_payload(True), headers=headers, timeout=30).json()
        rid1 = res1[0]["id"] if isinstance(res1, list) else res1.get("id")
        if rid1:
            url1 = poll_render(rid1, "INT1")
            if url1: return url1
    except: pass

    # Intento 2: Sin subtítulos
    logger.info("🔄 Intento 2: render básico sin subtítulos...")
    try:
        res2 = requests.post(api_url, json=build_payload(False), headers=headers, timeout=30).json()
        rid2 = res2[0]["id"] if isinstance(res2, list) else res2.get("id")
        if rid2:
            url2 = poll_render(rid2, "INT2")
            if url2: return url2
    except: pass

    # Intento 3: NUCLEAR (Mínimo absoluto)
    logger.info("☢️ Intentando render NUCLEAR (Video Only)...")
    try:
        nuclear = {
            "source": {
                "output_format": "mp4",
                "elements": [{
                    "type": "video",
                    "source": clip_source_url,
                    "duration": "45 s",
                    "fit": "contain"
                }]
            }
        }
        res3 = requests.post(api_url, json=nuclear, headers=headers, timeout=30).json()
        rid3 = res3[0]["id"] if isinstance(res3, list) else res3.get("id")
        if rid3:
            url3 = poll_render(rid3, "NUCLEAR")
            if url3: return url3
    except: pass

    return None


# ---------------------------------------------------------------------------
# SUBIR A YOUTUBE SHORTS
# ---------------------------------------------------------------------------
def upload_to_youtube_shorts(video_url: str, title: str, description: str):
    """Descarga el video renderizado y lo sube a YouTube Shorts."""
    creds = get_youtube_credentials()
    if not creds: return None
    final_file = str(BASE_DIR / "final_short.mp4")
    try:
        r = requests.get(video_url, timeout=120)
        with open(final_file, "wb") as f: f.write(r.content)
        service = build("youtube", "v3", credentials=creds)
        body = {
            "snippet": {"title": title, "description": description, "categoryId": "24"},
            "status": {"privacyStatus": "public", "selfDeclaredMadeForKids": False},
        }
        media = MediaFileUpload(final_file, chunksize=-1, resumable=True)
        request = service.videos().insert(part="snippet,status", body=body, media_body=media)
        response = request.execute()
        return response["id"]
    except Exception as e:
        logger.error(f"❌ Error subiendo: {e}")
        return None
    finally:
        if os.path.exists(final_file): os.remove(final_file)


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------
def main():
    logger.info("🎬 INICIANDO YoutYann v17.0 'HAIL MARY'")
    attempts = 0
    max_attempts = 5
    success = False

    while attempts < max_attempts and not success:
        attempts += 1
        logger.info(f"--- 🔄 CICLO {attempts}/{max_attempts} ---")
        
        video_data = search_trending_video()
        if not video_data: continue
        
        analysis = analyze_video_for_clipper(video_data)
        if not analysis: continue

        source_url = get_direct_video_url(video_data["url"])
        final_video_url = render_viral_video(source_url, analysis)

        if not final_video_url:
            save_id(FAILED_FILE, video_data["id"])
            continue

        yt_id = upload_to_youtube_shorts(final_video_url, analysis["viral_title"], analysis["viral_description"])
        if yt_id:
            save_id(PROCESSED_FILE, video_data["id"])
            logger.info(f"🎉 ÉXITO: https://youtube.com/shorts/{yt_id}")
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
