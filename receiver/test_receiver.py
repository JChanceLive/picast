"""Tests for PiCast Receiver API routes."""

from unittest.mock import patch

import pytest

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
            "https://youtube.com/watch?v=test123test", "Test Video"
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


class TestStop:
    @patch("picast_receiver._stop_playback")
    def test_stop(self, mock_stop, client):
        resp = client.post("/api/stop")
        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True
        mock_stop.assert_called_once()
