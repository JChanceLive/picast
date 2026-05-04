"""Tests for the PiCast receiver Blueprint.

Validates the v0.9.0 Blueprint factory against an in-memory fake
PlayerAdapter. Response shapes match the v0.8.0 contract that
picast-z1 + the Chrome extension depend on.
"""

from __future__ import annotations

import os
import sys

import pytest
from flask import Flask

# Allow direct test execution from the repo root or the receiver dir.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from blueprint import create_receiver_blueprint  # noqa: E402


# --- Fake player adapter (in-memory, deterministic) ----------------------


class FakePlayer:
    def __init__(self) -> None:
        self.idle = True
        self.played: list[tuple[str, str, bool]] = []
        self.stopped = 0
        self.paused = False
        self.volume = 100
        self.last_url = ""
        self.last_title = ""
        self.last_volume = 100
        self.intentional_stop = False
        self.streamlink_alive_value = True
        self.time_pos: float | None = 0.0
        self.last_stable_since = 0.0
        self.play_succeeds = True
        self.set_volume_succeeds = True
        self._status_extras: dict = {}

    def play(self, url: str, title: str = "", mute: bool = False) -> bool:
        self.played.append((url, title, mute))
        if self.play_succeeds:
            self.idle = False
            self.last_url = url
            self.last_title = title
        return self.play_succeeds

    def stop(self) -> None:
        self.stopped += 1
        self.idle = True

    def pause(self) -> bool:
        self.paused = True
        return True

    def resume(self) -> bool:
        self.paused = False
        return True

    def set_volume(self, level: int) -> bool:
        if self.set_volume_succeeds:
            self.volume = level
            self.last_volume = level
            return True
        return False

    def is_idle(self) -> bool:
        return self.idle

    def status(self) -> dict:
        result = {
            "idle": self.idle,
            "title": self.last_title if not self.idle else "",
            "url": self.last_url if not self.idle else "",
        }
        if not self.idle:
            result["source_type"] = (
                "twitch" if "twitch.tv/" in self.last_url else "youtube"
            )
            result.update(self._status_extras)
        return result

    def reset_intentional_stop(self) -> None:
        self.intentional_stop = False

    def get_time_pos(self) -> float | None:
        return self.time_pos


@pytest.fixture
def player() -> FakePlayer:
    return FakePlayer()


@pytest.fixture
def client(player: FakePlayer):
    app = Flask(__name__)
    app.register_blueprint(create_receiver_blueprint(player, version="0.9.0-test"))
    app.config["TESTING"] = True
    return app.test_client()


# --- /api/health ----------------------------------------------------------


