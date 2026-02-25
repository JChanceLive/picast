"""AutoPlay pool system with weighted random selection.

Manages per-block video pools with rating-based weighting,
recent-play avoidance, and play history tracking.
"""

import logging
import random
import re
from datetime import datetime, timezone

from picast.server.database import Database

logger = logging.getLogger(__name__)

_YT_VIDEO_ID_RE = re.compile(
    r'(?:youtube\.com/watch\?v=|youtu\.be/|youtube\.com/embed/)([a-zA-Z0-9_-]{11})'
)


def extract_video_id(url: str) -> str:
    """Extract YouTube video ID from a URL. Returns empty string if not found."""
    m = _YT_VIDEO_ID_RE.search(url)
    return m.group(1) if m else ""


class AutoPlayPool:
    """Manages video pools for autoplay block transitions."""

    # Rating weights for selection probability
    WEIGHT_LIKED = 3.0
    WEIGHT_NEUTRAL = 1.0
    WEIGHT_DISLIKED = 0.1

    def __init__(self, db: Database, avoid_recent: int = 3):
        self.db = db
        self.avoid_recent = avoid_recent

    def add_video(
        self,
        block_name: str,
        url: str,
        title: str = "",
        tags: str = "",
        source: str = "manual",
    ) -> dict | None:
        """Add a video to a block's pool. Returns the row or None if duplicate."""
        video_id = extract_video_id(url)
        if not video_id:
            # Not a YouTube URL — use full URL as ID
            video_id = url

        now = datetime.now(timezone.utc).isoformat()
        try:
            self.db.execute(
                "INSERT INTO autoplay_videos "
                "(video_id, title, block_name, tags, added_date, source) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (video_id, title, block_name, tags, now, source),
            )
            self.db.commit()
            logger.info("Added video %s to pool '%s'", video_id, block_name)
            return self.get_video(block_name, video_id)
        except Exception as e:
            if "UNIQUE constraint" in str(e):
                logger.debug("Video %s already in pool '%s'", video_id, block_name)
                return None
            raise

    def remove_video(self, block_name: str, video_id: str) -> bool:
        """Retire a video (set active=0). Returns True if found."""
        row = self.db.fetchone(
            "SELECT id FROM autoplay_videos WHERE block_name = ? AND video_id = ? AND active = 1",
            (block_name, video_id),
        )
        if not row:
            return False
        self.db.execute(
            "UPDATE autoplay_videos SET active = 0 WHERE id = ?", (row["id"],)
        )
        self.db.commit()
        return True

    def restore_video(self, block_name: str, video_id: str) -> bool:
        """Restore a retired video (set active=1)."""
        row = self.db.fetchone(
            "SELECT id FROM autoplay_videos WHERE block_name = ? AND video_id = ? AND active = 0",
            (block_name, video_id),
        )
        if not row:
            return False
        self.db.execute(
            "UPDATE autoplay_videos SET active = 1 WHERE id = ?", (row["id"],)
        )
        self.db.commit()
        return True

    def rate_video(self, block_name: str, video_id: str, rating: int) -> bool:
        """Rate a video (-1, 0, or 1). Returns True if found."""
        rating = max(-1, min(1, rating))
        row = self.db.fetchone(
            "SELECT id FROM autoplay_videos WHERE block_name = ? AND video_id = ?",
            (block_name, video_id),
        )
        if not row:
            return False
        self.db.execute(
            "UPDATE autoplay_videos SET rating = ? WHERE id = ?",
            (rating, row["id"]),
        )
        self.db.commit()
        return True

    def get_video(self, block_name: str, video_id: str) -> dict | None:
        """Get a single video from the pool."""
        return self.db.fetchone(
            "SELECT * FROM autoplay_videos WHERE block_name = ? AND video_id = ?",
            (block_name, video_id),
        )

    def get_pool(self, block_name: str, include_retired: bool = False) -> list[dict]:
        """Get all videos in a block's pool."""
        if include_retired:
            return self.db.fetchall(
                "SELECT * FROM autoplay_videos WHERE block_name = ? ORDER BY added_date",
                (block_name,),
            )
        return self.db.fetchall(
            "SELECT * FROM autoplay_videos WHERE block_name = ? AND active = 1 "
            "ORDER BY added_date",
            (block_name,),
        )

    def get_all_blocks(self) -> list[dict]:
        """Get summary of all blocks with pool sizes."""
        rows = self.db.fetchall(
            "SELECT block_name, COUNT(*) as pool_size, "
            "SUM(CASE WHEN rating = 1 THEN 1 ELSE 0 END) as liked, "
            "SUM(CASE WHEN rating = -1 THEN 1 ELSE 0 END) as disliked "
            "FROM autoplay_videos WHERE active = 1 "
            "GROUP BY block_name ORDER BY block_name"
        )
        return rows

    def select_video(self, block_name: str) -> dict | None:
        """Select a random video from the pool using weighted random.

        Algorithm:
        1. Get all active videos for block
        2. Exclude videos played in last N triggers (avoid_recent)
        3. Weight by rating: liked=3x, neutral=1x, disliked=0.1x
        4. Random weighted selection
        5. Log to history, update play_count + last_played
        """
        pool = self.get_pool(block_name)
        if not pool:
            return None

        # Get recent plays to avoid
        recent = self.db.fetchall(
            "SELECT video_id FROM autoplay_history "
            "WHERE block_name = ? ORDER BY played_at DESC LIMIT ?",
            (block_name, self.avoid_recent),
        )
        recent_ids = {r["video_id"] for r in recent}

        # Filter out recently played
        candidates = [v for v in pool if v["video_id"] not in recent_ids]

        # Fall back to full pool if all were recently played
        if not candidates:
            candidates = pool

        # Weighted random selection
        weights = []
        for v in candidates:
            if v["rating"] == 1:
                weights.append(self.WEIGHT_LIKED)
            elif v["rating"] == -1:
                weights.append(self.WEIGHT_DISLIKED)
            else:
                weights.append(self.WEIGHT_NEUTRAL)

        selected = random.choices(candidates, weights=weights, k=1)[0]

        # Log play and update stats
        now = datetime.now(timezone.utc).isoformat()
        self.db.execute(
            "INSERT INTO autoplay_history (video_id, block_name, played_at) "
            "VALUES (?, ?, ?)",
            (selected["video_id"], block_name, now),
        )
        self.db.execute(
            "UPDATE autoplay_videos SET play_count = play_count + 1, last_played = ? "
            "WHERE id = ?",
            (now, selected["id"]),
        )
        self.db.commit()

        return selected

    def get_history(self, block_name: str | None = None, limit: int = 20) -> list[dict]:
        """Get play history, optionally filtered by block."""
        if block_name:
            return self.db.fetchall(
                "SELECT h.*, v.title, v.rating FROM autoplay_history h "
                "LEFT JOIN autoplay_videos v ON h.video_id = v.video_id AND h.block_name = v.block_name "
                "WHERE h.block_name = ? ORDER BY h.played_at DESC LIMIT ?",
                (block_name, limit),
            )
        return self.db.fetchall(
            "SELECT h.*, v.title, v.rating FROM autoplay_history h "
            "LEFT JOIN autoplay_videos v ON h.video_id = v.video_id AND h.block_name = v.block_name "
            "ORDER BY h.played_at DESC LIMIT ?",
            (limit,),
        )

    def get_last_played(self, block_name: str | None = None) -> dict | None:
        """Get the most recently played video."""
        if block_name:
            return self.db.fetchone(
                "SELECT h.*, v.title, v.rating FROM autoplay_history h "
                "LEFT JOIN autoplay_videos v ON h.video_id = v.video_id AND h.block_name = v.block_name "
                "WHERE h.block_name = ? ORDER BY h.played_at DESC LIMIT 1",
                (block_name,),
            )
        return self.db.fetchone(
            "SELECT h.*, v.title, v.rating FROM autoplay_history h "
            "LEFT JOIN autoplay_videos v ON h.video_id = v.video_id AND h.block_name = v.block_name "
            "ORDER BY h.played_at DESC LIMIT 1"
        )

    def seed_from_mappings(self, mappings: dict[str, str]) -> int:
        """Import legacy single-URL mappings as pool entries. Returns count added."""
        count = 0
        for block_name, url in mappings.items():
            result = self.add_video(block_name, url, source="import")
            if result:
                count += 1
        return count

    def video_id_to_url(self, video_id: str) -> str:
        """Convert a video ID back to a playable YouTube URL."""
        if video_id.startswith(("http://", "https://", "/")):
            return video_id  # Already a URL
        return f"https://www.youtube.com/watch?v={video_id}"
