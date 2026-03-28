"""
Thumbnail Engine — Generate eye-catching thumbnails from video frames.

Features:
- Extract best frame based on visual interest
- Add title text overlay with gradient
- Color enhancement for mobile screens
- Multiple thumbnail variants
"""

import subprocess
import logging
import os
import json

logger = logging.getLogger(__name__)

# Try importing Pillow (optional, for advanced thumbnails)
PIL_AVAILABLE = False
try:
    from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageEnhance
    PIL_AVAILABLE = True
except ImportError:
    logger.info("ℹ️ Pillow not installed — using FFmpeg-only thumbnails")


class ThumbnailEngine:
    """Generates thumbnails from video frames."""

    def generate(self, video_path: str, output_path: str,
                 title: str = "", energy: str = "high"):
        """
        Generate a thumbnail from the video.

        Strategy:
        1. Extract the most visually interesting frame (1/3 into clip)
        2. Enhance colors for mobile pop
        3. Add title text with gradient overlay
        """
        # Step 1: Extract frame at ~30% into the clip
        frame_path = output_path.replace(".jpg", "_raw.jpg")
        self._extract_best_frame(video_path, frame_path)

        if not os.path.exists(frame_path):
            logger.warning("⚠️ Could not extract frame for thumbnail")
            return

        # Step 2: If Pillow available, enhance and add text
        if PIL_AVAILABLE and title:
            try:
                self._create_enhanced_thumbnail(
                    frame_path, output_path, title, energy
                )
                os.remove(frame_path)
                return
            except Exception as e:
                logger.warning(f"⚠️ Pillow thumbnail failed: {e}")

        # Fallback: FFmpeg-only thumbnail with text
        self._create_ffmpeg_thumbnail(frame_path, output_path, title)
        if os.path.exists(frame_path) and frame_path != output_path:
            os.remove(frame_path)

    def _extract_best_frame(self, video_path: str, output_path: str):
        """Extract a visually interesting frame from the video."""
        # Get duration
        try:
            probe_cmd = [
                "ffprobe", "-v", "quiet",
                "-show_entries", "format=duration",
                "-of", "json", video_path,
            ]
            result = subprocess.run(
                probe_cmd, capture_output=True, text=True, timeout=30
            )
            data = json.loads(result.stdout)
            duration = float(data.get("format", {}).get("duration", 10))
        except Exception:
            duration = 10

        # Extract frame at 30% (usually where the action is)
        timestamp = duration * 0.3

        cmd = [
            "ffmpeg", "-y",
            "-ss", str(timestamp),
            "-i", video_path,
            "-vframes", "1",
            "-q:v", "2",
            output_path,
        ]

        try:
            subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if os.path.exists(output_path):
                logger.info(f"🖼️ Frame extracted at {timestamp:.1f}s")
        except Exception as e:
            logger.error(f"❌ Frame extraction failed: {e}")

    def _create_enhanced_thumbnail(self, frame_path: str, output_path: str,
                                    title: str, energy: str):
        """Create enhanced thumbnail with Pillow."""
        img = Image.open(frame_path)

        # Resize to 1280x720 (YouTube thumbnail standard)
        img = img.resize((1280, 720), Image.LANCZOS)

        # Enhance colors based on energy level
        enhancer = ImageEnhance.Contrast(img)
        contrast_map = {"low": 1.1, "medium": 1.2, "high": 1.3, "extreme": 1.5}
        img = enhancer.enhance(contrast_map.get(energy, 1.2))

        enhancer = ImageEnhance.Color(img)
        img = enhancer.enhance(1.3)  # Slightly more vivid

        enhancer = ImageEnhance.Brightness(img)
        img = enhancer.enhance(1.05)

        # Add dark gradient at bottom for text readability
        draw = ImageDraw.Draw(img)
        for y in range(500, 720):
            alpha = int(200 * (y - 500) / 220)
            draw.rectangle([(0, y), (1280, y + 1)], fill=(0, 0, 0, alpha))

        # Add title text
        if title:
            try:
                # Try to use a bold font
                font = ImageFont.truetype(
                    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 56
                )
            except Exception:
                font = ImageFont.load_default()

            # Text with outline for visibility
            text = title.upper()[:40]
            bbox = draw.textbbox((0, 0), text, font=font)
            text_w = bbox[2] - bbox[0]
            x = (1280 - text_w) // 2
            y = 620

            # Draw outline
            for dx in range(-3, 4):
                for dy in range(-3, 4):
                    draw.text((x + dx, y + dy), text, font=font, fill="black")
            # Draw text
            draw.text((x, y), text, font=font, fill="yellow")

        img.save(output_path, "JPEG", quality=95)
        logger.info(f"🖼️ Enhanced thumbnail saved: {output_path}")

    def _create_ffmpeg_thumbnail(self, frame_path: str, output_path: str,
                                  title: str):
        """Create thumbnail using FFmpeg only (no Pillow needed)."""
        if not title:
            # Just copy the frame
            subprocess.run(["cp", frame_path, output_path])
            return

        safe_title = title.upper()[:40].replace("'", "'\\''").replace(":", "\\:")

        cmd = [
            "ffmpeg", "-y",
            "-i", frame_path,
            "-vf", (
                f"scale=1280:720,"
                f"eq=contrast=1.2:saturation=1.3:brightness=0.05,"
                f"drawtext=text='{safe_title}':"
                f"fontsize=52:fontcolor=yellow:"
                f"borderw=4:bordercolor=black:"
                f"x=(w-text_w)/2:y=h-100"
            ),
            "-q:v", "2",
            output_path,
        ]

        try:
            subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            logger.info(f"🖼️ FFmpeg thumbnail saved: {output_path}")
        except Exception as e:
            logger.error(f"❌ FFmpeg thumbnail failed: {e}")
            subprocess.run(["cp", frame_path, output_path])
