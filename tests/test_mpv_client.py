"""Tests for MPVClient.

These tests don't require a running mpv instance - they test the client's
behavior when mpv is not available (connection handling, defaults, etc).
"""

import pytest

from picast.server.mpv_client import MPVClient


class TestMPVClient:
    def test_not_connected_by_default(self):
        client = MPVClient("/tmp/nonexistent-test-socket")
        assert client.connected is False

    def test_connect_fails_gracefully(self):
        client = MPVClient("/tmp/nonexistent-test-socket")
        result = client.connect(timeout=0.1)
        assert result is False
        assert client.connected is False

    def test_get_status_when_disconnected(self):
        client = MPVClient("/tmp/nonexistent-test-socket")
        status = client.get_status()
        assert status["idle"] is True
        assert status["connected"] is False

    def test_get_property_returns_default_when_disconnected(self):
        client = MPVClient("/tmp/nonexistent-test-socket")
        assert client.get_property("volume", 100) == 100
        assert client.get_property("pause", False) is False

    def test_disconnect_when_not_connected(self):
        client = MPVClient("/tmp/nonexistent-test-socket")
        # Should not raise
        client.disconnect()

    def test_pause_returns_false_when_disconnected(self):
        client = MPVClient("/tmp/nonexistent-test-socket")
        assert client.pause() is False

    def test_resume_returns_false_when_disconnected(self):
        client = MPVClient("/tmp/nonexistent-test-socket")
        assert client.resume() is False

    def test_set_volume_clamps(self):
        client = MPVClient("/tmp/nonexistent-test-socket")
        # These won't actually set anything since we're not connected,
        # but they verify the clamping logic doesn't crash
        client.set_volume(-10)
        client.set_volume(200)

    def test_set_speed_clamps(self):
        client = MPVClient("/tmp/nonexistent-test-socket")
        client.set_speed(0.1)
        client.set_speed(10.0)
