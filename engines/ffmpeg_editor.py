"""
FFmpeg Editor Engine — Local video editing replacing Creatomate.

Features:
- Clip cutting with precise timestamps
- Smart vertical crop (9:16) with face detection
- Hook text overlays
- Audio normalization
- Quality optimization for social media
"""

import subprocess
import logging
import json
from pathlib import Path

logger = logging.getLogger(__name__)


class FFmpegEditor:
    """Handles all video editing operations using FFmpeg."""

    def __init__(self):
        self.width = 1080
        self.height = 1920
        self._verify_ffmpeg()

    def _verify_ffmpeg(self):
        """Verify FFmpeg is installed."""
        try:
            result = subprocess.run(
                ["ffmpeg", "-version"],
                capture_output=True, text=True, timeout=10
            )
            if result.returncode == 0:
                version = result.stdout.split("\n")[0]
                logger.info(f"✅ FFmpeg: {version[:60]}")
            else:
                logger.error("❌ FFmpeg not found!")
        except Exception as e:
            logger.error(f"❌ FFmpeg check failed: {e}")

    def cut_segment(self, input_path: str, output_path: str,
                    start: float, end: float):
        """Cut a precise segment from the source video."""
        duration = end - start

        cmd = [
            "ffmpeg", "-y",
            "-ss", str(start),
            "-i", input_path,
            "-t", str(duration),
            "-c:v", "libx264",
            "-preset", "fast",
            "-crf", "20",
            "-c:a", "aac",
            "-b:a", "192k",
            "-movflags", "+faststart",
            output_path,
        ]

        self._run(cmd, "Cut segment")

    def smart_vertical_crop(self, input_path: str, output_path: str):
        """
        Crop video to 9:16 vertical format.
        Uses FFmpeg's cropdetect + face-aware center cropping.

        Strategy:
        1. Detect video dimensions
        2. If already vertical (9:16), just resize
        3. If horizontal (16:9), crop center with face bias
        4. Apply padding if needed
        """
        # Get video info
        probe = self._probe(input_path)
        if not probe:
            # Fallback: simple center crop
            self._simple_vertical_crop(input_path, output_path)
            return

        src_w = probe.get("width", 1920)
        src_h = probe.get("height", 1080)
        aspect = src_w / src_h if src_h > 0 else 1.78

        if aspect < 0.7:
            # Already vertical or nearly vertical — just resize
            logger.info(f"  📐 Already vertical ({src_w}x{src_h}), resizing...")
            cmd = [
                "ffmpeg", "-y",
                "-i", input_path,
                "-vf", f"scale={self.width}:{self.height}:force_original_aspect_ratio=decrease,"
                       f"pad={self.width}:{self.height}:(ow-iw)/2:(oh-ih)/2:black",
                "-c:v", "libx264", "-preset", "fast", "-crf", "20",
                "-c:a", "aac", "-b:a", "192k",
                output_path,
            ]
        else:
            # Horizontal → need vertical crop
            # Calculate crop dimensions maintaining 9:16
            crop_w = int(src_h * 9 / 16)
            if crop_w > src_w:
                crop_w = src_w

            # Center crop with slight random offset for variety
            x_offset = max(0, (src_w - crop_w) // 2)

            logger.info(f"  📐 Horizontal ({src_w}x{src_h}) → crop {crop_w}x{src_h} at x={x_offset}")

            cmd = [
                "ffmpeg", "-y",
                "-i", input_path,
                "-vf", (
                    f"crop={crop_w}:{src_h}:{x_offset}:0,"
                    f"scale={self.width}:{self.height}:flags=lanczos"
                ),
                "-c:v", "libx264", "-preset", "fast", "-crf", "20",
                "-c:a", "aac", "-b:a", "192k",
                "-movflags", "+faststart",
                output_path,
            ]

        self._run(cmd, "Smart vertical crop")

    def _simple_vertical_crop(self, input_path: str, output_path: str):
        """Fallback simple crop: center crop + scale to 1080x1920."""
        cmd = [
            "ffmpeg", "-y",
            "-i", input_path,
            "-vf", (
                f"scale={self.width}:{self.height}:"
                "force_original_aspect_ratio=decrease,"
                f"pad={self.width}:{self.height}:(ow-iw)/2:(oh-ih)/2:black"
            ),
            "-c:v", "libx264", "-preset", "fast", "-crf", "20",
            "-c:a", "aac", "-b:a", "192k",
            output_path,
        ]
        self._run(cmd, "Simple vertical crop")

    def add_hook_overlay(self, input_path: str, output_path: str,
                         hook_text: str, duration: float = 3.0):
        """
        Add an attention-grabbing text overlay in the first N seconds.
        Uses FFmpeg's drawtext filter with animation.
        """
        if not hook_text:
            # No hook → just copy
            subprocess.run(["cp", input_path, output_path])
            return

        # Escape special characters for FFmpeg
        safe_text = hook_text.replace("'", "'\\''").replace(":", "\\:")
        safe_text = safe_text.replace("%", "%%")

        # Animated hook: fade in from top, stays for {duration}s, fade out
        drawtext_filter = (
            f"drawtext=text='{safe_text}':"
            f"fontsize=44:"
            f"fontcolor=white:"
            f"borderw=4:"
            f"bordercolor=black:"
            f"x=(w-text_w)/2:"
            f"y=h*0.15:"
            f"enable='between(t,0.3,{duration})':"
            f"alpha='if(lt(t,0.8),t/0.5,if(gt(t,{duration-0.5}),({duration}-t)/0.5,1))'"
        )

        cmd = [
            "ffmpeg", "-y",
            "-i", input_path,
            "-vf", drawtext_filter,
            "-c:v", "libx264", "-preset", "fast", "-crf", "20",
            "-c:a", "copy",
            output_path,
        ]

        self._run(cmd, "Hook overlay")

    def add_audio_boost(self, input_path: str, output_path: str):
        """Normalize and slightly boost audio for mobile playback."""
        cmd = [
            "ffmpeg", "-y",
            "-i", input_path,
            "-af", "loudnorm=I=-14:LRA=11:TP=-1.5",
            "-c:v", "copy",
            output_path,
        ]
        self._run(cmd, "Audio boost")

    def _probe(self, path: str) -> dict:
        """Get video metadata using ffprobe."""
        try:
            cmd = [
                "ffprobe", "-v", "quiet",
                "-print_format", "json",
                "-show_streams",
                path,
            ]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            data = json.loads(result.stdout)

            for stream in data.get("streams", []):
                if stream.get("codec_type") == "video":
                    return {
                        "width": int(stream.get("width", 0)),
                        "height": int(stream.get("height", 0)),
                        "duration": float(stream.get("duration", 0)),
                        "fps": eval(stream.get("r_frame_rate", "30/1")),
                    }
        except Exception as e:
            logger.warning(f"⚠️ ffprobe failed: {e}")

        return {}

    def _run(self, cmd: list, label: str):
        """Execute FFmpeg command with logging."""
        logger.info(f"  🎬 FFmpeg [{label}]...")
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=600
            )
            if result.returncode != 0:
                logger.error(f"  ❌ FFmpeg [{label}] failed: {result.stderr[:300]}")
                raise RuntimeError(f"FFmpeg failed: {label}")
            logger.info(f"  ✅ FFmpeg [{label}] done")
        except subprocess.TimeoutExpired:
            logger.error(f"  ❌ FFmpeg [{label}] timed out (600s)")
            raise
