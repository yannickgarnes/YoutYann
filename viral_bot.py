"""
================================================================================
YoutYann v20.0 — "HISTÓRICO ENGINE"
================================================================================

REVOLUCIÓN TOTAL respecto a v17:

🎬 EDICIÓN LOCAL con FFmpeg (adiós Creatomate — gratis e ilimitado)
🎤 SUBTÍTULOS con Whisper (word-level timestamps, estilizados)
👤 SMART CROP vertical con detección de caras (OpenCV)
🖼️ THUMBNAILS automáticos (Pillow + frames del vídeo)
🪝 HOOK TEXT overlays (primeros 3 segundos)
📊 SEO ENGINE (títulos, descripciones, tags optimizados por IA)
🌍 MULTI-PLATAFORMA (YouTube Shorts + TikTok + Instagram Reels)
🔄 ORIGINALIDAD (efectos, zoom, transiciones para evitar "reused content")
📈 ANALYTICS tracker (JSON local de rendimiento)
⏰ SCHEDULING (múltiples shorts/día en horarios óptimos)
🔥 TRENDING DETECTION (Google Trends + análisis de viralidad)

Stack: FFmpeg + Whisper + OpenCV + Gemini + Pillow + yt-dlp
================================================================================
"""

import os
import sys
import json
import logging
import random
import tempfile
import subprocess
import re
from datetime import datetime, timedelta
from typing import Optional

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google import genai

# Internal modules
from engines.ffmpeg_editor import FFmpegEditor
from engines.subtitle_engine import SubtitleEngine
from engines.thumbnail_engine import ThumbnailEngine
from engines.seo_engine import SEOEngine
from engines.originality_engine import OriginalityEngine
from utils.cache import CacheManager
from utils.analytics import AnalyticsTracker
from config.settings import Settings

