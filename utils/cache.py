"""
Cache Manager — Persistent tracking of processed/failed video IDs.

Prevents re-processing the same videos across runs.
Uses JSON files for simplicity (no DB needed).
"""

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class CacheManager:
    """Manages processed and failed video ID caches."""

    def __init__(self, base_dir: Path):
        self.processed_file = base_dir / "processed_ids.json"
        self.failed_file = base_dir / "failed_ids.json"

    def _load(self, file_path: Path) -> set:
        """Load IDs from a JSON file."""
        if file_path.exists():
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict) and "ids" in data:
                    return set(data["ids"])
                elif isinstance(data, list):
                    return set(data)
            except Exception as e:
                logger.warning(f"⚠️ Error loading {file_path.name}: {e}")
        return set()

    def _save(self, file_path: Path, video_id: str):
        """Add an ID to a JSON cache file."""
        ids = self._load(file_path)
        ids.add(video_id)

        # Keep only last 500 IDs to prevent file bloat
        if len(ids) > 500:
            ids = set(list(ids)[-500:])

        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(list(ids), f, indent=2)

    def is_processed(self, video_id: str) -> bool:
        """Check if a video has been processed or previously failed."""
        p = self._load(self.processed_file)
        f = self._load(self.failed_file)
        return video_id in p or video_id in f

    def mark_processed(self, video_id: str):
        """Mark a video as successfully processed."""
        self._save(self.processed_file, video_id)
        logger.info(f"💾 Marked as processed: {video_id}")

    def mark_failed(self, video_id: str):
        """Mark a video as failed."""
        self._save(self.failed_file, video_id)
        logger.info(f"💾 Marked as failed: {video_id}")

    def get_stats(self) -> dict:
        """Return cache statistics."""
        processed = self._load(self.processed_file)
        failed = self._load(self.failed_file)
        return {
            "processed": len(processed),
            "failed": len(failed),
            "total": len(processed) + len(failed),
        }
