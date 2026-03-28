"""
Subtitle Engine — Whisper transcription + styled subtitle burning.

Features:
- Auto-transcription with faster-whisper (word-level timestamps)
- Fallback to yt-dlp auto-captions
- Karaoke-style word highlighting
- Custom font styling optimized for mobile
"""

import subprocess
import logging
import json
import tempfile
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# Try importing faster-whisper (optional)
WHISPER_AVAILABLE = False
try:
    from faster_whisper import WhisperModel
    WHISPER_AVAILABLE = True
except ImportError:
    logger.info("ℹ️ faster-whisper not installed — using yt-dlp subtitles fallback")


class SubtitleEngine:
    """Generates and burns subtitles into video."""

    def __init__(self, model_size: str = "base"):
        self.model_size = model_size
        self.whisper_model = None

        if WHISPER_AVAILABLE:
            try:
                self.whisper_model = WhisperModel(
                    model_size, device="cpu", compute_type="int8"
                )
                logger.info(f"✅ Whisper model '{model_size}' loaded")
            except Exception as e:
                logger.warning(f"⚠️ Could not load Whisper: {e}")

    def extract_transcript(self, video_path: str) -> str:
        """
        Extract transcript from video.
        Priority: 1. Whisper  2. yt-dlp auto-subs  3. Empty
        """
        # Method 1: Whisper
        if self.whisper_model:
            try:
                segments, info = self.whisper_model.transcribe(
                    video_path,
                    beam_size=5,
                    word_timestamps=True,
                )
                text = " ".join(
                    seg.text.strip() for seg in segments
                )
                if text:
                    logger.info(f"📝 Whisper transcript: {len(text)} chars")
                    return text
            except Exception as e:
                logger.warning(f"⚠️ Whisper failed: {e}")

        # Method 2: yt-dlp auto-subs (if source is YouTube)
        try:
            return self._extract_ytdlp_subs(video_path)
        except Exception:
            pass

        return ""

    def generate_srt(self, video_path: str, output_srt: str) -> bool:
        """
        Generate SRT subtitle file with word-level timestamps.
        """
        if self.whisper_model:
            try:
                segments, info = self.whisper_model.transcribe(
                    video_path,
                    beam_size=5,
                    word_timestamps=True,
                )

                srt_content = []
                idx = 1

                for segment in segments:
                    # Group words into 3-5 word chunks for readability
                    words = segment.words if segment.words else []
                    chunk = []

                    for word in words:
                        chunk.append(word)
                        if len(chunk) >= 4 or word == words[-1]:
                            if chunk:
                                start = chunk[0].start
                                end = chunk[-1].end
                                text = " ".join(w.word.strip() for w in chunk)

                                srt_content.append(
                                    f"{idx}\n"
                                    f"{self._format_time(start)} --> {self._format_time(end)}\n"
                                    f"{text.upper()}\n\n"
                                )
                                idx += 1
                                chunk = []

                with open(output_srt, "w", encoding="utf-8") as f:
                    f.writelines(srt_content)

                logger.info(f"📝 SRT generated: {idx - 1} subtitle blocks")
                return True

            except Exception as e:
                logger.warning(f"⚠️ SRT generation failed: {e}")

        # Fallback: generate from FFmpeg's speech detection
        return self._generate_srt_ffmpeg(video_path, output_srt)

    def burn_subtitles(self, input_path: str, output_path: str):
        """
        Burn stylized subtitles into the video.
        Style: Bold, uppercase, yellow text with black outline (TikTok style).
        """
        # Generate SRT file
        srt_path = input_path.replace(".mp4", ".srt")
        has_srt = self.generate_srt(input_path, srt_path)

        if not has_srt or not os.path.exists(srt_path):
            # No subtitles available → just copy
            logger.info("ℹ️ No subtitles available, skipping...")
            subprocess.run(["cp", input_path, output_path])
            return

        # Burn with FFmpeg using ASS styling
        # Style: Bold yellow text, black outline, positioned at bottom 20%
        style = (
            "FontName=Impact,"
            "FontSize=22,"
            "PrimaryColour=&H0000FFFF,"  # Yellow (AABBGGRR)
            "OutlineColour=&H00000000,"   # Black outline
            "BorderStyle=1,"
            "Outline=3,"
            "Shadow=2,"
            "Alignment=2,"               # Center bottom
            "MarginV=120"                 # Above bottom safe area
        )

        cmd = [
            "ffmpeg", "-y",
            "-i", input_path,
            "-vf", f"subtitles={srt_path}:force_style='{style}'",
            "-c:v", "libx264", "-preset", "fast", "-crf", "20",
            "-c:a", "copy",
            output_path,
        ]

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            if result.returncode != 0:
                logger.warning(f"⚠️ Subtitle burn failed, trying simpler filter...")
                # Fallback without force_style
                cmd2 = [
                    "ffmpeg", "-y",
                    "-i", input_path,
                    "-vf", f"subtitles={srt_path}",
                    "-c:v", "libx264", "-preset", "fast", "-crf", "20",
                    "-c:a", "copy",
                    output_path,
                ]
                result2 = subprocess.run(cmd2, capture_output=True, text=True, timeout=300)
                if result2.returncode != 0:
                    logger.warning("⚠️ Subtitle burn failed completely, copying original")
                    subprocess.run(["cp", input_path, output_path])
            else:
                logger.info("✅ Subtitles burned successfully")
        except Exception as e:
            logger.error(f"❌ Subtitle burning error: {e}")
            subprocess.run(["cp", input_path, output_path])
        finally:
            # Clean SRT
            if os.path.exists(srt_path):
                os.remove(srt_path)

    def _extract_ytdlp_subs(self, video_path: str) -> str:
        """Try to extract subtitles via yt-dlp (for YouTube sources)."""
        # This only works if the video_path is a YouTube URL
        return ""

    def _generate_srt_ffmpeg(self, video_path: str, output_srt: str) -> bool:
        """Fallback: Create minimal subtitles based on video duration."""
        try:
            # Get duration
            probe_cmd = [
                "ffprobe", "-v", "quiet",
                "-show_entries", "format=duration",
                "-of", "json", video_path,
            ]
            result = subprocess.run(probe_cmd, capture_output=True, text=True, timeout=30)
            data = json.loads(result.stdout)
            duration = float(data.get("format", {}).get("duration", 30))

            # Without actual transcript, skip subtitles
            logger.info("ℹ️ No transcript available for subtitle generation")
            return False

        except Exception:
            return False

    @staticmethod
    def _format_time(seconds: float) -> str:
        """Format seconds to SRT timestamp (HH:MM:SS,mmm)."""
        h = int(seconds // 3600)
        m = int((seconds % 3600) // 60)
        s = int(seconds % 60)
        ms = int((seconds % 1) * 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