# ---------------------------------------------------------------------------
# LOGGER
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("youtyann.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# INIT
# ---------------------------------------------------------------------------
settings = Settings()
cache = CacheManager(settings.BASE_DIR)
analytics = AnalyticsTracker(settings.BASE_DIR)
ffmpeg = FFmpegEditor()
subtitles = SubtitleEngine()
thumbnails = ThumbnailEngine()
seo = SEOEngine(settings.GEMINI_API_KEY)
originality = OriginalityEngine()

# ---------------------------------------------------------------------------
# YOUTUBE & GEMINI CLIENTS
# ---------------------------------------------------------------------------
youtube = None
client_gemini = None

try:
    if settings.YOUTUBE_API_KEY:
        youtube = build("youtube", "v3", developerKey=settings.YOUTUBE_API_KEY)
        logger.info("✅ YouTube Data API OK")
    else:
        logger.error("❌ YOUTUBE_API_KEY missing")

    if settings.GEMINI_API_KEY:
        client_gemini = genai.Client(api_key=settings.GEMINI_API_KEY)
        logger.info("✅ Gemini Client OK")
    else:
        logger.error("❌ GEMINI_API_KEY missing")
except Exception as e:
    logger.error(f"Fatal init error: {e}")
    sys.exit(1)


# ---------------------------------------------------------------------------
# YOUTUBE CREDENTIALS (OAuth for upload)
# ---------------------------------------------------------------------------
def get_youtube_credentials() -> Optional[Credentials]:
    """Load OAuth2 credentials from env or local file."""
    token_data = None
    env_token = os.environ.get("YOUTUBE_TOKEN_JSON")

    if env_token:
        try:
            token_data = json.loads(env_token)
            logger.info("✅ Credentials from YOUTUBE_TOKEN_JSON env")
        except json.JSONDecodeError:
            logger.warning("⚠️ YOUTUBE_TOKEN_JSON invalid JSON")

    if not token_data:
        token_file = settings.BASE_DIR / "token.json"
        if not token_file.exists():
            logger.error(f"❌ token.json not found at {token_file}")
            return None
        try:
            with open(token_file, "r", encoding="utf-8") as f:
                token_data = json.load(f)
        except Exception as e:
            logger.error(f"❌ Error reading token.json: {e}")
            return None

    required = ["client_id", "client_secret", "refresh_token"]
    missing = [k for k in required if k not in token_data]
    if missing:
        logger.error(f"❌ token.json missing keys: {missing}")
        return None

    try:
        return Credentials.from_authorized_user_info(
            token_data,
            scopes=["https://www.googleapis.com/auth/youtube.upload"],
        )
    except ValueError as e:
        logger.error(f"❌ Google Auth error: {e}")
        return None


# ---------------------------------------------------------------------------
# SEARCH TRENDING VIDEO
# ---------------------------------------------------------------------------
def search_trending_video() -> Optional[dict]:
    """Find the most viral short from configured channels."""
    if not youtube:
        return None

    # Niche-focused channels organized by category for better targeting
    channels_by_niche = settings.CHANNELS_BY_NICHE
    all_channels = []
    for niche, channels in channels_by_niche.items():
        all_channels.extend([(ch, niche) for ch in channels])

    random.shuffle(all_channels)
    logger.info(f"🔍 Scanning {len(all_channels)} channels for viral shorts...")

    for target_channel, niche in all_channels:
        try:
            params = dict(
                part="snippet",
                q=f"{target_channel} shorts",
                type="video",
                videoDuration="short",
                order="viewCount",
                publishedAfter=(
                    datetime.utcnow() - timedelta(days=settings.LOOKBACK_DAYS)
                ).isoformat("T") + "Z",
                maxResults=20,
            )

            response = youtube.search().list(**params).execute()
            items = response.get("items", [])
            if not items:
                continue

            random.shuffle(items)
            for video in items:
                video_id = video["id"]["videoId"]
                if cache.is_processed(video_id):
                    continue

                title = video["snippet"]["title"]
                logger.info(
                    f"✅ Found: '{title}' from {target_channel} "
                    f"(https://youtu.be/{video_id})"
                )
                return {
                    "id": video_id,
                    "title": title,
                    "url": f"https://www.youtube.com/watch?v={video_id}",
                    "channel": video["snippet"]["channelTitle"],
                    "niche": niche,
                }
        except Exception as e:
            logger.error(f"  ❌ Error searching '{target_channel}': {e}")
            continue

    return None


# ---------------------------------------------------------------------------
# VIDEO DETAILS
# ---------------------------------------------------------------------------
def get_video_details(video_id: str) -> Optional[dict]:
    """Get title, description, duration, stats."""
    try:
        response = (
            youtube.videos()
            .list(part="snippet,contentDetails,statistics", id=video_id)
            .execute()
        )
        if not response.get("items"):
            return None

        item = response["items"][0]
        iso_dur = item["contentDetails"]["duration"]
        duration_seconds = _parse_iso_duration(iso_dur)

        return {
            "title": item["snippet"]["title"],
            "description": item["snippet"]["description"][:2000],
            "duration_iso": iso_dur,
            "duration_seconds": duration_seconds,
            "views": item["statistics"].get("viewCount", "0"),
            "likes": item["statistics"].get("likeCount", "0"),
            "tags": item["snippet"].get("tags", []),
            "language": item["snippet"].get("defaultLanguage", "en"),
        }
    except Exception as e:
        logger.error(f"❌ Error getting video details: {e}")
        return None


def _parse_iso_duration(iso: str) -> int:
    """Convert ISO 8601 duration to seconds."""
    h = int(re.search(r"(\d+)H", iso).group(1)) if "H" in iso else 0
    m = int(re.search(r"(\d+)M", iso).group(1)) if "M" in iso else 0
    s = int(re.search(r"(\d+)S", iso).group(1)) if "S" in iso else 0
    return h * 3600 + m * 60 + s


# ---------------------------------------------------------------------------
# DOWNLOAD VIDEO with yt-dlp
# ---------------------------------------------------------------------------
def download_full_video(youtube_url: str) -> Optional[str]:
    """Download the full video locally with yt-dlp for processing."""
    logger.info(f"📥 Downloading: {youtube_url}")

    output_path = str(settings.TEMP_DIR / "source_%(id)s.%(ext)s")

    cmd = [
        "yt-dlp",
        "-f", "bestvideo[height<=1080][ext=mp4]+bestaudio[ext=m4a]/best[height<=1080][ext=mp4]/best",
        "--merge-output-format", "mp4",
        "-o", output_path,
        "--no-playlist",
        "--no-check-certificates",
    ]

    # Add cookies if available
    cookies_content = os.environ.get("YOUTUBE_COOKIES", "")
    if cookies_content:
        cookie_file = tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8"
        )
        if not cookies_content.strip().startswith("# Netscape HTTP Cookie File"):
            cookie_file.write("# Netscape HTTP Cookie File\n")
        cookie_file.write(cookies_content)
        cookie_file.flush()
        cmd.extend(["--cookies", cookie_file.name])
        cookie_file.close()
        logger.info("🍪 Using YouTube cookies")

    cmd.append(youtube_url)

    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=300
        )
        if result.returncode != 0:
            logger.error(f"❌ yt-dlp error: {result.stderr[:500]}")
            return None

        # Find the downloaded file
        for f in settings.TEMP_DIR.glob("source_*"):
            logger.info(f"✅ Downloaded: {f.name}")
            return str(f)

        return None
    except Exception as e:
        logger.error(f"❌ Download failed: {e}")
        return None


