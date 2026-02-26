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

    def __init__(
        self,
        db: Database,
        avoid_recent: int = 3,
        cross_block_learning: bool = True,
    ):
        self.db = db
        self.avoid_recent = avoid_recent
        self.cross_block_learning = cross_block_learning

    # Auto-shelve threshold: videos skipped this many times get deactivated
    AUTO_SHELVE_SKIP_COUNT = 5

    def add_video(
        self,
        block_name: str,
        url: str,
        title: str = "",
        tags: str = "",
        source: str = "manual",
        duration: int = 0,
    ) -> dict | None:
        """Add a video to a block's pool. Returns the row or None if duplicate."""
        video_id = extract_video_id(url)
        if not video_id:
            # Not a YouTube URL — use full URL as ID
            video_id = url

        now = datetime.now(timezone.utc).isoformat()
        cursor = self.db.execute(
            "INSERT OR IGNORE INTO autoplay_videos "
            "(video_id, title, block_name, tags, added_date, source, duration) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (video_id, title, block_name, tags, now, source, duration),
        )
        self.db.commit()
        if cursor.rowcount == 0:
            logger.debug("Video %s already in pool '%s'", video_id, block_name)
            return None
        logger.info("Added video %s to pool '%s'", video_id, block_name)
        return self.get_video(block_name, video_id)

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
        # Emit cross-block signal for liked videos
        if rating == 1 and self.cross_block_learning:
            self._emit_cross_block_signal(video_id, block_name, "liked", 1.5)
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

        # Weighted random selection with self-learning modifiers
        weights = []
        for v in candidates:
            if v["rating"] == 1:
                base = self.WEIGHT_LIKED
            elif v["rating"] == -1:
                base = self.WEIGHT_DISLIKED
            else:
                base = self.WEIGHT_NEUTRAL
            # Skip penalty: each skip reduces weight by 30%
            skip_penalty = 0.7 ** v.get("skip_count", 0)
            # Completion boost: each completion adds 20%, capped at 2x
            completion_boost = min(1.0 + v.get("completion_count", 0) * 0.2, 2.0)
            weights.append(base * skip_penalty * completion_boost)

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

    def record_completion(self, block_name: str, video_id: str) -> bool:
        """Increment completion_count for a video. Returns True if found."""
        row = self.db.fetchone(
            "SELECT id, completion_count FROM autoplay_videos "
            "WHERE block_name = ? AND video_id = ?",
            (block_name, video_id),
        )
        if not row:
            return False
        new_count = row["completion_count"] + 1
        self.db.execute(
            "UPDATE autoplay_videos SET completion_count = ? WHERE id = ?",
            (new_count, row["id"]),
        )
        self.db.commit()
        logger.debug("Recorded completion for %s in %s (count=%d)", video_id, block_name, new_count)
        # Emit cross-block signal at 5 completions
        if new_count == 5 and self.cross_block_learning:
            self._emit_cross_block_signal(video_id, block_name, "completed_5x", 1.0)
        return True

    def record_skip(self, block_name: str, video_id: str) -> int:
        """Increment skip_count for a video. Auto-shelves at threshold.

        Returns new skip_count, or -1 if not found.
        """
        row = self.db.fetchone(
            "SELECT id, skip_count FROM autoplay_videos "
            "WHERE block_name = ? AND video_id = ?",
            (block_name, video_id),
        )
        if not row:
            return -1
        new_count = row["skip_count"] + 1
        self.db.execute(
            "UPDATE autoplay_videos SET skip_count = ? WHERE id = ?",
            (new_count, row["id"]),
        )
        self.db.commit()
        logger.debug("Recorded skip for %s in %s (count=%d)", video_id, block_name, new_count)
        # Auto-shelve if threshold reached
        if new_count >= self.AUTO_SHELVE_SKIP_COUNT:
            self.remove_video(block_name, video_id)
            logger.info("Auto-shelved %s in %s after %d skips", video_id, block_name, new_count)
        return new_count

    def update_last_history(
        self,
        video_id: str,
        block_name: str,
        duration_watched: int = 0,
        completed: int = 0,
        stop_reason: str = "",
    ) -> bool:
        """Update the most recent history row for a video with completion data."""
        row = self.db.fetchone(
            "SELECT id FROM autoplay_history "
            "WHERE video_id = ? AND block_name = ? ORDER BY played_at DESC LIMIT 1",
            (video_id, block_name),
        )
        if not row:
            return False
        self.db.execute(
            "UPDATE autoplay_history SET duration_watched = ?, completed = ?, "
            "stop_reason = ? WHERE id = ?",
            (duration_watched, completed, stop_reason, row["id"]),
        )
        self.db.commit()
        return True

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

    # --- Seasonal Tag CRUD ---

    def set_seasonal_tags(self, video_id: str, seasons: list[str]):
        """Set seasonal tags for a video (replaces existing)."""
        self.db.execute(
            "DELETE FROM autoplay_seasonal_tags WHERE video_id = ?", (video_id,)
        )
        for season in seasons:
            season = season.lower().strip()
            if season:
                self.db.execute(
                    "INSERT OR IGNORE INTO autoplay_seasonal_tags (video_id, season) "
                    "VALUES (?, ?)",
                    (video_id, season),
                )
        self.db.commit()

    def get_seasonal_tags(self, video_id: str) -> list[str]:
        """Get seasonal tags for a video."""
        rows = self.db.fetchall(
            "SELECT season FROM autoplay_seasonal_tags WHERE video_id = ? ORDER BY season",
            (video_id,),
        )
        return [r["season"] for r in rows]

    def remove_seasonal_tag(self, video_id: str, season: str) -> bool:
        """Remove a single seasonal tag. Returns True if found."""
        cursor = self.db.execute(
            "DELETE FROM autoplay_seasonal_tags WHERE video_id = ? AND season = ?",
            (video_id, season.lower().strip()),
        )
        self.db.commit()
        return cursor.rowcount > 0

    def get_all_seasons(self) -> list[dict]:
        """Get all season names with video counts."""
        return self.db.fetchall(
            "SELECT season, COUNT(*) as video_count "
            "FROM autoplay_seasonal_tags GROUP BY season ORDER BY season"
        )

    # --- Cross-Block Learning ---

    def _emit_cross_block_signal(
        self, video_id: str, source_block: str, signal_type: str, signal_strength: float = 1.0,
    ):
        """Record a cross-block preference signal for a video.

        Signals indicate a video is well-received in one block,
        making it a candidate for suggestion in other blocks.
        """
        now = datetime.now(timezone.utc).isoformat()
        self.db.execute(
            "INSERT OR REPLACE INTO autoplay_cross_block_prefs "
            "(video_id, source_block, signal_type, signal_strength, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (video_id, source_block, signal_type, signal_strength, now),
        )
        self.db.commit()
        logger.debug(
            "Cross-block signal: %s in %s -> %s (%.1f)",
            video_id, source_block, signal_type, signal_strength,
        )

    def get_cross_block_suggestions(self, target_block: str, limit: int = 10) -> list[dict]:
        """Get videos that are well-received in other blocks but not in target.

        Returns videos sorted by aggregate signal strength (strongest first).
        Only suggests videos that exist in at least one OTHER active block.
        """
        rows = self.db.fetchall(
            "SELECT p.video_id, p.source_block, "
            "  SUM(p.signal_strength) as total_strength, "
            "  v.title "
            "FROM autoplay_cross_block_prefs p "
            "JOIN autoplay_videos v ON p.video_id = v.video_id AND p.source_block = v.block_name "
            "WHERE p.source_block != ? "
            "  AND v.active = 1 "
            "  AND p.video_id NOT IN ("
            "    SELECT video_id FROM autoplay_videos "
            "    WHERE block_name = ? AND active = 1"
            "  ) "
            "GROUP BY p.video_id "
            "ORDER BY total_strength DESC "
            "LIMIT ?",
            (target_block, target_block, limit),
        )
        return rows

    def get_cross_block_signals(self, video_id: str) -> list[dict]:
        """Get all cross-block signals for a video."""
        return self.db.fetchall(
            "SELECT * FROM autoplay_cross_block_prefs "
            "WHERE video_id = ? ORDER BY signal_strength DESC",
            (video_id,),
        )

    # --- Pool Export / Import ---

    def export_pools(self) -> dict:
        """Export all pools with ratings and seasonal tags.

        Excludes ephemeral state (skip_count, completion_count, play_count,
        last_played) — those are learned and device-specific.
        """
        blocks = self.get_all_blocks()
        result = {"blocks": {}}
        for block_info in blocks:
            block_name = block_info["block_name"]
            videos = self.get_pool(block_name, include_retired=True)
            block_data = []
            for v in videos:
                entry = {
                    "video_id": v["video_id"],
                    "title": v["title"],
                    "tags": v["tags"],
                    "rating": v["rating"],
                    "source": v["source"],
                    "active": bool(v["active"]),
                }
                seasons = self.get_seasonal_tags(v["video_id"])
                if seasons:
                    entry["seasons"] = seasons
                block_data.append(entry)
            result["blocks"][block_name] = block_data
        return result

    def import_pools(self, data: dict, merge: bool = True) -> dict:
        """Import pools from parsed data.

        Args:
            data: Dict with 'blocks' key mapping block names to video lists.
            merge: If True, adds new videos without removing existing.
                   If False, deactivates existing pool first.

        Returns:
            Stats dict with added/skipped/blocks counts.
        """
        blocks_data = data.get("blocks", {})
        stats = {"added": 0, "skipped": 0, "blocks": 0}

        for block_name, videos in blocks_data.items():
            stats["blocks"] += 1

            if not merge:
                # Deactivate all existing videos in this block
                existing = self.get_pool(block_name)
                for v in existing:
                    self.remove_video(block_name, v["video_id"])

            for v in videos:
                video_id = v.get("video_id", "")
                if not video_id:
                    continue
                url = self.video_id_to_url(video_id)
                result = self.add_video(
                    block_name, url,
                    title=v.get("title", ""),
                    tags=v.get("tags", ""),
                    source=v.get("source", "import"),
                )
                if result:
                    stats["added"] += 1
                    # Apply rating if specified
                    rating = v.get("rating", 0)
                    if rating != 0:
                        self.rate_video(block_name, video_id, rating)
                    # Apply active state
                    if not v.get("active", True):
                        self.remove_video(block_name, video_id)
                    # Apply seasonal tags
                    seasons = v.get("seasons", [])
                    if seasons:
                        self.set_seasonal_tags(video_id, seasons)
                else:
                    stats["skipped"] += 1

        return stats
