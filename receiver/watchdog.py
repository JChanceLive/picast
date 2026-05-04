"""Receiver watchdog — detect mpv death/stall and auto-reconnect.

Extracted from picast_receiver.py v0.8.0 with no behavioral changes.
The watchdog observes a PlayerAdapter and acts on it; it never touches
mpv directly.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass


logger = logging.getLogger("picast-receiver.watchdog")


@dataclass
class WatchdogConfig:
    check_interval: int = 10           # seconds between polls
    max_retries: int = 5
    backoff: tuple = (5, 15, 30, 60, 120)
    stable_reset: int = 300             # reset retry count after 5m stable
    stall_threshold: int = 3            # consecutive same-pos checks


class ReceiverWatchdog:
    """Background thread that restarts the player on death or stall.

    Compose with a PlayerAdapter. Call start() once after Flask is up.
    """

    def __init__(self, player, config: WatchdogConfig | None = None) -> None:
        self._player = player
        self._cfg = config or WatchdogConfig()

        self._enabled = True
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

        self._retry_count = 0
        self._last_drop_time = 0.0
        self._last_time_pos = -1.0
        self._stall_count = 0

    # --- Lifecycle ---

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._loop, daemon=True, name="watchdog",
        )
        self._thread.start()
        logger.info(
            "Watchdog thread started (interval=%ds)", self._cfg.check_interval,
        )

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None

    # --- Toggle / status (used by /api/watchdog routes) ---

    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = bool(enabled)
        logger.info("Watchdog %s", "enabled" if self._enabled else "disabled")

    def status(self) -> dict:
        return {
            "enabled": self._enabled,
            "retry_count": self._retry_count,
            "max_retries": self._cfg.max_retries,
            "last_url": self._player.last_url,
            "last_drop_time": self._last_drop_time,
            "stall_count": self._stall_count,
        }

    # --- Loop body ---

    def _loop(self) -> None:
        cfg = self._cfg
        while not self._stop_event.is_set():
            self._stop_event.wait(cfg.check_interval)
            if self._stop_event.is_set():
                break
            try:
                if not self._enabled:
                    continue
                if not self._player.last_url:
                    continue
                if self._player.intentional_stop:
                    continue

                alive = not self._player.is_idle()

                # Pipe health: if mpv alive but streamlink dead → broken pipe.
                if alive and not self._player.streamlink_alive():
                    logger.warning(
                        "Watchdog: streamlink died, restarting pipe",
                    )
                    self._player.stop()
                    self._player.reset_intentional_stop()
                    alive = False

                if alive:
                    pos = self._player.get_time_pos()
                    if pos is not None:
                        if (
                            self._last_time_pos >= 0
                            and abs(pos - self._last_time_pos) < 0.5
                        ):
                            self._stall_count += 1
                            if self._stall_count >= cfg.stall_threshold:
                                logger.warning(
                                    "Watchdog: playback stalled at %.1fs for "
                                    "%d checks, restarting — %s",
                                    pos, self._stall_count,
                                    self._player.last_title or self._player.last_url,
                                )
                                self._stall_count = 0
                                self._last_time_pos = -1.0
                                self._player.stop()
                                self._player.reset_intentional_stop()
                                alive = False
                        else:
                            self._stall_count = 0
                        self._last_time_pos = pos

                    if alive:
                        # Stable-playback retry-counter reset
                        stable_since = self._player.last_stable_since
                        if stable_since > 0:
                            stable_secs = time.time() - stable_since
                            if stable_secs >= cfg.stable_reset and self._retry_count > 0:
                                logger.info(
                                    "Watchdog: stable %.0fs, "
                                    "resetting retry count", stable_secs,
                                )
                                self._retry_count = 0
                        continue

                # Reconnect path
                self._last_drop_time = time.time()
                if self._retry_count >= cfg.max_retries:
                    logger.warning(
                        "Watchdog: max retries (%d) reached, giving up on %s",
                        cfg.max_retries, self._player.last_url,
                    )
                    continue

                backoff = cfg.backoff[
                    min(self._retry_count, len(cfg.backoff) - 1)
                ]
                self._retry_count += 1
                logger.warning(
                    "Watchdog: mpv died, retry %d/%d in %ds — %s",
                    self._retry_count, cfg.max_retries, backoff,
                    self._player.last_title or self._player.last_url,
                )
                # Interruptible backoff
                if self._stop_event.wait(backoff):
                    break
                if self._player.intentional_stop or not self._enabled:
                    continue

                success = self._player.play(
                    self._player.last_url, self._player.last_title,
                )
                if success and self._player.last_volume != 100:
                    time.sleep(2)  # wait for IPC socket
                    self._player.set_volume(self._player.last_volume)
                    logger.info(
                        "Watchdog: restored volume to %d",
                        self._player.last_volume,
                    )
            except Exception:
                logger.exception("Watchdog: unexpected error (continuing)")
