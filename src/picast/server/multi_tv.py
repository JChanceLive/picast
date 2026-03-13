"""Multi-TV queue distribution for PiCast.

Distributes the playback queue across all connected TVs (main + fleet).
One video per screen, round-robin style. When a video finishes on any TV,
that TV pulls the next item from the shared queue.

This is a "work-stealing" pattern over a shared queue with optional
URL pre-checking via yt-dlp --simulate.
"""

import logging
import subprocess
import threading
import time

logger = logging.getLogger(__name__)

# How long to cache pre-check results (seconds)
_CHECK_CACHE_TTL = 300  # 5 minutes

# Pre-check subprocess timeout (seconds)
_CHECK_TIMEOUT = 8

# Watcher thread poll interval (seconds)
_WATCH_INTERVAL = 10


class MultiTVManager:
    """Manages queue distribution across multiple TVs.

    Coordinates the main player and fleet devices to each play one video
    from the shared queue simultaneously. Pre-checks URLs with yt-dlp
    before assignment.

    Usage:
        mtv = MultiTVManager(queue, fleet, player, sources)
        mtv.enable()   # start distributing
        mtv.disable()  # stop, let autopilot resume
    """

    def __init__(self, queue, fleet, player, sources):
        self._queue = queue           # QueueManager
        self._fleet = fleet           # FleetManager (can be None)
        self._player = player         # local Player
        self._sources = sources       # SourceRegistry (for URL validation)
        self._enabled = False
        self._assignments = {}        # device_id -> queue_item_id
        self._check_cache = {}        # url -> (ok: bool, checked_at: float)
        self._checking = False        # True during pre-check
        self._lock = threading.Lock()
        self._watcher = None          # background polling thread
        self._stop_event = threading.Event()

    @property
    def enabled(self) -> bool:
        return self._enabled

    def enable(self):
        """Enable multi-TV mode. Start pre-checking and distributing."""
        with self._lock:
            if self._enabled:
                return
            self._enabled = True
            self._assignments.clear()
            self._stop_event.clear()

        # Pre-check pending items in background, then distribute
        t = threading.Thread(
            target=self._enable_background,
            daemon=True,
            name="multi-tv-enable",
        )
        t.start()

    def _enable_background(self):
        """Background work for enable: poll fleet, pre-check, distribute."""
        # Poll fleet first so we know which devices are online
        if self._fleet:
            try:
                self._fleet.poll_devices()
            except Exception as e:
                logger.debug("Multi-TV enable poll error: %s", e)
        pending = self._queue.get_pending()
        if pending:
            self.pre_check(pending)
        self.distribute()
        self._start_watcher()

    def disable(self):
        """Disable multi-TV mode. Don't interrupt playing videos."""
        with self._lock:
            self._enabled = False
            self._assignments.clear()
            self._stop_event.set()

        # Wait for watcher to stop
        if self._watcher and self._watcher.is_alive():
            self._watcher.join(timeout=_WATCH_INTERVAL + 2)
            self._watcher = None

    def distribute(self):
        """Assign pending queue items to idle devices round-robin."""
        if not self._enabled:
            return

        idle_devices = self._get_idle_devices()
        if not idle_devices:
            return

        pending = self._queue.get_pending()
        if not pending:
            return

        for device_id in idle_devices:
            item = self._next_assignable(pending)
            if item is None:
                break  # No more playable items

            with self._lock:
                self._assignments[device_id] = item.id

            ok = self._push_to_device(device_id, item)
            if not ok:
                with self._lock:
                    self._assignments.pop(device_id, None)
                logger.warning(
                    "Multi-TV: failed to push item %d to %s",
                    item.id, device_id,
                )

    def on_video_finished(self, device_id: str):
        """Handle video completion on a device. Advance the queue."""
        with self._lock:
            item_id = self._assignments.pop(device_id, None)

        if item_id is not None:
            self._queue.mark_played(item_id)

        if self._enabled:
            # Distribute next item to this device
            self.distribute()

    def on_queue_changed(self):
        """Handle new items added to queue. Fill idle TVs."""
        if self._enabled:
            self.distribute()

    def pre_check(self, items):
        """Pre-check URLs with yt-dlp --simulate. Sequential, cached."""
        self._checking = True
        try:
            for item in items:
                url = item.url
                # Check cache first
                cached = self._check_cache.get(url)
                if cached:
                    ok, checked_at = cached
                    if time.monotonic() - checked_at < _CHECK_CACHE_TTL:
                        continue  # Still valid

                ok = self._check_url(url)
                self._check_cache[url] = (ok, time.monotonic())
        finally:
            self._checking = False

    def get_status(self) -> dict:
        """Return multi-TV status for API/UI."""
        with self._lock:
            devices = []
            for device_id in self._get_all_devices():
                item_id = self._assignments.get(device_id)
                devices.append({
                    "device_id": device_id,
                    "queue_item_id": item_id,
                })

            pending = self._queue.get_pending()
            assigned_ids = set(self._assignments.values())
            remaining = sum(
                1 for item in pending if item.id not in assigned_ids
            )

            skipped = sum(
                1 for url, (ok, _) in self._check_cache.items()
                if not ok
            )

            return {
                "enabled": self._enabled,
                "devices": devices,
                "queue_remaining": remaining,
                "skipped_urls": skipped,
                "checking": self._checking,
            }

    # --- Internal ---

    def _get_all_devices(self) -> list[str]:
        """Return all device IDs: main + fleet."""
        devices = ["main"]
        if self._fleet:
            devices.extend(self._fleet.device_ids)
        return devices

    def _get_idle_devices(self) -> list[str]:
        """Return device IDs not currently assigned a video."""
        with self._lock:
            all_devices = self._get_all_devices()
            assigned = set(self._assignments.keys())

            idle = []
            for dev_id in all_devices:
                if dev_id in assigned:
                    continue
                # For fleet devices, check if available (allows interrupting autoplay)
                if dev_id != "main" and self._fleet:
                    if not self._fleet.is_available_for_queue(dev_id):
                        continue
                idle.append(dev_id)
            return idle

    def _next_assignable(self, pending) -> object | None:
        """Get the next pending item that passes pre-check and isn't assigned."""
        with self._lock:
            assigned_ids = set(self._assignments.values())

        for item in pending:
            if item.id in assigned_ids:
                continue
            # Check pre-check cache
            cached = self._check_cache.get(item.url)
            if cached:
                ok, checked_at = cached
                if time.monotonic() - checked_at < _CHECK_CACHE_TTL and not ok:
                    continue  # Known bad URL, skip
            return item
        return None

    def _push_to_device(self, device_id: str, item) -> bool:
        """Push a queue item to a device for immediate playback."""
        if device_id == "main":
            try:
                self._player.play_now(item.url, item.title)
                logger.info(
                    "Multi-TV: main playing item %d (%s)",
                    item.id, item.title or item.url,
                )
                return True
            except Exception as e:
                logger.warning("Multi-TV: main play failed: %s", e)
                return False
        else:
            if self._fleet is None:
                return False
            return self._fleet.play_immediately(
                device_id,
                {"url": item.url, "title": item.title},
            )

    def _check_url(self, url: str) -> bool:
        """Check if a URL is playable using yt-dlp --simulate."""
        try:
            result = subprocess.run(
                ["yt-dlp", "--simulate", "--no-warnings", "--socket-timeout", "5", url],
                timeout=_CHECK_TIMEOUT,
                capture_output=True,
                text=True,
            )
            return result.returncode == 0
        except subprocess.TimeoutExpired:
            logger.debug("Multi-TV pre-check timeout for %s", url)
            return False
        except FileNotFoundError:
            # yt-dlp not installed — skip pre-checking
            logger.debug("Multi-TV: yt-dlp not found, skipping pre-check")
            return True
        except Exception as e:
            logger.debug("Multi-TV pre-check error for %s: %s", url, e)
            return False

    def _start_watcher(self):
        """Start the background watcher thread for fleet device completion."""
        if self._watcher and self._watcher.is_alive():
            return

        self._watcher = threading.Thread(
            target=self._watch_loop,
            daemon=True,
            name="multi-tv-watcher",
        )
        self._watcher.start()

    def _watch_loop(self):
        """Poll fleet devices to detect video completion."""
        while not self._stop_event.is_set():
            self._stop_event.wait(_WATCH_INTERVAL)
            if self._stop_event.is_set():
                break

            if not self._enabled or not self._fleet:
                continue

            # Poll fleet devices and detect completions
            try:
                self._fleet.poll_devices()
            except Exception as e:
                logger.debug("Multi-TV watcher poll error: %s", e)
                continue

            with self._lock:
                fleet_assignments = {
                    dev_id: item_id
                    for dev_id, item_id in self._assignments.items()
                    if dev_id != "main"
                }

            for dev_id, item_id in fleet_assignments.items():
                if self._fleet.is_device_idle(dev_id):
                    # Device was assigned but is now idle = video finished
                    logger.info(
                        "Multi-TV watcher: %s finished item %d",
                        dev_id, item_id,
                    )
                    self.on_video_finished(dev_id)
