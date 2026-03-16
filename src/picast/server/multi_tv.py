"""Multi-TV queue distribution for PiCast.

Distributes the playback queue across all connected TVs (main + fleet).
One video per screen, round-robin style. When a video finishes on any TV,
that TV pulls the next item from the shared queue.

This is a "work-stealing" pattern over a shared queue with optional
URL pre-checking via yt-dlp --simulate.
"""

import json
import logging
import subprocess
import threading
import time
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)

# How long to cache pre-check results (seconds)
_CHECK_CACHE_TTL = 300  # 5 minutes

# Pre-check subprocess timeout (seconds)
_CHECK_TIMEOUT = 8

# Watcher thread poll intervals (seconds)
_WATCH_INTERVAL_PLAYING = 3   # Fast poll when devices are playing
_WATCH_INTERVAL_IDLE = 8      # Slow poll when all devices idle

# Fleet proxy HTTP timeout (seconds)
_FLEET_PROXY_TIMEOUT = 5


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
        """Enable multi-TV mode. Start pre-checking and distributing.

        If already enabled, performs a full reset: clears assignments
        and redistributes from the queue to all devices.
        """
        already_on = False
        with self._lock:
            if self._enabled:
                already_on = True
                # Reset: clear assignments and pre-check cache
                self._assignments.clear()
                self._check_cache.clear()
            else:
                self._enabled = True
                self._stop_event.clear()

        if already_on:
            # Already running — redistribute immediately (watcher alive)
            self.distribute()
            return

        # Start background thread: poll fleet, then distribute
        t = threading.Thread(
            target=self._enable_background,
            daemon=True,
            name="multi-tv-enable",
        )
        t.start()

    def _enable_background(self):
        """Background work for enable: poll fleet then distribute.

        Pre-check is intentionally skipped — yt-dlp --simulate rejects
        valid URLs (Twitch, playlist params, auth-gated) causing the
        entire queue to be blacklisted.  Let the player/receiver handle
        actual playback errors instead.
        """
        try:
            # Poll fleet first so we know which devices are online
            if self._fleet:
                try:
                    self._fleet.poll_devices()
                except Exception as e:
                    logger.debug("Multi-TV enable poll error: %s", e)
            self.distribute()
        except Exception as e:
            logger.warning("Multi-TV enable background error: %s", e)
        # Always start watcher regardless of prior errors
        self._start_watcher()

    def disable(self):
        """Disable multi-TV mode. Don't interrupt playing videos."""
        with self._lock:
            self._enabled = False
            self._assignments.clear()
            self._stop_event.set()

        # Wait for watcher to stop
        if self._watcher and self._watcher.is_alive():
            self._watcher.join(timeout=_WATCH_INTERVAL_IDLE + 2)
            self._watcher = None

    def distribute(self):
        """Assign pending queue items to idle devices round-robin."""
        if not self._enabled:
            return

        idle_devices = self._get_idle_devices()
        logger.info("Multi-TV distribute: idle devices = %s", idle_devices)
        if not idle_devices:
            return

        pending = self._queue.get_pending()
        logger.info(
            "Multi-TV distribute: %d pending items", len(pending) if pending else 0,
        )
        if not pending:
            return

        for device_id in idle_devices:
            item = self._next_assignable(pending)
            if item is None:
                logger.info("Multi-TV distribute: no more assignable items")
                break  # No more playable items

            logger.info(
                "Multi-TV distribute: assigning item %d (%s) -> %s",
                item.id, item.title or item.url, device_id,
            )
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
        """Handle new items added to queue. Fill idle TVs.

        Runs distribute() in a background thread so the caller (HTTP endpoint)
        is not blocked by fleet device HTTP calls.
        """
        if self._enabled:
            threading.Thread(
                target=self._safe_distribute,
                daemon=True,
                name="multi-tv-queue-changed",
            ).start()

    def _safe_distribute(self):
        """Distribute with exception handling for background thread use."""
        try:
            self.distribute()
        except Exception as e:
            logger.warning("Multi-TV on_queue_changed distribute error: %s", e)

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

    # --- Per-Device Remote Control ---

    def skip_device(self, device_id: str) -> dict:
        """Skip the current video on a device.

        Clears the assignment, moves the skipped item to the end of the
        pending queue, and triggers redistribution so the device gets
        the next item.

        Returns dict with 'ok', 'skipped_item_id', and optionally
        'new_item_id' on success, or 'error' on failure.
        """
        with self._lock:
            item_id = self._assignments.get(device_id)
            if item_id is None:
                return {"ok": False, "error": f"No assignment for {device_id}"}
            del self._assignments[device_id]

        # Move skipped item to end of pending queue
        self._queue.move_to_end(item_id)

        # For main device, interrupt the current playback
        if device_id == "main":
            try:
                self._player.skip()
            except Exception as e:
                logger.warning("skip_device: player.skip() failed: %s", e)

        # Redistribute to fill this device with the next item
        if self._enabled:
            self.distribute()

        with self._lock:
            new_item_id = self._assignments.get(device_id)

        result = {"ok": True, "skipped_item_id": item_id}
        if new_item_id is not None:
            result["new_item_id"] = new_item_id
        return result

    def pause_device(self, device_id: str) -> bool:
        """Pause playback on a device."""
        if device_id == "main":
            return self._player.mpv.pause()
        return self._fleet_proxy(device_id, "pause")

    def resume_device(self, device_id: str) -> bool:
        """Resume playback on a device."""
        if device_id == "main":
            return self._player.mpv.resume()
        return self._fleet_proxy(device_id, "resume")

    def set_device_volume(self, device_id: str, level: int) -> bool:
        """Set volume on a device (0-100)."""
        if device_id == "main":
            return self._player.mpv.set_volume(level)
        return self._fleet_proxy_json(device_id, "volume", {"level": level})

    def get_device_status(self, device_id: str) -> dict:
        """Get detailed status for a single device."""
        if device_id == "main":
            status = self._player.get_status()
            with self._lock:
                status["queue_item_id"] = self._assignments.get("main")
            return status

        # Fleet device: proxy GET to device's /api/status
        if self._fleet is None:
            return {"error": f"No fleet manager for {device_id}"}
        return self._fleet_proxy_get(device_id, "status")

    def _fleet_proxy(self, device_id: str, action: str) -> bool:
        """POST to a fleet device's /api/{action} endpoint.

        Returns True on success, False on failure.
        """
        if self._fleet is None:
            logger.warning("fleet_proxy: no fleet manager for %s", device_id)
            return False

        with self._fleet._lock:
            state = self._fleet._devices.get(device_id)
        if state is None or not state.online:
            logger.warning("fleet_proxy: device %s unavailable", device_id)
            return False

        base = self._fleet._device_base_url(state.config)
        req = urllib.request.Request(
            f"{base}/api/{action}",
            data=b"",
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=_FLEET_PROXY_TIMEOUT) as resp:
                data = json.loads(resp.read())
                return bool(data.get("ok", False))
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
            logger.warning("fleet_proxy %s/%s failed: %s", device_id, action, e)
            return False

    def _fleet_proxy_json(self, device_id: str, action: str, payload: dict) -> bool:
        """POST with JSON body to a fleet device's /api/{action} endpoint.

        Returns True on success, False on failure.
        """
        if self._fleet is None:
            logger.warning("fleet_proxy_json: no fleet manager for %s", device_id)
            return False

        with self._fleet._lock:
            state = self._fleet._devices.get(device_id)
        if state is None or not state.online:
            logger.warning("fleet_proxy_json: device %s unavailable", device_id)
            return False

        base = self._fleet._device_base_url(state.config)
        body = json.dumps(payload).encode()
        req = urllib.request.Request(
            f"{base}/api/{action}",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=_FLEET_PROXY_TIMEOUT) as resp:
                data = json.loads(resp.read())
                return bool(data.get("ok", False))
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
            logger.warning("fleet_proxy_json %s/%s failed: %s", device_id, action, e)
            return False

    def _fleet_proxy_get(self, device_id: str, action: str) -> dict:
        """GET from a fleet device's /api/{action} endpoint.

        Returns the JSON response dict, or an error dict on failure.
        """
        if self._fleet is None:
            return {"error": f"No fleet manager for {device_id}"}

        with self._fleet._lock:
            state = self._fleet._devices.get(device_id)
        if state is None or not state.online:
            return {"error": f"Device {device_id} unavailable"}

        base = self._fleet._device_base_url(state.config)
        req = urllib.request.Request(f"{base}/api/{action}")
        try:
            with urllib.request.urlopen(req, timeout=_FLEET_PROXY_TIMEOUT) as resp:
                data = json.loads(resp.read())
                # Enrich with room/mood from fleet config
                data["room"] = state.config.room
                data["mood"] = state.config.mood
                with self._lock:
                    data["queue_item_id"] = self._assignments.get(device_id)
                return data
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
            logger.warning("fleet_proxy_get %s/%s failed: %s", device_id, action, e)
            return {"error": str(e)}

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
                    logger.debug("Multi-TV idle check: %s already assigned", dev_id)
                    continue
                # For fleet devices, check if available (allows interrupting autoplay)
                if dev_id != "main" and self._fleet:
                    available = self._fleet.is_available_for_queue(dev_id)
                    if not available:
                        logger.info(
                            "Multi-TV idle check: %s NOT available for queue", dev_id,
                        )
                        continue
                idle.append(dev_id)
            logger.debug(
                "Multi-TV idle check: all=%s, assigned=%s, idle=%s",
                all_devices, assigned, idle,
            )
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
                logger.warning("Multi-TV push: no fleet manager for %s", device_id)
                return False
            logger.info(
                "Multi-TV push: sending play_immediately to %s (%s)",
                device_id, item.url,
            )
            ok = self._fleet.play_immediately(
                device_id,
                {"url": item.url, "title": item.title},
            )
            logger.info("Multi-TV push: %s result=%s", device_id, ok)
            return ok

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
        """Poll fleet devices to detect video completion.

        Uses adaptive polling: fast (3s) when any device is playing,
        slow (8s) when all are idle.
        """
        while not self._stop_event.is_set():
            # Adaptive interval: fast when playing, slow when idle
            with self._lock:
                has_assignments = bool(self._assignments)
            interval = (
                _WATCH_INTERVAL_PLAYING if has_assignments
                else _WATCH_INTERVAL_IDLE
            )

            self._stop_event.wait(interval)
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
