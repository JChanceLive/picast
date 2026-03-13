"""Fleet device manager for AI Autopilot multi-room content routing.

Coordinates content distribution across multiple PiCast devices on the
local network. Each device has a room and mood that determines what
content the autopilot selects for it.

Fleet devices are other PiCast instances reachable via their REST API.
The fleet manager polls their status, detects manual overrides, and
pushes content to idle devices.
"""

import json
import logging
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone

from picast.config import AutopilotConfig, FleetDeviceConfig

logger = logging.getLogger(__name__)

# How long to wait for fleet device HTTP responses
_DEVICE_TIMEOUT = 5  # seconds


@dataclass
class DeviceState:
    """Tracked state for a single fleet device."""

    device_id: str
    config: FleetDeviceConfig
    online: bool = False
    idle: bool = True
    playing_title: str = ""
    playing_url: str = ""
    autoplay_enabled: bool = False
    manual_override: bool = False
    last_poll: datetime | None = None
    last_error: str = ""
    consecutive_failures: int = 0


class FleetManager:
    """Manages fleet devices and routes content based on mood/room.

    The fleet manager is stateful — it tracks device health and detects
    manual overrides (user-initiated playback that should not be
    interrupted by autopilot).

    Usage:
        fm = FleetManager(config)
        fm.poll_devices()  # refresh status
        for device_id in fm.get_idle_devices():
            fm.push_content(device_id, {"url": "...", "title": "..."})
    """

    def __init__(self, config: AutopilotConfig):
        self._config = config
        self._devices: dict[str, DeviceState] = {}
        self._lock = threading.Lock()

        # Initialize device states from config
        for dev_id, dev_conf in config.fleet_devices.items():
            self._devices[dev_id] = DeviceState(
                device_id=dev_id,
                config=dev_conf,
            )

    @property
    def device_ids(self) -> list[str]:
        """List of configured device IDs."""
        return list(self._devices.keys())

    def poll_devices(self) -> dict[str, DeviceState]:
        """Poll all fleet devices for current status.

        Makes GET /api/status requests to each device. Updates internal
        state with online/idle/playing info. Returns the full state dict.
        """
        with self._lock:
            for dev_id, state in self._devices.items():
                self._poll_one(state)
            return dict(self._devices)

    def poll_if_stale(self, max_age: float = 10) -> None:
        """Poll devices only if the most recent poll is older than max_age seconds."""
        with self._lock:
            now = datetime.now(timezone.utc)
            newest = None
            for state in self._devices.values():
                if state.last_poll is not None:
                    if newest is None or state.last_poll > newest:
                        newest = state.last_poll
            if newest is not None and (now - newest).total_seconds() < max_age:
                return  # Data is fresh enough
            for state in self._devices.values():
                self._poll_one(state)

    def poll_device(self, device_id: str) -> DeviceState | None:
        """Poll a single device. Returns its state or None if unknown."""
        with self._lock:
            state = self._devices.get(device_id)
            if state is None:
                return None
            self._poll_one(state)
            return state

    def is_device_idle(self, device_id: str) -> bool:
        """Check if a device is idle and ready for autopilot content."""
        with self._lock:
            state = self._devices.get(device_id)
            if state is None:
                return False
            return state.online and state.idle and not state.manual_override

    def is_available_for_queue(self, device_id: str) -> bool:
        """Check if a device can receive queue content.

        Available if online and not manually overridden. Unlike is_device_idle,
        this returns True even if the device is playing autoplay content —
        user-curated queue items take priority over autoplay.
        """
        with self._lock:
            state = self._devices.get(device_id)
            if state is None:
                return False
            return state.online and not state.manual_override

    def is_manual_override(self, device_id: str) -> bool:
        """Check if a device has user-initiated content playing.

        Manual override means the device is playing something that was
        NOT pushed by autopilot. We detect this by checking if the device
        is playing but autopilot is disabled or the content doesn't match
        what we pushed.
        """
        with self._lock:
            state = self._devices.get(device_id)
            if state is None:
                return False
            return state.manual_override

    def push_content(self, device_id: str, video: dict) -> bool:
        """Push a video to a fleet device via POST /api/queue/add.

        Args:
            device_id: Target device identifier
            video: Dict with at least 'url' and optionally 'title'

        Returns:
            True if the device accepted the content, False otherwise.
        """
        with self._lock:
            state = self._devices.get(device_id)
            if state is None:
                logger.warning("push_content: unknown device %s", device_id)
                return False

            if not state.online:
                logger.info("push_content: device %s is offline", device_id)
                return False

        url = video.get("url", "")
        title = video.get("title", "")
        if not url:
            logger.warning("push_content: no url in video dict")
            return False

        base = self._device_base_url(state.config)
        push_data: dict = {"url": url, "title": title}
        if state.config.mute:
            push_data["mute"] = True
        payload = json.dumps(push_data).encode()
        req = urllib.request.Request(
            f"{base}/api/queue/add",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=_DEVICE_TIMEOUT) as resp:
                data = json.loads(resp.read())
                ok = data.get("ok", False)
                if ok:
                    logger.info(
                        "Pushed to %s: %s (%s)", device_id, title, url
                    )
                else:
                    logger.warning(
                        "Device %s rejected push: %s",
                        device_id, data.get("error", "unknown"),
                    )
                return bool(ok)
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
            logger.warning("push_content failed for %s: %s", device_id, e)
            return False

    def play_immediately(self, device_id: str, video: dict) -> bool:
        """Play a video immediately on a fleet device via POST /api/play.

        Unlike push_content() which queues, this triggers immediate playback.

        Args:
            device_id: Target device identifier
            video: Dict with at least 'url' and optionally 'title'

        Returns:
            True if the device accepted the play command, False otherwise.
        """
        with self._lock:
            state = self._devices.get(device_id)
            if state is None:
                logger.warning("play_immediately: unknown device %s", device_id)
                return False

            if not state.online:
                logger.info("play_immediately: device %s is offline", device_id)
                return False

        url = video.get("url", "")
        title = video.get("title", "")
        if not url:
            logger.warning("play_immediately: no url in video dict")
            return False

        base = self._device_base_url(state.config)
        play_data = {"url": url, "title": title}
        if state.config.mute:
            play_data["mute"] = True
        payload = json.dumps(play_data).encode()
        req = urllib.request.Request(
            f"{base}/api/play",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        try:
            with urllib.request.urlopen(req, timeout=_DEVICE_TIMEOUT) as resp:
                data = json.loads(resp.read())
                ok = data.get("ok", False)
                if ok:
                    logger.info(
                        "Play immediately on %s: %s (%s)", device_id, title, url
                    )
                else:
                    logger.warning(
                        "Device %s rejected play: %s",
                        device_id, data.get("error", "unknown"),
                    )
                return bool(ok)
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
            logger.warning("play_immediately failed for %s: %s", device_id, e)
            return False

    def get_fleet_status(self) -> list[dict]:
        """Return fleet status for UI display.

        Returns a list of device dicts with all relevant status info.
        """
        with self._lock:
            result = []
            for dev_id, state in self._devices.items():
                result.append({
                    "device_id": dev_id,
                    "room": state.config.room,
                    "mood": state.config.mood,
                    "host": state.config.host,
                    "port": state.config.port,
                    "online": state.online,
                    "idle": state.idle,
                    "manual_override": state.manual_override,
                    "playing_title": state.playing_title,
                    "playing_url": state.playing_url,
                    "last_poll": (
                        state.last_poll.isoformat() if state.last_poll else None
                    ),
                    "last_error": state.last_error,
                })
            return result

    def get_idle_devices(self) -> list[str]:
        """Return device IDs that are idle and ready for content."""
        with self._lock:
            return [
                dev_id
                for dev_id, state in self._devices.items()
                if state.online and state.idle and not state.manual_override
            ]

    def get_device_mood(self, device_id: str) -> str:
        """Get the mood configured for a device."""
        with self._lock:
            state = self._devices.get(device_id)
            if state is None:
                return ""
            return state.config.mood

    def get_device_room(self, device_id: str) -> str:
        """Get the room configured for a device."""
        with self._lock:
            state = self._devices.get(device_id)
            if state is None:
                return ""
            return state.config.room

    # --- Internal ---

    def _poll_one(self, state: DeviceState) -> None:
        """Poll a single device and update its state. Must hold _lock."""
        base = self._device_base_url(state.config)
        req = urllib.request.Request(f"{base}/api/status")

        try:
            with urllib.request.urlopen(req, timeout=_DEVICE_TIMEOUT) as resp:
                data = json.loads(resp.read())

            state.online = True
            state.idle = data.get("idle", True)
            state.playing_title = data.get("title", "") or ""
            state.playing_url = data.get("url", "") or ""
            state.autoplay_enabled = data.get("autoplay_enabled", False)
            state.last_poll = datetime.now(timezone.utc)
            state.last_error = ""
            state.consecutive_failures = 0

            # Manual override: device is playing AND autoplay is off
            # (meaning user started something manually, not via autopilot)
            state.manual_override = (
                not state.idle
                and not state.autoplay_enabled
            )

        except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
            state.online = False
            state.idle = False
            state.manual_override = False
            state.last_error = str(e)
            state.consecutive_failures += 1
            state.last_poll = datetime.now(timezone.utc)
            logger.debug("Poll failed for %s: %s", state.device_id, e)

    @staticmethod
    def _device_base_url(config: FleetDeviceConfig) -> str:
        """Build the base URL for a fleet device."""
        return f"http://{config.host}:{config.port}"


# --- Content Routing ---


def select_for_fleet(
    fleet: FleetManager,
    engine,
    profile,
    config: AutopilotConfig,
) -> list[dict]:
    """Select and push content to all idle fleet devices.

    For each idle device:
    1. Get the device's mood (chill/focus/vibes)
    2. Pick a video using the engine's mood-based scoring
    3. Push to the device

    Returns a list of push results: [{device_id, video, success}]
    """
    results = []

    idle_devices = fleet.get_idle_devices()
    if not idle_devices:
        return results

    for device_id in idle_devices:
        mood = fleet.get_device_mood(device_id)
        if not mood:
            logger.info(
                "No mood configured for device %s, skipping", device_id
            )
            continue

        video = engine.select_next(mood=mood)

        if not video:
            logger.info("No video available for device %s (mood=%s)", device_id, mood)
            results.append({
                "device_id": device_id,
                "video": None,
                "success": False,
            })
            continue

        success = fleet.push_content(device_id, video)
        results.append({
            "device_id": device_id,
            "video": video,
            "success": success,
        })

    return results
