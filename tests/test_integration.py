"""Integration tests for PiCast v1.0.0 features.

Tests end-to-end flows across block metadata, PiPulse integration,
setup status, pool enrichment, and graceful degradation.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from picast.config import AutoplayConfig, PipulseConfig, ServerConfig
from picast.server.app import create_app


@pytest.fixture
def fresh_app(tmp_path):
    """App with zero optional config — simulates first boot."""
    config = ServerConfig(
        db_file=str(tmp_path / "test.db"),
        data_dir=str(tmp_path),
    )
    app = create_app(config)
    app.player.stop()
    app.config["TESTING"] = True
    return app


@pytest.fixture
def fresh_client(fresh_app):
    return fresh_app.test_client()


@pytest.fixture
def configured_app(tmp_path):
    """App with all optional features configured."""
    config = ServerConfig(
        db_file=str(tmp_path / "test.db"),
        data_dir=str(tmp_path),
        ytdl_cookies_from_browser="chromium",
    )
    pipulse = PipulseConfig(enabled=True, host="10.0.0.103", port=5055)
    autoplay = AutoplayConfig(enabled=True, pool_mode=True)
    app = create_app(config, autoplay_config=autoplay, pipulse_config=pipulse)
    app.player.stop()
    app.config["TESTING"] = True
    return app


@pytest.fixture
def configured_client(configured_app):
    return configured_app.test_client()


# --- Health & Basics ---


class TestHealthEndpoint:
    def test_returns_ok(self, fresh_client):
        resp = fresh_client.get("/api/health")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["status"] == "ok"

    def test_returns_version(self, fresh_client):
        from picast.__about__ import __version__

        resp = fresh_client.get("/api/health")
        data = resp.get_json()
        assert data["version"] == __version__

    def test_includes_queue_stats(self, fresh_client):
        resp = fresh_client.get("/api/health")
        data = resp.get_json()
        assert "queue_pending" in data
        assert "queue_total" in data


class TestFreshBootGraceful:
    """A fresh install with no optional config should work without errors."""

    def test_home_page_loads(self, fresh_client):
        resp = fresh_client.get("/")
        assert resp.status_code == 200

    def test_pool_page_loads(self, fresh_client):
        resp = fresh_client.get("/pool")
        assert resp.status_code == 200

    def test_settings_page_loads(self, fresh_client):
        resp = fresh_client.get("/settings")
        assert resp.status_code == 200

    def test_pool_summary_empty(self, fresh_client):
        resp = fresh_client.get("/api/autoplay/pool")
        assert resp.status_code == 200
        assert resp.get_json() == []

    def test_block_metadata_empty(self, fresh_client):
        resp = fresh_client.get("/api/settings/blocks")
        assert resp.status_code == 200
        assert resp.get_json() == []


# --- Setup Status ---


class TestSetupStatus:
    def test_fresh_all_unconfigured(self, fresh_client):
        resp = fresh_client.get("/api/settings/setup-status")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["pushover"]["configured"] is False
        assert data["youtube"]["configured"] is False
        assert data["pipulse"]["configured"] is False

    def test_youtube_detected_from_config(self, configured_client):
        resp = configured_client.get("/api/settings/setup-status")
        data = resp.get_json()
        assert data["youtube"]["configured"] is True
        assert data["youtube"]["method"] == "cookies"

    def test_pipulse_auto_configured_from_config(self, configured_client):
        """PipulseConfig(enabled=True) auto-sets DB flag at app creation."""
        resp = configured_client.get("/api/settings/setup-status")
        data = resp.get_json()
        assert data["pipulse"]["configured"] is True

    def test_pipulse_not_configured_on_fresh_app(self, fresh_client):
        """Fresh app without PiPulse config shows as unconfigured."""
        resp = fresh_client.get("/api/settings/setup-status")
        data = resp.get_json()
        assert data["pipulse"]["configured"] is False


# --- Block Metadata CRUD ---


class TestBlockMetadataCRUD:
    def test_create_block(self, fresh_client):
        resp = fresh_client.post(
            "/api/settings/blocks",
            json={"block_name": "morning", "display_name": "Morning Foundation", "emoji": "sunrise"},
        )
        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True

    def test_read_blocks(self, fresh_client):
        fresh_client.post(
            "/api/settings/blocks",
            json={"block_name": "morning", "display_name": "Morning"},
        )
        resp = fresh_client.get("/api/settings/blocks")
        blocks = resp.get_json()
        assert len(blocks) == 1
        assert blocks[0]["block_name"] == "morning"

    def test_update_block(self, fresh_client):
        fresh_client.post(
            "/api/settings/blocks",
            json={"block_name": "morning", "display_name": "Morning v1"},
        )
        fresh_client.post(
            "/api/settings/blocks",
            json={"block_name": "morning", "display_name": "Morning v2", "emoji": "sun"},
        )
        resp = fresh_client.get("/api/settings/blocks")
        blocks = resp.get_json()
        assert len(blocks) == 1
        assert blocks[0]["display_name"] == "Morning v2"
        assert blocks[0]["emoji"] == "sun"

    def test_delete_block(self, fresh_client):
        fresh_client.post(
            "/api/settings/blocks",
            json={"block_name": "morning", "display_name": "Morning"},
        )
        resp = fresh_client.delete("/api/settings/blocks/morning")
        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True
        # Verify gone
        resp = fresh_client.get("/api/settings/blocks")
        assert resp.get_json() == []

    def test_delete_nonexistent_404(self, fresh_client):
        resp = fresh_client.delete("/api/settings/blocks/nope")
        assert resp.status_code == 404

    def test_create_requires_block_name(self, fresh_client):
        resp = fresh_client.post(
            "/api/settings/blocks",
            json={"display_name": "No Name"},
        )
        assert resp.status_code == 400

    def test_defaults_display_name_to_block_name(self, fresh_client):
        fresh_client.post(
            "/api/settings/blocks",
            json={"block_name": "deep-work"},
        )
        resp = fresh_client.get("/api/settings/blocks")
        blocks = resp.get_json()
        assert blocks[0]["display_name"] == "deep-work"


# --- PiPulse Integration ---


class TestPipulseSettings:
    def test_get_default_settings(self, fresh_client):
        resp = fresh_client.get("/api/settings/pipulse")
        data = resp.get_json()
        assert data["enabled"] is False
        assert data["host"] == "10.0.0.103"
        assert data["port"] == 5055

    def test_update_settings(self, fresh_client):
        fresh_client.post(
            "/api/settings/pipulse",
            json={"enabled": True, "host": "192.168.1.50", "port": 8080},
        )
        resp = fresh_client.get("/api/settings/pipulse")
        data = resp.get_json()
        assert data["enabled"] is True
        assert data["host"] == "192.168.1.50"
        assert data["port"] == 8080


class TestPipulseImport:
    @patch("picast.server.pipulse_client.urllib.request.urlopen")
    def test_successful_import(self, mock_urlopen, fresh_client):
        blocks_data = {
            "blocks": {
                "morning": {"display_name": "Morning Foundation", "emoji": "sunrise"},
                "evening": {"display_name": "Evening Wind-Down", "emoji": "moon"},
            }
        }
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(blocks_data).encode()
        mock_urlopen.return_value = mock_resp

        resp = fresh_client.post("/api/settings/blocks/import")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["imported"] == 2

        # Verify blocks are in DB
        resp = fresh_client.get("/api/settings/blocks")
        blocks = resp.get_json()
        names = {b["block_name"] for b in blocks}
        assert "morning" in names
        assert "evening" in names

    @patch("picast.server.pipulse_client.urllib.request.urlopen")
    def test_pipulse_down_returns_502(self, mock_urlopen, fresh_client):
        mock_urlopen.side_effect = ConnectionRefusedError("refused")
        resp = fresh_client.post("/api/settings/blocks/import")
        assert resp.status_code == 502
        assert "Failed" in resp.get_json()["error"]


# --- Pool Enrichment ---


class TestPoolEnrichment:
    def test_pool_summary_includes_metadata(self, fresh_client):
        """When block metadata exists, pool summary merges it in."""
        # Create block metadata
        fresh_client.post(
            "/api/settings/blocks",
            json={
                "block_name": "focus",
                "display_name": "Deep Focus",
                "emoji": "brain",
                "tagline": "Enter the zone",
            },
        )
        # Add a video to the focus pool
        fresh_client.post(
            "/api/autoplay/pool/focus",
            json={"url": "https://www.youtube.com/watch?v=aaaaaaaaaaa", "title": "Lofi beats"},
        )

        resp = fresh_client.get("/api/autoplay/pool")
        blocks = resp.get_json()
        assert len(blocks) == 1
        assert blocks[0]["block_name"] == "focus"
        assert blocks[0]["display_name"] == "Deep Focus"
        assert blocks[0]["emoji"] == "brain"
        assert blocks[0]["tagline"] == "Enter the zone"

    def test_pool_summary_without_metadata(self, fresh_client):
        """Pool works even without block metadata — fields are empty strings."""
        fresh_client.post(
            "/api/autoplay/pool/orphan",
            json={"url": "https://www.youtube.com/watch?v=bbbbbbbbbbb", "title": "Test"},
        )
        resp = fresh_client.get("/api/autoplay/pool")
        blocks = resp.get_json()
        assert len(blocks) == 1
        assert blocks[0]["block_name"] == "orphan"
        assert blocks[0]["display_name"] == ""
        assert blocks[0]["emoji"] == ""


# --- Edge Cases ---


class TestEdgeCases:
    def test_autoplay_trigger_no_pool(self, fresh_client):
        """Triggering autoplay on empty pool returns graceful error."""
        resp = fresh_client.post(
            "/api/autoplay/trigger",
            json={"block_name": "nonexistent", "display_name": "Nonexistent"},
        )
        # Should not crash — returns error or empty state
        assert resp.status_code in (200, 404)

    def test_settings_blocks_empty_body(self, fresh_client):
        """POST to blocks with empty JSON returns 400."""
        resp = fresh_client.post(
            "/api/settings/blocks",
            json={},
        )
        assert resp.status_code == 400

    def test_multiple_block_metadata_sources(self, fresh_client):
        """Manual and imported metadata coexist."""
        # Manual entry
        fresh_client.post(
            "/api/settings/blocks",
            json={"block_name": "manual-block", "display_name": "Manual"},
        )
        # Simulate PiPulse import
        with patch("picast.server.pipulse_client.urllib.request.urlopen") as mock:
            blocks_data = {
                "blocks": {
                    "imported-block": {"display_name": "Imported", "emoji": "star"},
                }
            }
            mock_resp = MagicMock()
            mock_resp.read.return_value = json.dumps(blocks_data).encode()
            mock.return_value = mock_resp
            fresh_client.post("/api/settings/blocks/import")

        resp = fresh_client.get("/api/settings/blocks")
        blocks = resp.get_json()
        names = {b["block_name"] for b in blocks}
        assert "manual-block" in names
        assert "imported-block" in names
        assert len(blocks) == 2

    def test_health_endpoint_always_available(self, fresh_client):
        """Health endpoint works regardless of config state."""
        resp = fresh_client.get("/api/health")
        assert resp.status_code == 200
        assert resp.get_json()["status"] == "ok"

    def test_autoplay_export_empty(self, fresh_client):
        """Export with no pools returns empty structure."""
        resp = fresh_client.get("/api/autoplay/export")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "blocks" in data

    def test_block_metadata_survives_pool_operations(self, fresh_client):
        """Block metadata persists through pool add/remove cycles."""
        # Create metadata
        fresh_client.post(
            "/api/settings/blocks",
            json={"block_name": "test-block", "display_name": "Test Block"},
        )
        # Add and remove a pool video
        fresh_client.post(
            "/api/autoplay/pool/test-block",
            json={"url": "https://www.youtube.com/watch?v=ccccccccccc"},
        )
        fresh_client.delete("/api/autoplay/pool/test-block/ccccccccccc")

        # Metadata still exists
        resp = fresh_client.get("/api/settings/blocks")
        blocks = resp.get_json()
        assert any(b["block_name"] == "test-block" for b in blocks)
