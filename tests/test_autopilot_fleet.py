"""Tests for FleetManager — multi-device content routing for AI Autopilot."""

import json
from http.server import HTTPServer, BaseHTTPRequestHandler
from unittest.mock import MagicMock, patch

import pytest

from picast.config import AutopilotConfig, FleetDeviceConfig
from picast.server.autopilot_fleet import (
    DeviceState,
    FleetManager,
    mood_to_blocks,
    select_for_fleet,
)


# --- Helpers ---


def _make_fleet_config(devices=None):
    """Build an AutopilotConfig with fleet devices."""
    if devices is None:
        devices = {
            "living-room": FleetDeviceConfig(
                host="10.0.0.10", port=5050, room="living room", mood="chill",
            ),
            "office": FleetDeviceConfig(
                host="10.0.0.11", port=5050, room="office", mood="focus",
            ),
        }
    return AutopilotConfig(
        enabled=True,
        mode="fleet",
        fleet_devices=devices,
    )


def _mock_urlopen_response(data: dict, status=200):
    """Create a mock urllib response object."""
    mock_resp = MagicMock()
    mock_resp.read.return_value = json.dumps(data).encode()
    mock_resp.status = status
    mock_resp.__enter__ = MagicMock(return_value=mock_resp)
    mock_resp.__exit__ = MagicMock(return_value=False)
    return mock_resp


# --- FleetManager Init ---


class TestFleetManagerInit:
    def test_creates_device_states(self):
        config = _make_fleet_config()
        fm = FleetManager(config)
        assert len(fm.device_ids) == 2
        assert "living-room" in fm.device_ids
        assert "office" in fm.device_ids

    def test_empty_fleet(self):
        config = _make_fleet_config(devices={})
        fm = FleetManager(config)
        assert fm.device_ids == []

    def test_single_device(self):
        config = _make_fleet_config(devices={
            "bedroom": FleetDeviceConfig(
                host="10.0.0.12", port=5050, room="bedroom", mood="chill",
            ),
        })
        fm = FleetManager(config)
        assert fm.device_ids == ["bedroom"]


# --- Device Polling ---


class TestDevicePolling:
    @patch("picast.server.autopilot_fleet.urllib.request.urlopen")
    def test_poll_online_idle(self, mock_urlopen):
        mock_urlopen.return_value = _mock_urlopen_response({
            "idle": True,
            "title": "",
            "url": "",
            "autoplay_enabled": False,
        })

        config = _make_fleet_config()
        fm = FleetManager(config)
        fm.poll_devices()

        assert fm.is_device_idle("living-room")
        assert not fm.is_manual_override("living-room")

    @patch("picast.server.autopilot_fleet.urllib.request.urlopen")
    def test_poll_online_playing(self, mock_urlopen):
        mock_urlopen.return_value = _mock_urlopen_response({
            "idle": False,
            "title": "Test Video",
            "url": "https://youtube.com/watch?v=test123test",
            "autoplay_enabled": True,
        })

        config = _make_fleet_config()
        fm = FleetManager(config)
        fm.poll_devices()

        assert not fm.is_device_idle("living-room")
        # autoplay_enabled=True + playing = NOT manual override
        assert not fm.is_manual_override("living-room")

    @patch("picast.server.autopilot_fleet.urllib.request.urlopen")
    def test_poll_manual_override(self, mock_urlopen):
        """Device playing with autoplay disabled = manual override."""
        mock_urlopen.return_value = _mock_urlopen_response({
            "idle": False,
            "title": "User Picked Video",
            "url": "https://youtube.com/watch?v=manual12345",
            "autoplay_enabled": False,
        })

        config = _make_fleet_config()
        fm = FleetManager(config)
        fm.poll_devices()

        assert not fm.is_device_idle("living-room")
        assert fm.is_manual_override("living-room")

    @patch("picast.server.autopilot_fleet.urllib.request.urlopen")
    def test_poll_offline_device(self, mock_urlopen):
        import urllib.error
        mock_urlopen.side_effect = urllib.error.URLError("Connection refused")

        config = _make_fleet_config()
        fm = FleetManager(config)
        fm.poll_devices()

        assert not fm.is_device_idle("living-room")
        assert not fm.is_manual_override("living-room")

    @patch("picast.server.autopilot_fleet.urllib.request.urlopen")
    def test_poll_tracks_consecutive_failures(self, mock_urlopen):
        import urllib.error
        mock_urlopen.side_effect = urllib.error.URLError("timeout")

        config = _make_fleet_config()
        fm = FleetManager(config)

        fm.poll_devices()
        fm.poll_devices()

        state = fm._devices["living-room"]
        assert state.consecutive_failures == 2

    @patch("picast.server.autopilot_fleet.urllib.request.urlopen")
    def test_poll_resets_failures_on_success(self, mock_urlopen):
        import urllib.error

        config = _make_fleet_config()
        fm = FleetManager(config)

        # First: fail
        mock_urlopen.side_effect = urllib.error.URLError("timeout")
        fm.poll_devices()
        assert fm._devices["living-room"].consecutive_failures == 1

        # Then: succeed
        mock_urlopen.side_effect = None
        mock_urlopen.return_value = _mock_urlopen_response({"idle": True})
        fm.poll_devices()
        assert fm._devices["living-room"].consecutive_failures == 0

    @patch("picast.server.autopilot_fleet.urllib.request.urlopen")
    def test_poll_single_device(self, mock_urlopen):
        mock_urlopen.return_value = _mock_urlopen_response({"idle": True})

        config = _make_fleet_config()
        fm = FleetManager(config)
        state = fm.poll_device("office")

        assert state is not None
        assert state.online
        assert state.idle

    def test_poll_unknown_device(self):
        config = _make_fleet_config()
        fm = FleetManager(config)
        assert fm.poll_device("nonexistent") is None


