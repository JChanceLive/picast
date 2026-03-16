"""Tests for PiCast Receiver API routes."""

from unittest.mock import patch, MagicMock

import pytest

import picast_receiver
from picast_receiver import app


@pytest.fixture
def client():
    app.config["TESTING"] = True
    return app.test_client()


class TestHealth:
    def test_health_returns_ok(self, client):
        resp = client.get("/api/health")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["type"] == "receiver"
        assert "version" in data
        assert "hostname" in data


class TestStatus:
    def test_status_idle_by_default(self, client):
        resp = client.get("/api/status")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["idle"] is True
        assert data["title"] == ""
        assert data["url"] == ""
        assert data["autoplay_enabled"] is True


class TestPlay:
    def test_play_requires_url(self, client):
        resp = client.post("/api/play", json={})
        assert resp.status_code == 400
        assert "url required" in resp.get_json()["error"]

    def test_play_empty_url(self, client):
        resp = client.post("/api/play", json={"url": ""})
        assert resp.status_code == 400

    @patch("picast_receiver._play_url", return_value=True)
    def test_play_success(self, mock_play, client):
        resp = client.post("/api/play", json={
            "url": "https://youtube.com/watch?v=test123test",
            "title": "Test Video",
        })
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        mock_play.assert_called_once_with(
            "https://youtube.com/watch?v=test123test", "Test Video", mute=False
        )

    @patch("picast_receiver._play_url", return_value=False)
    def test_play_failure(self, mock_play, client):
        resp = client.post("/api/play", json={
            "url": "https://youtube.com/watch?v=test123test",
        })
        assert resp.status_code == 500


class TestQueueAdd:
    def test_queue_add_requires_url(self, client):
        resp = client.post("/api/queue/add", json={})
        assert resp.status_code == 400

    @patch("picast_receiver._play_url", return_value=True)
    def test_queue_add_plays_immediately(self, mock_play, client):
        resp = client.post("/api/queue/add", json={
            "url": "https://youtube.com/watch?v=test123test",
            "title": "Fleet Push",
        })
        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True
        mock_play.assert_called_once()


class TestPause:
    def test_pause_when_idle(self, client):
        resp = client.post("/api/pause")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is False
        assert "not playing" in data["error"]

    @patch("picast_receiver._is_idle", return_value=False)
    @patch("picast_receiver._mpv_command", return_value={"error": "success"})
    def test_pause_success(self, mock_cmd, mock_idle, client):
        resp = client.post("/api/pause")
        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True
        mock_cmd.assert_called_once_with(["set_property", "pause", True])

    @patch("picast_receiver._is_idle", return_value=False)
    @patch("picast_receiver._mpv_command", return_value=None)
    def test_pause_ipc_failure(self, mock_cmd, mock_idle, client):
        resp = client.post("/api/pause")
        assert resp.status_code == 200
        assert resp.get_json()["ok"] is False


class TestResume:
    def test_resume_when_idle(self, client):
        resp = client.post("/api/resume")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is False
        assert "not playing" in data["error"]

    @patch("picast_receiver._is_idle", return_value=False)
    @patch("picast_receiver._mpv_command", return_value={"error": "success"})
    def test_resume_success(self, mock_cmd, mock_idle, client):
        resp = client.post("/api/resume")
        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True
        mock_cmd.assert_called_once_with(["set_property", "pause", False])


class TestStatusExpanded:
    @patch("picast_receiver._is_idle", return_value=False)
    @patch("picast_receiver._mpv_command")
    def test_status_includes_playback_info(self, mock_cmd, mock_idle, client):
        """When playing, status should include position/duration/paused/volume."""
        picast_receiver._current_video = {
            "title": "Test Video",
            "url": "https://example.com/test",
            "started_at": 1234567890,
        }

        def side_effect(cmd):
            prop = cmd[1]
            return {
                "time-pos": {"data": 45.2, "error": "success"},
                "duration": {"data": 3600.0, "error": "success"},
                "pause": {"data": False, "error": "success"},
                "volume": {"data": 80.0, "error": "success"},
            }.get(prop)

        mock_cmd.side_effect = side_effect

        resp = client.get("/api/status")
        data = resp.get_json()
        assert data["idle"] is False
        assert data["title"] == "Test Video"
        assert data["position"] == 45.2
        assert data["duration"] == 3600.0
        assert data["paused"] is False
        assert data["volume"] == 80.0

        # Restore
        picast_receiver._current_video = {}

    @patch("picast_receiver._is_idle", return_value=False)
    @patch("picast_receiver._mpv_command", return_value=None)
    def test_status_graceful_ipc_failure(self, mock_cmd, mock_idle, client):
        """Status should not crash if mpv IPC fails — just omit playback fields."""
        picast_receiver._current_video = {
            "title": "Test", "url": "https://example.com", "started_at": 0,
        }
        resp = client.get("/api/status")
        data = resp.get_json()
        assert data["idle"] is False
        assert data["title"] == "Test"
        assert "position" not in data
        assert "duration" not in data

        picast_receiver._current_video = {}


class TestVolume:
    @patch("picast_receiver._is_idle", return_value=False)
    @patch("picast_receiver._mpv_command", return_value={"error": "success"})
    def test_volume_success(self, mock_cmd, mock_idle, client):
        resp = client.post("/api/volume", json={"level": 50})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["level"] == 50

    @patch("picast_receiver._is_idle", return_value=False)
    @patch("picast_receiver._mpv_command", return_value=None)
    def test_volume_ipc_failure(self, mock_cmd, mock_idle, client):
        resp = client.post("/api/volume", json={"level": 50})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is False


class TestStop:
    @patch("picast_receiver._stop_playback")
    def test_stop(self, mock_stop, client):
        resp = client.post("/api/stop")
        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True
        mock_stop.assert_called_once()


class TestMpvCommand:
    @patch("socket.socket")
    def test_mpv_command_success(self, mock_sock_cls):
        mock_sock = MagicMock()
        mock_sock_cls.return_value = mock_sock
        mock_sock.recv.return_value = b'{"data": 45.2, "error": "success"}\n'

        result = picast_receiver._mpv_command(["get_property", "time-pos"])
        assert result["data"] == 45.2

    @patch("socket.socket")
    def test_mpv_command_connection_error(self, mock_sock_cls):
        mock_sock = MagicMock()
        mock_sock_cls.return_value = mock_sock
        mock_sock.connect.side_effect = ConnectionRefusedError("No socket")

        result = picast_receiver._mpv_command(["get_property", "time-pos"])
        assert result is None
