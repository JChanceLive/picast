"""AI Autopilot engine for autonomous content selection (v2 — freeform).

Combines self-learning weights from the pool system with taste profile
energy profiles from Opus 4.6 to score and rank videos. Maintains a
pre-filled queue of upcoming selections per mood.

v2 changes: scores ALL pools (not per-block), uses mood-based energy
profiles instead of block strategies, supports creator affinity and
avoid patterns.

When a taste profile is available, scoring uses:
  ai_score = base_weight * genre_match * duration_fit * creator_boost * recency_penalty

When no profile (or stale), falls back to pure weighted random.
"""

import logging
import random
import threading
from datetime import datetime, timezone

from picast.config import AutopilotConfig
from picast.server.autoplay_pool import AutoPlayPool
from picast.server.autopilot_fleet import FleetManager, select_for_fleet
from picast.server.taste_profile import TasteProfile
from picast.server.youtube_discovery import DiscoveryAgent

logger = logging.getLogger(__name__)


class AutopilotEngine:
    """Coordinates AI-enhanced video selection with queue management.

    The engine is a stateful coordinator called by API endpoints — it does
    not run a background thread. It maintains a pre-selected queue of videos
    for the current mood, refilling as needed when videos are played or skipped.

    v2: Freeform mode — scores ALL pools regardless of block assignment,
    uses mood (chill/focus/vibes) instead of TIM block names.
    """

    def __init__(
        self,
        pool: AutoPlayPool,
        profile: TasteProfile,
        config: AutopilotConfig,
        db,
        discovery: DiscoveryAgent | None = None,
        fleet: FleetManager | None = None,
    ):
        self._pool = pool
        self._profile = profile
        self._config = config
        self._db = db
        self._discovery = discovery
        self._fleet = fleet
        self._running = False
        self._current_mood: str | None = None
        self._queue: list[dict] = []
        self._lock = threading.Lock()

    @property
    def running(self) -> bool:
        return self._running

    @property
    def current_mood(self) -> str | None:
        return self._current_mood

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
            status = {
                "enabled": self._running,
                "mode": self._config.mode,
                "current_mood": self._current_mood,
                "queue_depth": len(self._queue),
                "target_depth": self._config.queue_depth,
                "pool_only": self._config.pool_only,
                "discovery_ratio": self._config.discovery_ratio,
                "stale": stale,
                "stale_reason": stale_reason,
                "stale_threshold_hours": self._config.stale_threshold_hours,
                "profile": self._profile.to_dict(),
            }
            if self._fleet is not None:
                status["fleet_devices"] = len(self._fleet.device_ids)
            return status

    def on_mood_change(self, mood: str) -> None:
        """Handle mood change. Clears and refills queue for new mood."""
        with self._lock:
            old_mood = self._current_mood
            self._current_mood = mood
            self._queue.clear()
            self._profile.load(self._db)  # Reload in case profile was updated
            self._fill_queue(mood)
            self._log("mood_change", mood=mood,
                       reason=f"from {old_mood}")
        logger.info(
            "Autopilot mood change: %s -> %s (queue: %d)",
            old_mood, mood, len(self._queue),
        )

    def on_block_change(self, block_name: str) -> None:
        """Handle PiPulse block transition (backward compat).

        Maps block names to moods and delegates to on_mood_change.
        """
        mood = _block_to_mood(block_name)
        self.on_mood_change(mood)

    def on_video_complete(self, video_id: str) -> None:
        """Video finished playing naturally. Remove from queue and refill."""
        with self._lock:
            self._queue = [v for v in self._queue if v["video_id"] != video_id]
            if self._current_mood:
                self._fill_queue(self._current_mood)
            self._log("video_complete", video_id=video_id,
                       mood=self._current_mood)

    def on_video_skip(self, video_id: str) -> None:
        """Video was skipped by user. Remove from queue and refill."""
        with self._lock:
            self._queue = [v for v in self._queue if v["video_id"] != video_id]
            if self._current_mood:
                self._fill_queue(self._current_mood)
            self._log("video_skip", video_id=video_id,
                       mood=self._current_mood)

    def select_next(self, mood: str | None = None) -> dict | None:
        """Pop the next video from the queue.

        If the queue is empty, fills it first. Returns None if no videos
        available in the library.
        """
        with self._lock:
            target_mood = mood or self._current_mood
            if not target_mood:
                return None

            if target_mood != self._current_mood:
                self._current_mood = target_mood
                self._queue.clear()

            if not self._queue:
                self._fill_queue(target_mood)

            if not self._queue:
                return None

            selected = self._queue.pop(0)
            self._fill_queue(target_mood)
            self._log(
                "select", video_id=selected["video_id"],
                mood=target_mood, score=selected.get("score"),
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
            if self._running and self._current_mood:
                self._queue.clear()
                self._fill_queue(self._current_mood)
            self._log("profile_reload",
                       reason="profile reloaded" if result else "no profile found")
        return result

    def record_feedback(self, video_id: str, signal: str,
                        mood: str | None = None) -> None:
        """Record a 'more like this' or 'less like this' feedback signal."""
        m = mood or self._current_mood
        self._log("feedback", video_id=video_id, mood=m,
                  reason=signal)
        logger.info("Autopilot feedback: %s for %s in mood %s", signal, video_id, m)

    def get_profile_data(self) -> dict | None:
        """Return the raw taste profile dict for API responses."""
        with self._lock:
            if self._profile.is_loaded:
                return self._profile._profile
            return None

    # --- Fleet Mode ---

    @property
    def fleet(self) -> FleetManager | None:
        return self._fleet

    def select_next_fleet(self) -> list[dict]:
        """Run fleet content distribution cycle.

        Polls fleet devices, selects content for each idle device based
        on its mood, and pushes it. Returns list of push results.

        Only works when mode is 'fleet' and a FleetManager is configured.
        """
        if self._config.mode != "fleet" or self._fleet is None:
            return []

        if not self._running:
            return []

        # Poll device status first
        self._fleet.poll_devices()

        # Route content to idle devices
        results = select_for_fleet(
            self._fleet, self, self._profile, self._config,
        )

        for r in results:
            self._log(
                "fleet_push",
                video_id=r["video"].get("video_id") if r["video"] else None,
                mood=None,
                source="fleet",
                reason=f"device={r['device_id']} success={r['success']}",
            )

        return results

    def get_fleet_status(self) -> list[dict] | None:
        """Return fleet device status for API. None if no fleet manager."""
        if self._fleet is None:
            return None
        return self._fleet.get_fleet_status()

    # --- Internal Methods ---

    def _fill_queue(self, mood: str) -> None:
        """Fill queue to target depth with scored library + discovery videos."""
        needed = self._config.queue_depth - len(self._queue)
        if needed <= 0:
            return

        queued_ids = {v["video_id"] for v in self._queue}

        # Determine discovery vs pool split
        use_discovery = (
            self._discovery is not None
            and not self._config.pool_only
            and self._config.discovery_ratio > 0
            and self._profile.is_loaded
            and not self._profile.is_stale(self._config.stale_threshold_hours)
        )

        discovery_slots = 0
        if use_discovery:
            discovery_slots = max(1, round(needed * self._config.discovery_ratio))

        # Fill discovery slots first (slower, network call)
        discovery_items: list[dict] = []
        if discovery_slots > 0 and self._discovery is not None:
            try:
                results = self._discovery.discover_from_profile(
                    self._profile, mood=mood,
                )
                for r in results:
                    if r.video_id not in queued_ids:
                        discovery_items.append({
                            "video_id": r.video_id,
                            "title": r.title,
                            "tags": "",
                            "duration": r.duration,
                            "score": 1.0,
                            "url": r.url,
                            "source": "discovery",
                        })
                        queued_ids.add(r.video_id)
                    if len(discovery_items) >= discovery_slots:
                        break
            except Exception:
                logger.warning("Discovery failed for mood %s", mood, exc_info=True)

        # Fill remaining slots from library (all pools)
        pool_needed = needed - len(discovery_items)
        pool_items: list[dict] = []
        if pool_needed > 0:
            scored = self._score_library(mood)
            scored = [v for v in scored if v["video_id"] not in queued_ids]
            if scored:
                shuffled = _weighted_shuffle(scored)
                pool_items = shuffled[:pool_needed]

        # Interleave: discovery items spread across the queue
        self._queue.extend(discovery_items + pool_items)

    def _score_library(self, mood: str) -> list[dict]:
        """Score all active pool videos across ALL blocks for a mood.

        Uses AI-enhanced scoring when a fresh profile is available,
        falls back to pure self-learning weights otherwise.
        """
        # Get ALL active pool videos across all blocks
        all_videos = self._db.fetchall(
            "SELECT * FROM autoplay_videos WHERE active = 1 ORDER BY added_date"
        )
        if not all_videos:
            return []

        # Get recent plays to avoid (across all blocks)
        recent = self._db.fetchall(
            "SELECT video_id FROM autoplay_history "
            "ORDER BY played_at DESC LIMIT ?",
            (self._pool.avoid_recent,),
        )
        recent_ids = {r["video_id"] for r in recent}

        # Check if AI scoring is available
        use_ai = (
            self._profile.is_loaded
            and not self._profile.is_stale(self._config.stale_threshold_hours)
        )

        # Get AI scoring data if available
        genre_weights = self._profile.get_genre_weights() if use_ai else {}
        energy = self._profile.get_energy_profile(mood) if use_ai else {}
        max_duration = energy.get("max_duration", 0)
        energy_genres = set(energy.get("genres", []))
        creator_affinity = self._profile.get_creator_affinity() if use_ai else {}
        avoid_patterns = self._profile.get_avoid_patterns() if use_ai else []

        # Get today's plays for recency penalty
        today_ids: set[str] = set()
        if use_ai:
            today_plays = self._db.fetchall(
                "SELECT DISTINCT video_id FROM autoplay_history "
                "WHERE date(played_at) = date('now')"
            )
            today_ids = {r["video_id"] for r in today_plays}

        scored = []
        for v in all_videos:
            if v["video_id"] in recent_ids:
                continue

            title = v.get("title", "") or ""
            title_lower = title.lower()

            # Check avoid patterns
            if use_ai and avoid_patterns:
                tags_lower = (v.get("tags", "") or "").lower()
                if any(p in title_lower or p in tags_lower for p in avoid_patterns):
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
                # Genre match: check video tags against profile weights + energy fit
                video_tags = [
                    t.strip().lower()
                    for t in (v.get("tags", "") or "").split(",")
                    if t.strip()
                ]
                genre_match = 0.5  # default for untagged/unmatched
                for tag in video_tags:
                    if tag in genre_weights:
                        tag_weight = genre_weights[tag]
                        # Boost if tag matches energy profile's preferred genres
                        if tag in energy_genres:
                            tag_weight = min(tag_weight * 1.3, 1.0)
                        genre_match = max(genre_match, tag_weight)

                # Duration fit
                duration = v.get("duration", 0) or 0
                if max_duration > 0 and duration > max_duration:
                    duration_fit = 0.3
                else:
                    duration_fit = 1.0

                # Creator affinity boost
                creator_boost = 1.0
                for creator, affinity in creator_affinity.items():
                    if creator.lower() in title_lower:
                        creator_boost = affinity
                        break

                # Recency penalty
                recency_penalty = 0.5 if v["video_id"] in today_ids else 1.0

                score = base_weight * genre_match * duration_fit * creator_boost * recency_penalty
            else:
                score = base_weight

            scored.append({
                "video_id": v["video_id"],
                "title": title,
                "tags": v.get("tags", ""),
                "duration": v.get("duration", 0),
                "block_name": v.get("block_name", ""),
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
        mood: str | None = None,
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
                (action, video_id, mood, source, score, reason),
            )
            self._db.commit()
        except Exception:
            logger.debug("Failed to write autopilot log", exc_info=True)


# --- Block-to-Mood Mapping (backward compat for PiPulse triggers) ---

_BLOCK_MOOD_MAP: dict[str, str] = {
    "morning-foundation": "chill",
    "evening-transition": "chill",
    "night-restoration": "chill",
    "creation-stack": "focus",
    "pro-gears": "focus",
    "sys-gears": "focus",
    "midday-reset": "vibes",
}


def _block_to_mood(block_name: str) -> str:
    """Map a TIM block name to a mood. Defaults to 'vibes' for unknown blocks."""
    return _BLOCK_MOOD_MAP.get(block_name, "vibes")


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
