"""
YoutYann Settings — Centralized configuration from env vars.
"""

import os
from pathlib import Path


class Settings:
    def __init__(self):
        # API Keys
        self.YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY")
        self.GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

        # Language mode: ES | EN | BOTH
        self.LANG_MODE = os.environ.get("LANG_MODE", "BOTH").upper()

        # Paths
        self.BASE_DIR = Path(__file__).resolve().parent.parent
        self.TEMP_DIR = self.BASE_DIR / "temp"
        self.TEMP_DIR.mkdir(exist_ok=True)

        # Pipeline config
        self.MAX_ATTEMPTS = int(os.environ.get("MAX_ATTEMPTS", "5"))
        self.SHORTS_PER_RUN = int(os.environ.get("SHORTS_PER_RUN", "3"))
        self.LOOKBACK_DAYS = int(os.environ.get("LOOKBACK_DAYS", "14"))

        # Subtitle style
        self.SUBTITLE_FONT = os.environ.get("SUBTITLE_FONT", "Montserrat-Bold")
        self.SUBTITLE_SIZE = int(os.environ.get("SUBTITLE_SIZE", "22"))
        self.SUBTITLE_COLOR = os.environ.get("SUBTITLE_COLOR", "&H00FFFFFF")  # White
        self.SUBTITLE_OUTLINE = os.environ.get("SUBTITLE_OUTLINE", "&H00000000")  # Black

        # Hook overlay
        self.HOOK_FONT_SIZE = int(os.environ.get("HOOK_FONT_SIZE", "48"))
        self.HOOK_DURATION = float(os.environ.get("HOOK_DURATION", "3.0"))

        # Channels organized by niche for targeted content
        self.CHANNELS_BY_NICHE = {
            "gaming": [
                "MrBeast Gaming", "Markiplier", "PewDiePie",
                "Jacksepticeye", "Ninja", "Ibai Llanos",
                "AuronPlay", "TheGrefg", "Spreen",
            ],
            "entertainment": [
                "MrBeast", "DailyDoseOfInternet", "5-Minute Crafts",
                "Dude Perfect", "Zach King",
            ],
            "satisfying": [
                "Satisfying", "Oddly Satisfying", "ASMR",
                "Hydraulic Press Channel",
            ],
            "sports": [
                "DjMaRiiO", "ESPN", "House of Highlights",
            ],
            "tech": [
                "Linus Tech Tips", "Marques Brownlee",
                "Unbox Therapy",
            ],
            "comedy": [
                "Shorts Comedy", "Funny Fails",
                "Try Not To Laugh",
            ],
        }

        # Video output settings
        self.OUTPUT_WIDTH = 1080
        self.OUTPUT_HEIGHT = 1920
        self.OUTPUT_FPS = 30
        self.OUTPUT_BITRATE = "4M"