# --- Content Push ---


class TestContentPush:
    @patch("picast.server.autopilot_fleet.urllib.request.urlopen")
    def test_push_success(self, mock_urlopen):
        # Make device online first
        mock_urlopen.return_value = _mock_urlopen_response({"idle": True})
        config = _make_fleet_config()
        fm = FleetManager(config)
        fm.poll_devices()

        # Push content
        mock_urlopen.return_value = _mock_urlopen_response({"ok": True})
        result = fm.push_content("living-room", {
            "url": "https://youtube.com/watch?v=test123test",
            "title": "Test Video",
        })
        assert result is True

    @patch("picast.server.autopilot_fleet.urllib.request.urlopen")
    def test_push_rejected(self, mock_urlopen):
        mock_urlopen.return_value = _mock_urlopen_response({"idle": True})
        config = _make_fleet_config()
        fm = FleetManager(config)
        fm.poll_devices()

        mock_urlopen.return_value = _mock_urlopen_response(
            {"ok": False, "error": "queue full"}
        )
        result = fm.push_content("living-room", {
            "url": "https://youtube.com/watch?v=test123test",
        })
        assert result is False

    def test_push_offline_device(self):
        config = _make_fleet_config()
        fm = FleetManager(config)
        # Device never polled = offline
        result = fm.push_content("living-room", {
            "url": "https://youtube.com/watch?v=test123test",
        })
        assert result is False

    def test_push_unknown_device(self):
        config = _make_fleet_config()
        fm = FleetManager(config)
        result = fm.push_content("nonexistent", {
            "url": "https://youtube.com/watch?v=test123test",
        })
        assert result is False

    def test_push_no_url(self):
        config = _make_fleet_config()
        fm = FleetManager(config)
        fm._devices["living-room"].online = True
        result = fm.push_content("living-room", {"title": "No URL"})
        assert result is False

    @patch("picast.server.autopilot_fleet.urllib.request.urlopen")
    def test_push_network_error(self, mock_urlopen):
        import urllib.error

        config = _make_fleet_config()
        fm = FleetManager(config)
        fm._devices["living-room"].online = True

        mock_urlopen.side_effect = urllib.error.URLError("connection reset")
        result = fm.push_content("living-room", {
            "url": "https://youtube.com/watch?v=test123test",
        })
        assert result is False


# --- Fleet Status ---


