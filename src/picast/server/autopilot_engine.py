"""AI Autopilot engine for autonomous content selection.

Combines self-learning weights from the pool system with taste profile
genre weights from Opus 4.6 to score and rank videos. Maintains a
pre-filled queue of upcoming selections per block.

When a taste profile is available, scoring uses:
  ai_score = base_weight * genre_match * duration_fit * recency_penalty

When no profile (or stale), falls back to pure weighted random.
"""

import logging
import random
import threading
from datetime import datetime, timezone

from picast.config import AutopilotConfig
from picast.server.autoplay_pool import AutoPlayPool
from picast.server.taste_profile import TasteProfile

logger = logging.getLogger(__name__)


class AutopilotEngine:
    """Coordinates AI-enhanced video selection with queue management.

    The engine is a stateful coordinator called by API endpoints — it does
    not run a background thread. It maintains a pre-selected queue of videos
    for each block, refilling as needed when videos are played or skipped.
    """

    def __init__(
        self,
        pool: AutoPlayPool,
        profile: TasteProfile,
        config: AutopilotConfig,
        db,
    ):
        self._pool = pool
        self._profile = profile
        self._config = config
        self._db = db
        self._running = False
        self._current_block: str | None = None
        self._queue: list[dict] = []
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        return self._running

    @property
    def current_block(self) -> str | None:
        return self._current_block

    def start(self) -> None:
        """Enable autopilot. Loads profile if needed."""
        with self._lock:
            self._running = True
            if not self._profile.is_loaded:
                self._profile.load(self._db)
            self._log("start", reason="Autopilot enabled")
        logger.info("Autopilot started (profile loaded: %s)", self._profile.is_loaded)

    def stop(self) -> None:
        """Pause autopilot. Keeps queue intact for resume."""
        with self._lock:
            self._running = False
            self._log("stop", reason="Autopilot paused")
        logger.info("Autopilot stopped (queue preserved: %d items)", len(self._queue))

    def toggle(self) -> bool:
        """Toggle autopilot on/off. Returns new state."""
        if self._running:
            self.stop()
        else:
            self.start()
        return self._running

    def get_status(self) -> dict:
        """Return status dict for API responses."""
        with self._lock:
            stale = self._profile.is_stale(self._config.stale_threshold_hours)
            stale_reason = None
            if not self._profile.is_loaded:
                stale_reason = "no profile loaded"
            elif stale:
                stale_reason = f"profile older than {self._config.stale_threshold_hours}h"
            return {
                "enabled": self._running,
                "mode": self._config.mode,
                "current_block": self._current_block,
                "queue_depth": len(self._queue),
                "target_depth": self._config.queue_depth,
                "pool_only": self._config.pool_only,
                "stale": stale,
                "stale_reason": stale_reason,
                "stale_threshold_hours": self._config.stale_threshold_hours,
                "profile": self._profile.to_dict(),
            }

    def on_block_change(self, block_name: str) -> None:
        """Handle PiPulse block transition. Clears and refills queue."""
        with self._lock:
            old_block = self._current_block
            self._current_block = block_name
            self._queue.clear()
            self._profile.load(self._db)  # Reload in case profile was updated
            self._fill_queue(block_name)
            self._log("block_change", block_name=block_name,
                       reason=f"from {old_block}")
        logger.info(
            "Autopilot block change: %s -> %s (queue: %d)",
            old_block, block_name, len(self._queue),
        )

    def on_video_complete(self, video_id: str) -> None:
        """Video finished playing naturally. Remove from queue and refill."""
        with self._lock:
            self._queue = [v for v in self._queue if v["video_id"] != video_id]
            if self._current_block:
                self._fill_queue(self._current_block)
            self._log("video_complete", video_id=video_id,
                       block_name=self._current_block)

    def on_video_skip(self, video_id: str) -> None:
        """Video was skipped by user. Remove from queue and refill."""
        with self._lock:
            self._queue = [v for v in self._queue if v["video_id"] != video_id]
            if self._current_block:
                self._fill_queue(self._current_block)
            self._log("video_skip", video_id=video_id,
                       block_name=self._current_block)

    def select_next(self, block_name: str | None = None) -> dict | None:
        """Pop the next video from the queue.

        If the queue is empty, fills it first. Returns None if no videos
        available in the pool.
        """
        with self._lock:
            block = block_name or self._current_block
            if not block:
                return None

            if block != self._current_block:
                self._current_block = block
                self._queue.clear()

            if not self._queue:
                self._fill_queue(block)

            if not self._queue:
                return None

            selected = self._queue.pop(0)
            self._fill_queue(block)
            self._log(
                "select", video_id=selected["video_id"],
                block_name=block, score=selected.get("score"),
                source="ai" if self._profile.is_loaded
                       and not self._profile.is_stale(self._config.stale_threshold_hours)
                       else "fallback",
            )
            return selected

    def get_queue_preview(self) -> list[dict]:
        """Return a copy of the current queue for UI display."""
        with self._lock:
            return list(self._queue)

    def set_mode(self, mode: str) -> str:
        """Switch between 'single' and 'fleet' modes. Returns the new mode."""
        if mode not in ("single", "fleet"):
            raise ValueError(f"Invalid mode: {mode!r} (must be 'single' or 'fleet')")
        with self._lock:
            self._config.mode = mode
            self._log("mode_change", reason=f"switched to {mode}")
        logger.info("Autopilot mode changed to: %s", mode)
        return mode

    def reload_profile(self) -> dict | None:
        """Force-reload the taste profile from database."""
        with self._lock:
            result = self._profile.load(self._db)
            if self._running and self._current_block:
                self._queue.clear()
                self._fill_queue(self._current_block)
            self._log("profile_reload",
                       reason="profile reloaded" if result else "no profile found")
        return result

    def record_feedback(self, video_id: str, signal: str,
                        block_name: str | None = None) -> None:
        """Record a 'more like this' or 'less like this' feedback signal."""
        block = block_name or self._current_block
        self._log("feedback", video_id=video_id, block_name=block,
                  reason=signal)
        logger.info("Autopilot feedback: %s for %s in %s", signal, video_id, block)

    def get_profile_data(self) -> dict | None:
        """Return the raw taste profile dict for API responses."""
        with self._lock:
            if self._profile.is_loaded:
                return self._profile._profile
            return None

    # --- Internal Methods ---

    def _fill_queue(self, block_name: str) -> None:
        """Fill queue to target depth with scored videos."""
        needed = self._config.queue_depth - len(self._queue)
        if needed <= 0:
            return

        scored = self._score_pool(block_name)

        # Remove videos already in queue
        queued_ids = {v["video_id"] for v in self._queue}
        scored = [v for v in scored if v["video_id"] not in queued_ids]

        if not scored:
            return

        # Weighted shuffle for variety (bias toward higher scores)
        shuffled = _weighted_shuffle(scored)
        self._queue.extend(shuffled[:needed])

    def _score_pool(self, block_name: str) -> list[dict]:
        """Score all active pool videos for a block.

        Uses AI-enhanced scoring when a fresh profile is available,
        falls back to pure self-learning weights otherwise.
        """
        pool = self._pool.get_pool(block_name)
        if not pool:
            return []

        # Get recent plays to avoid
        recent = self._db.fetchall(
            "SELECT video_id FROM autoplay_history "
            "WHERE block_name = ? ORDER BY played_at DESC LIMIT ?",
            (block_name, self._pool.avoid_recent),
        )
        recent_ids = {r["video_id"] for r in recent}

        # Check if AI scoring is available
        use_ai = (
            self._profile.is_loaded
            and not self._profile.is_stale(self._config.stale_threshold_hours)
        )

        # Get AI scoring data if available
        genre_weights = self._profile.get_genre_weights() if use_ai else {}
        strategy = self._profile.get_block_strategy(block_name) if use_ai else {}
        max_duration = strategy.get("max_duration", 0)

        # Get today's plays for recency penalty
        today_ids: set[str] = set()
        if use_ai:
            today_plays = self._db.fetchall(
                "SELECT DISTINCT video_id FROM autoplay_history "
                "WHERE block_name = ? AND date(played_at) = date('now')",
                (block_name,),
            )
            today_ids = {r["video_id"] for r in today_plays}

        scored = []
        for v in pool:
            if v["video_id"] in recent_ids:
                continue

            # Base weight from self-learning (same formula as AutoPlayPool)
            if v["rating"] == 1:
                base = AutoPlayPool.WEIGHT_LIKED
            elif v["rating"] == -1:
                base = AutoPlayPool.WEIGHT_DISLIKED
            else:
                base = AutoPlayPool.WEIGHT_NEUTRAL

            skip_penalty = 0.7 ** v.get("skip_count", 0)
            completion_boost = min(1.0 + v.get("completion_count", 0) * 0.2, 2.0)
            base_weight = base * skip_penalty * completion_boost

            if use_ai:
                # Genre match: check video tags against profile weights
                video_tags = [
                    t.strip().lower()
                    for t in (v.get("tags", "") or "").split(",")
                    if t.strip()
                ]
                genre_match = 0.5  # default for untagged/unmatched
                for tag in video_tags:
                    if tag in genre_weights:
                        genre_match = max(genre_match, genre_weights[tag])

                # Duration fit
                duration = v.get("duration", 0) or 0
                if max_duration > 0 and duration > max_duration:
                    duration_fit = 0.3
                else:
                    duration_fit = 1.0

                # Recency penalty
                recency_penalty = 0.5 if v["video_id"] in today_ids else 1.0

                score = base_weight * genre_match * duration_fit * recency_penalty
            else:
                score = base_weight

            scored.append({
                "video_id": v["video_id"],
                "title": v.get("title", ""),
                "tags": v.get("tags", ""),
                "duration": v.get("duration", 0),
                "score": round(score, 4),
                "url": self._pool.video_id_to_url(v["video_id"]),
            })

        # Sort by score descending
        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored

    def _log(
        self,
        action: str,
        video_id: str | None = None,
        block_name: str | None = None,
        source: str | None = None,
        score: float | None = None,
        reason: str | None = None,
    ) -> None:
        """Log an action to the autopilot_log table."""
        try:
            self._db.execute(
                "INSERT INTO autopilot_log "
                "(action, video_id, block_name, source, score, reason) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (action, video_id, block_name, source, score, reason),
            )
            self._db.commit()
        except Exception:
            logger.debug("Failed to write autopilot log", exc_info=True)


def _weighted_shuffle(scored: list[dict]) -> list[dict]:
    """Shuffle scored videos with bias toward higher scores.

    Uses weighted random sampling without replacement to maintain
    score-based ordering while adding variety.
    """
    if len(scored) <= 1:
        return list(scored)

    result = []
    remaining = list(scored)
    while remaining:
        weights = [max(v["score"], 0.01) for v in remaining]
        idx = random.choices(range(len(remaining)), weights=weights, k=1)[0]
        result.append(remaining.pop(idx))
    return result