def test_health_returns_ok(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert data["type"] == "receiver"
    assert data["version"] == "0.9.0-test"
    assert "hostname" in data


# --- /api/status ----------------------------------------------------------


def test_status_idle_by_default(client):
    resp = client.get("/api/status")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["idle"] is True
    assert data["title"] == ""
    assert data["url"] == ""
    assert data["autoplay_enabled"] is True


def test_status_includes_playback_info_when_playing(player, client):
    player.idle = False
    player.last_title = "Test Video"
    player.last_url = "https://example.com/test"
    player._status_extras = {
        "position": 45.2, "duration": 3600.0, "paused": False, "volume": 80.0,
    }
    data = client.get("/api/status").get_json()
    assert data["idle"] is False
    assert data["title"] == "Test Video"
    assert data["position"] == 45.2
    assert data["source_type"] == "youtube"


# --- /api/play ------------------------------------------------------------


def test_play_requires_url(client):
    resp = client.post("/api/play", json={})
    assert resp.status_code == 400
    assert "url required" in resp.get_json()["error"]


def test_play_empty_url(client):
    resp = client.post("/api/play", json={"url": ""})
    assert resp.status_code == 400


def test_play_success(player, client):
    resp = client.post("/api/play", json={
        "url": "https://youtube.com/watch?v=test123test",
        "title": "Test Video",
    })
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True
    assert player.played == [
        ("https://youtube.com/watch?v=test123test", "Test Video", False),
    ]


def test_play_failure(player, client):
    player.play_succeeds = False
    resp = client.post("/api/play", json={
        "url": "https://youtube.com/watch?v=test123test",
    })
    assert resp.status_code == 500


def test_play_mute_propagates(player, client):
    client.post("/api/play", json={
        "url": "https://youtube.com/watch?v=x", "mute": True,
    })
    assert player.played[-1][2] is True  # mute=True propagated


# --- /api/queue/add (alias for play) -------------------------------------


def test_queue_add_requires_url(client):
    resp = client.post("/api/queue/add", json={})
    assert resp.status_code == 400


def test_queue_add_plays_immediately(player, client):
    resp = client.post("/api/queue/add", json={
        "url": "https://youtube.com/watch?v=fleet",
        "title": "Fleet Push",
    })
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True
    # queue/add must NOT carry mute=True (it's not a mute API)
    assert player.played[-1] == (
        "https://youtube.com/watch?v=fleet", "Fleet Push", False,
    )


# --- /api/pause + /api/resume --------------------------------------------


def test_pause_when_idle(client):
    resp = client.post("/api/pause")
    assert resp.get_json()["ok"] is False
    assert "not playing" in resp.get_json()["error"]


def test_pause_success(player, client):
    player.idle = False
    resp = client.post("/api/pause")
    assert resp.get_json()["ok"] is True
    assert player.paused is True


def test_resume_when_idle(client):
    resp = client.post("/api/resume")
    assert resp.get_json()["ok"] is False


def test_resume_success(player, client):
    player.idle = False
    player.paused = True
    resp = client.post("/api/resume")
    assert resp.get_json()["ok"] is True
    assert player.paused is False


# --- /api/volume ----------------------------------------------------------


def test_volume_when_idle(client):
    resp = client.post("/api/volume", json={"level": 50})
    data = resp.get_json()
    assert data["ok"] is False
    assert "not playing" in data["error"]


def test_volume_success(player, client):
    player.idle = False
    resp = client.post("/api/volume", json={"level": 50})
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] is True
    assert data["level"] == 50
    assert player.volume == 50


def test_volume_clamped(player, client):
    player.idle = False
    client.post("/api/volume", json={"level": 250})
    assert player.volume == 100  # clamped to upper bound
    client.post("/api/volume", json={"level": -10})
    assert player.volume == 0   # clamped to lower bound


def test_volume_ipc_failure(player, client):
    player.idle = False
    player.set_volume_succeeds = False
    resp = client.post("/api/volume", json={"level": 50})
    assert resp.get_json()["ok"] is False


# --- /api/stop ------------------------------------------------------------


def test_stop_calls_player(player, client):
    resp = client.post("/api/stop")
    assert resp.status_code == 200
    assert resp.get_json()["ok"] is True
    assert player.stopped == 1


# --- /api/watchdog --------------------------------------------------------


def test_watchdog_status_no_watchdog_configured(player, client):
    """Without a watchdog, status returns a stub with enabled=False."""
    data = client.get("/api/watchdog").get_json()
    assert data["enabled"] is False
    assert data["retry_count"] == 0


def test_watchdog_toggle_no_watchdog_configured(client):
    resp = client.post("/api/watchdog", json={"enabled": False})
    assert resp.get_json()["ok"] is False


def test_watchdog_status_with_watchdog():
    """When configured, status returns the watchdog's actual state."""
    p = FakePlayer()

    class FakeWatchdog:
        enabled = True

        def status(self) -> dict:
            return {
                "enabled": True,
                "retry_count": 2,
                "max_retries": 5,
                "last_url": "https://example.com",
                "last_drop_time": 12345.0,
                "stall_count": 1,
            }

        def set_enabled(self, v: bool) -> None:
            FakeWatchdog.enabled = v

    wd = FakeWatchdog()
    app = Flask(__name__)
    app.register_blueprint(create_receiver_blueprint(p, watchdog=wd))
    client = app.test_client()
    data = client.get("/api/watchdog").get_json()
    assert data["retry_count"] == 2
    resp = client.post("/api/watchdog", json={"enabled": False})
    assert resp.get_json()["ok"] is True
    assert resp.get_json()["enabled"] is False


# --- Standalone CLI app shape (regression for picast-z1) ------------------


def test_standalone_app_imports():
    """The CLI wrapper must still import cleanly from the receiver dir."""
    import importlib

    mod = importlib.import_module("picast_receiver")
    assert hasattr(mod, "main")
    assert mod.__version__.startswith("0.9.")