# ---------------------------------------------------------------------------
# ANALYZE WITH GEMINI (v20: enhanced with transcript analysis)
# ---------------------------------------------------------------------------
def analyze_video(video_data: dict, source_path: str) -> Optional[dict]:
    """Use Gemini to identify the best viral clip + generate SEO metadata."""
    logger.info("🧠 Gemini analyzing video...")

    details = get_video_details(video_data["id"])
    if not details:
        return None

    duration_secs = details["duration_seconds"]
    is_english = settings.LANG_MODE in ("EN", "BOTH")

    # Get transcript if available (via yt-dlp subtitles or Whisper)
    transcript_text = subtitles.extract_transcript(source_path)

    prompt = f"""
You are an ELITE viral content strategist and video editor for TikTok/YouTube Shorts/Reels.

VIDEO INFO:
- Title: {details['title']}
- Channel: {video_data['channel']}
- Niche: {video_data['niche']}
- Views: {details['views']}
- Likes: {details['likes']}
- Duration: {details['duration_iso']} ({duration_secs}s total)
- Description: {details['description'][:500]}
- Tags: {', '.join(details.get('tags', [])[:10])}
{f'- Transcript excerpt: {transcript_text[:1500]}' if transcript_text else ''}

YOUR TASK:
1. Identify the SINGLE most viral-worthy moment (15-58 seconds)
2. The clip MUST have a strong HOOK in the first 2-3 seconds
3. Skip intros/outros/sponsor segments (avoid first 15-30s and last 15s)
4. Generate VIRAL metadata optimized for maximum CTR and engagement

CONSTRAINTS:
- start_time >= 15 (skip intros)
- end_time <= {duration_secs} (video length)
- Clip duration: 15-58 seconds
- viral_title: 2-5 words, MAXIMUM clickbait energy, use power words
- hook_text: 5-8 words shown in first 3 seconds (pattern interrupt)
- description: 150-300 chars, SEO-optimized with emojis and CTA
- tags: 8-12 relevant hashtags for discovery

{"Write ALL text in ENGLISH for global reach and higher CPM." if is_english else "Write in SPANISH for Hispanic market."}

Respond ONLY with valid JSON:
{{
    "start_time": <seconds>,
    "end_time": <seconds>,
    "viral_title": "<clickbait title>",
    "hook_text": "<attention-grabbing overlay text>",
    "description": "<SEO description with emojis and CTA>",
    "tags": ["#tag1", "#tag2", ...],
    "summary": "<why this moment will go viral>",
    "energy_level": "<low|medium|high|extreme>",
    "suggested_effects": ["zoom_pulse", "shake", "speed_ramp"]
}}
"""

    # Try multiple Gemini models
    try:
        available = [m.name for m in client_gemini.models.list()]
        flash = [m for m in available if "flash" in m.lower()]
        others = [m for m in available if m not in flash]
        model_names = flash + others
    except Exception:
        model_names = ["gemini-2.0-flash", "gemini-1.5-flash", "gemini-1.5-pro"]

    for name in model_names:
        clean = name.split("/")[-1]
        try:
            logger.info(f"🤖 Trying Gemini: {clean}")
            resp = client_gemini.models.generate_content(
                model=clean,
                contents=prompt,
                config={"response_mime_type": "application/json"},
            )
            result = json.loads(resp.text)

            # Validate and clamp clip times
            start = max(15.0, float(result.get("start_time", 20)))
            end = min(float(duration_secs), float(result.get("end_time", 78)))

            if end - start < 15:
                end = start + 30
            if end - start > 58:
                end = start + 58
            if end > duration_secs:
                end = float(duration_secs)
            if end - start < 10:
                start = min(30.0, duration_secs * 0.2)
                end = start + 45.0

            result["start_time"] = round(start, 1)
            result["end_time"] = round(end, 1)

            # Ensure all required fields exist
            result.setdefault("hook_text", "WAIT FOR IT... 🤯")
            result.setdefault("description", f"🔥 {result.get('viral_title', 'Epic moment')} #shorts #viral")
            result.setdefault("tags", ["#shorts", "#viral", "#trending"])
            result.setdefault("energy_level", "high")
            result.setdefault("suggested_effects", ["zoom_pulse"])

            logger.info(
                f"✅ Gemini OK: '{result['viral_title']}' "
                f"({result['start_time']}s–{result['end_time']}s) "
                f"Energy: {result['energy_level']}"
            )
            return result

        except Exception as e:
            logger.warning(f"⚠️ Model '{clean}' failed: {e}")
            continue

    logger.error("❌ All Gemini models failed")
    return None


