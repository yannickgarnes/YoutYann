"""
Analytics Tracker — Logs upload performance for optimization.

Tracks:
- Upload history with metadata
- Success/failure rates
- Best performing niches
- Time-based patterns
"""

import json
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


class AnalyticsTracker:
    """Tracks and analyzes upload performance."""

    def __init__(self, base_dir: Path):
        self.analytics_file = base_dir / "analytics.json"
        self._ensure_file()

    def _ensure_file(self):
        """Create analytics file if it doesn't exist."""
        if not self.analytics_file.exists():
            with open(self.analytics_file, "w", encoding="utf-8") as f:
                json.dump({"uploads": [], "sessions": []}, f, indent=2)

    def _load(self) -> dict:
        """Load analytics data."""
        try:
            with open(self.analytics_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {"uploads": [], "sessions": []}

    def _save(self, data: dict):
        """Save analytics data."""
        # Keep only last 200 entries to prevent bloat
        if len(data.get("uploads", [])) > 200:
            data["uploads"] = data["uploads"][-200:]
        if len(data.get("sessions", [])) > 50:
            data["sessions"] = data["sessions"][-50:]

        with open(self.analytics_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)

    def log_upload(self, video_id: str, source_id: str,
                   source_channel: str, niche: str,
                   title: str, duration: float):
        """Log a successful upload."""
        data = self._load()
        data["uploads"].append({
            "video_id": video_id,
            "source_id": source_id,
            "source_channel": source_channel,
            "niche": niche,
            "title": title,
            "duration": round(duration, 1),
            "timestamp": datetime.utcnow().isoformat(),
        })
        self._save(data)
        logger.info(f"📊 Analytics: logged upload {video_id}")

    def log_session(self, success_count: int, total_attempts: int):
        """Log a session summary."""
        data = self._load()
        data["sessions"].append({
            "success": success_count,
            "attempts": total_attempts,
            "rate": round(success_count / max(total_attempts, 1) * 100, 1),
            "timestamp": datetime.utcnow().isoformat(),
        })
        self._save(data)

    def print_summary(self):
        """Print analytics summary to logger."""
        data = self._load()
        uploads = data.get("uploads", [])

        if not uploads:
            logger.info("📊 No uploads yet")
            return

        # Count by niche
        niche_counts = {}
        for u in uploads:
            n = u.get("niche", "unknown")
            niche_counts[n] = niche_counts.get(n, 0) + 1

        # Recent uploads
        recent = uploads[-10:]

        logger.info(f"📊 Total uploads: {len(uploads)}")
        logger.info(f"📊 By niche: {niche_counts}")
        logger.info(f"📊 Last 5 uploads:")
        for u in recent[-5:]:
            logger.info(
                f"   • [{u.get('niche', '?')}] {u.get('title', '?')} "
                f"({u.get('duration', 0)}s) — {u.get('timestamp', '?')[:10]}"
            )

    def get_best_niche(self) -> str:
        """Return the niche with most successful uploads."""
        data = self._load()
        niche_counts = {}
        for u in data.get("uploads", []):
            n = u.get("niche", "entertainment")
            niche_counts[n] = niche_counts.get(n, 0) + 1

        if niche_counts:
            return max(niche_counts, key=niche_counts.get)
        return "entertainment"
