"""Tests for device discovery and multi-Pi support."""

import os
import tempfile

import pytest

from picast.server.discovery import DeviceInfo, DeviceRegistry, _get_local_ip


# --- DeviceInfo tests ---


class TestDeviceInfo:
    def test_to_dict(self):
        d = DeviceInfo(name="living-room", host="192.168.1.10", port=5000)
        data = d.to_dict()
        assert data["name"] == "living-room"
        assert data["host"] == "192.168.1.10"
        assert data["port"] == 5000
        assert data["source"] == "config"
        assert data["online"] is True

    def test_defaults(self):
        d = DeviceInfo(name="test", host="host", port=5000)
        assert d.source == "config"
        assert d.online is True
        assert d.version == ""


# --- DeviceRegistry tests ---


class TestDeviceRegistry:
    def test_local_registered(self):
        reg = DeviceRegistry(local_name="my-pi", local_port=5000)
        devices = reg.list_devices()
        assert len(devices) == 1
        assert devices[0]["name"] == "my-pi"
        assert devices[0]["source"] == "local"

    def test_add_from_config(self):
        reg = DeviceRegistry(local_name="pi-1")
        reg.add_from_config("pi-2", "192.168.1.20", 5000)
        devices = reg.list_devices()
        assert len(devices) == 2
        names = {d["name"] for d in devices}
        assert "pi-1" in names
        assert "pi-2" in names

    def test_add_discovered(self):
        reg = DeviceRegistry(local_name="pi-1")
        reg.add_discovered("pi-3", "192.168.1.30", 5000, version="0.1.0")
        devices = reg.list_devices()
        assert len(devices) == 2
        pi3 = [d for d in devices if d["name"] == "pi-3"][0]
        assert pi3["source"] == "discovered"
        assert pi3["version"] == "0.1.0"

    def test_discovered_doesnt_overwrite_config(self):
        reg = DeviceRegistry(local_name="pi-1")
        reg.add_from_config("pi-2", "192.168.1.20", 5000)
        reg.add_discovered("pi-2", "192.168.1.99", 5001)
        pi2 = reg.get_device("pi-2")
        # Config entry should be preserved
        assert pi2["host"] == "192.168.1.20"
        assert pi2["source"] == "config"

    def test_remove_discovered(self):
        reg = DeviceRegistry(local_name="pi-1")
        reg.add_discovered("pi-3", "192.168.1.30", 5000)
        # Should be visible
        assert len(reg.list_devices()) == 2
        # Mark offline
        reg.remove_discovered("pi-3")
        # Not visible by default
        assert len(reg.list_devices()) == 1
        # Visible with include_offline
        assert len(reg.list_devices(include_offline=True)) == 2

    def test_remove_doesnt_affect_config(self):
        reg = DeviceRegistry(local_name="pi-1")
        reg.add_from_config("pi-2", "192.168.1.20", 5000)
        reg.remove_discovered("pi-2")
        # Config device should still be visible
        assert len(reg.list_devices()) == 2

    def test_get_device(self):
        reg = DeviceRegistry(local_name="pi-1")
        reg.add_from_config("pi-2", "192.168.1.20", 5000)
        d = reg.get_device("pi-2")
        assert d is not None
        assert d["host"] == "192.168.1.20"

    def test_get_device_not_found(self):
        reg = DeviceRegistry(local_name="pi-1")
        assert reg.get_device("nonexistent") is None

    def test_list_excludes_offline(self):
        reg = DeviceRegistry(local_name="pi-1")
        reg.add_discovered("pi-2", "192.168.1.20", 5000)
        reg.remove_discovered("pi-2")
        online = reg.list_devices(include_offline=False)
        assert all(d["online"] for d in online)

    def test_start_discovery_without_zeroconf(self):
        """Should gracefully handle missing zeroconf."""
        reg = DeviceRegistry(local_name="pi-1")
        # This should not raise even if zeroconf is not installed
        reg.start_discovery()

    def test_stop_discovery_noop(self):
        """Should handle stop when never started."""
        reg = DeviceRegistry(local_name="pi-1")
        reg.stop_discovery()  # Should not raise


# --- API endpoint tests ---


class TestDeviceAPI:
    @pytest.fixture
    def app(self, tmp_path):
        from picast.server.app import create_app
        from picast.config import ServerConfig

        config = ServerConfig(data_dir=str(tmp_path))
        devices = [
            ("living-room", "192.168.1.10", 5000),
            ("bedroom", "192.168.1.11", 5000),
        ]
        app = create_app(config, devices=devices)
        app.config["TESTING"] = True
        return app

    @pytest.fixture
    def client(self, app):
        return app.test_client()

    def test_list_devices(self, client):
        resp = client.get("/api/devices")
        assert resp.status_code == 200
        devices = resp.get_json()
        names = {d["name"] for d in devices}
        assert "living-room" in names
        assert "bedroom" in names

    def test_get_device(self, client):
        resp = client.get("/api/devices/living-room")
        assert resp.status_code == 200
        d = resp.get_json()
        assert d["host"] == "192.168.1.10"

    def test_get_device_not_found(self, client):
        resp = client.get("/api/devices/nonexistent")
        assert resp.status_code == 404

    def test_web_pages_include_devices(self, client):
        """Web pages should render even with devices."""
        for path in ["/", "/history", "/collections"]:
            resp = client.get(path)
            assert resp.status_code == 200


# --- Utility tests ---


class TestUtils:
    def test_get_local_ip(self):
        ip = _get_local_ip()
        assert isinstance(ip, str)
        # Should be a valid IP format
        parts = ip.split(".")
        assert len(parts) == 4