# ---------------------------------------------------------------------------
# FULL PIPELINE: Download → Cut → Edit → Subtitle → Thumbnail → Upload
# ---------------------------------------------------------------------------
def process_video(video_data: dict) -> Optional[str]:
    """
    Complete processing pipeline:
    1. Download full video
    2. Analyze with Gemini
    3. Cut clip segment
    4. Smart vertical crop (face detection)
    5. Add originality effects (zoom, speed ramps, color grading)
    6. Generate & burn subtitles
    7. Add hook text overlay
    8. Generate thumbnail
    9. Upload to YouTube Shorts
    """
    source_path = None
    try:
        # Step 1: Download
        source_path = download_full_video(video_data["url"])
        if not source_path:
            return None

        # Step 2: Analyze
        analysis = analyze_video(video_data, source_path)
        if not analysis:
            return None

        start = analysis["start_time"]
        end = analysis["end_time"]
        duration = end - start

        # Step 3: Cut clip
        logger.info(f"✂️ Cutting clip: {start}s → {end}s ({duration:.1f}s)")
        clip_path = str(settings.TEMP_DIR / "clip.mp4")
        ffmpeg.cut_segment(source_path, clip_path, start, end)

        # Step 4: Smart vertical crop with face detection
        logger.info("👤 Smart vertical crop...")
        cropped_path = str(settings.TEMP_DIR / "cropped.mp4")
        ffmpeg.smart_vertical_crop(clip_path, cropped_path)

        # Step 5: Originality effects
        logger.info("🎨 Adding originality effects...")
        effects_path = str(settings.TEMP_DIR / "effects.mp4")
        originality.apply_effects(
            cropped_path, effects_path,
            energy=analysis.get("energy_level", "high"),
            effects=analysis.get("suggested_effects", []),
        )

        # Step 6: Generate & burn subtitles
        logger.info("📝 Generating subtitles...")
        subtitled_path = str(settings.TEMP_DIR / "subtitled.mp4")
        subtitles.burn_subtitles(effects_path, subtitled_path)

        # Step 7: Hook text overlay
        logger.info("🪝 Adding hook overlay...")
        final_path = str(settings.TEMP_DIR / "final_short.mp4")
        hook_text = analysis.get("hook_text", "")
        ffmpeg.add_hook_overlay(subtitled_path, final_path, hook_text)

        # Step 8: Generate thumbnail
        logger.info("🖼️ Generating thumbnail...")
        thumb_path = str(settings.TEMP_DIR / "thumbnail.jpg")
        thumbnails.generate(
            final_path, thumb_path,
            title=analysis["viral_title"],
            energy=analysis.get("energy_level", "high"),
        )

        # Step 9: Upload to YouTube Shorts
        logger.info("🚀 Uploading to YouTube Shorts...")
        yt_id = upload_to_youtube(
            final_path, thumb_path, analysis, video_data
        )

        if yt_id:
            # Track analytics
            analytics.log_upload(
                video_id=yt_id,
                source_id=video_data["id"],
                source_channel=video_data["channel"],
                niche=video_data["niche"],
                title=analysis["viral_title"],
                duration=duration,
            )
            return yt_id

        return None

    except Exception as e:
        logger.error(f"❌ Pipeline error: {e}", exc_info=True)
        return None
    finally:
        # Cleanup temp files
        _cleanup_temp()