class TestFleetStatus:
    def test_status_returns_all_devices(self):
        config = _make_fleet_config()
        fm = FleetManager(config)
        status = fm.get_fleet_status()
        assert len(status) == 2
        ids = {d["device_id"] for d in status}
        assert ids == {"living-room", "office"}

    def test_status_includes_room_and_mood(self):
        config = _make_fleet_config()
        fm = FleetManager(config)
        status = fm.get_fleet_status()
        lr = next(d for d in status if d["device_id"] == "living-room")
        assert lr["room"] == "living room"
        assert lr["mood"] == "chill"

    @patch("picast.server.autopilot_fleet.urllib.request.urlopen")
    def test_get_idle_devices(self, mock_urlopen):
        mock_urlopen.return_value = _mock_urlopen_response({"idle": True})
        config = _make_fleet_config()
        fm = FleetManager(config)
        fm.poll_devices()

        idle = fm.get_idle_devices()
        assert len(idle) == 2


# --- Mood Mapping ---


class TestMoodMapping:
    def test_chill_mood(self):
        blocks = mood_to_blocks("chill")
        assert "evening-transition" in blocks
        assert "night-restoration" in blocks

    def test_focus_mood(self):
        blocks = mood_to_blocks("focus")
        assert "creation-stack" in blocks
        assert "pro-gears" in blocks

    def test_energy_mood(self):
        blocks = mood_to_blocks("energy")
        assert "midday-reset" in blocks

    def test_unknown_mood(self):
        blocks = mood_to_blocks("unknown")
        assert blocks == []


# --- Fleet Content Selection ---


class TestSelectForFleet:
    @patch("picast.server.autopilot_fleet.urllib.request.urlopen")
    def test_pushes_to_idle_devices(self, mock_urlopen):
        mock_urlopen.return_value = _mock_urlopen_response({"idle": True})

        config = _make_fleet_config()
        fm = FleetManager(config)
        fm.poll_devices()

        mock_engine = MagicMock()
        mock_engine.select_next.return_value = {
            "video_id": "test123test",
            "title": "Test Video",
            "url": "https://youtube.com/watch?v=test123test",
        }

        # Push returns success
        mock_urlopen.return_value = _mock_urlopen_response({"ok": True})

        results = select_for_fleet(fm, mock_engine, MagicMock(), config)
        assert len(results) == 2
        assert all(r["success"] for r in results)

    def test_skips_when_no_idle_devices(self):
        config = _make_fleet_config()
        fm = FleetManager(config)
        # No poll = all offline = no idle devices

        results = select_for_fleet(fm, MagicMock(), MagicMock(), config)
        assert results == []

    @patch("picast.server.autopilot_fleet.urllib.request.urlopen")
    def test_skips_device_with_no_video(self, mock_urlopen):
        mock_urlopen.return_value = _mock_urlopen_response({"idle": True})

        config = _make_fleet_config()
        fm = FleetManager(config)
        fm.poll_devices()

        mock_engine = MagicMock()
        mock_engine.select_next.return_value = None  # No videos available

        results = select_for_fleet(fm, mock_engine, MagicMock(), config)
        assert len(results) == 2
        assert all(not r["success"] for r in results)

    @patch("picast.server.autopilot_fleet.urllib.request.urlopen")
    def test_mood_routes_to_correct_blocks(self, mock_urlopen):
        mock_urlopen.return_value = _mock_urlopen_response({"idle": True})

        config = _make_fleet_config()
        fm = FleetManager(config)
        fm.poll_devices()

        mock_engine = MagicMock()
        called_blocks = []

        def track_select(block):
            called_blocks.append(block)
            return {
                "video_id": "test123test",
                "title": "Test",
                "url": "https://youtube.com/watch?v=test123test",
            }

        mock_engine.select_next.side_effect = track_select

        mock_urlopen.return_value = _mock_urlopen_response({"ok": True})
        select_for_fleet(fm, mock_engine, MagicMock(), config)

        # living-room is "chill" -> first block is "evening-transition"
        # office is "focus" -> first block is "creation-stack"
        assert "evening-transition" in called_blocks
        assert "creation-stack" in called_blocks


# --- Device Info Helpers ---


class TestDeviceInfoHelpers:
    def test_get_device_mood(self):
        config = _make_fleet_config()
        fm = FleetManager(config)
        assert fm.get_device_mood("living-room") == "chill"
        assert fm.get_device_mood("office") == "focus"
        assert fm.get_device_mood("nonexistent") == ""

    def test_get_device_room(self):
        config = _make_fleet_config()
        fm = FleetManager(config)
        assert fm.get_device_room("living-room") == "living room"
        assert fm.get_device_room("office") == "office"
        assert fm.get_device_room("nonexistent") == ""