# ---------------------------------------------------------------------------
# UPLOAD TO YOUTUBE SHORTS
# ---------------------------------------------------------------------------
def upload_to_youtube(
    video_path: str, thumb_path: str, analysis: dict, video_data: dict = None
) -> Optional[str]:
    """Upload the processed short to YouTube."""
    creds = get_youtube_credentials()
    if not creds:
        return None

    try:
        service = build("youtube", "v3", credentials=creds)

        title = analysis["viral_title"][:100]
        description = analysis.get("description", "")
        tags_list = [t.replace("#", "") for t in analysis.get("tags", [])]

        # Add standard tags
        tags_list.extend(["shorts", "viral", "trending"])
        tags_list = list(set(tags_list))[:30]  # YouTube max 30 tags

        body = {
            "snippet": {
                "title": title,
                "description": description,
                "tags": tags_list,
                "categoryId": "24",  # Entertainment
            },
            "status": {
                "privacyStatus": "public",
                "selfDeclaredMadeForKids": False,
                "shorts": {"shortsAutoGenerate": True},
            },
        }

        media = MediaFileUpload(
            video_path, chunksize=10 * 1024 * 1024, resumable=True
        )
        request = service.videos().insert(
            part="snippet,status", body=body, media_body=media
        )

        response = None
        while response is None:
            status, response = request.next_chunk()
            if status:
                logger.info(f"  📤 Upload progress: {int(status.progress() * 100)}%")

        yt_id = response["id"]
        logger.info(f"🎉 UPLOADED: https://youtube.com/shorts/{yt_id}")

        # Set thumbnail if possible
        try:
            if os.path.exists(thumb_path):
                service.thumbnails().set(
                    videoId=yt_id,
                    media_body=MediaFileUpload(thumb_path),
                ).execute()
                logger.info("🖼️ Custom thumbnail set!")
        except Exception as e:
            logger.warning(f"⚠️ Could not set thumbnail: {e}")

        return yt_id

    except Exception as e:
        logger.error(f"❌ Upload error: {e}")
        return None


# ---------------------------------------------------------------------------
# CLEANUP
# ---------------------------------------------------------------------------
def _cleanup_temp():
    """Remove temporary files."""
    try:
        for f in settings.TEMP_DIR.glob("*"):
            if f.is_file():
                f.unlink()
        logger.info("🧹 Temp files cleaned")
    except Exception as e:
        logger.warning(f"⚠️ Cleanup error: {e}")


# ---------------------------------------------------------------------------
# MAIN
# ---------------------------------------------------------------------------
def main():
    banner = """
    ╔══════════════════════════════════════════════════╗
    ║  🎬  YoutYann v20.0 — "HISTÓRICO ENGINE"  🎬   ║
    ║                                                  ║
    ║  FFmpeg + Whisper + OpenCV + Gemini + Pillow     ║
    ║  Full local editing • Zero API render costs      ║
    ╚══════════════════════════════════════════════════╝
    """
    logger.info(banner)
    logger.info(f"🌍 Language: {settings.LANG_MODE} | "
                f"Max attempts: {settings.MAX_ATTEMPTS} | "
                f"Shorts/run: {settings.SHORTS_PER_RUN}")

    total_success = 0

    for run in range(settings.SHORTS_PER_RUN):
        logger.info(f"\n{'='*60}")
        logger.info(f"📹 SHORT {run + 1}/{settings.SHORTS_PER_RUN}")
        logger.info(f"{'='*60}")

        attempts = 0
        success = False

        while attempts < settings.MAX_ATTEMPTS and not success:
            attempts += 1
            logger.info(f"--- 🔄 Attempt {attempts}/{settings.MAX_ATTEMPTS} ---")

            video_data = search_trending_video()
            if not video_data:
                logger.warning("No trending video found, retrying...")
                continue

            yt_id = process_video(video_data)

            if yt_id:
                cache.mark_processed(video_data["id"])
                success = True
                total_success += 1
                logger.info(f"✅ Short {run + 1} complete: https://youtube.com/shorts/{yt_id}")
            else:
                cache.mark_failed(video_data["id"])
                logger.warning(f"❌ Attempt {attempts} failed for {video_data['id']}")

        if not success:
            logger.error(f"☠️ Short {run + 1}: all {settings.MAX_ATTEMPTS} attempts exhausted")

    # Final report
    logger.info(f"\n{'='*60}")
    logger.info(f"📊 SESSION REPORT: {total_success}/{settings.SHORTS_PER_RUN} shorts uploaded")
    logger.info(f"{'='*60}")
    analytics.print_summary()


if __name__ == "__main__":
    main()
