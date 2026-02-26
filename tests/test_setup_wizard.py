"""Tests for the picast-setup interactive wizard."""

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from picast.setup_wizard import (
    _load_toml,
    _merge_section,
    _toml_value,
    _write_toml,
    detect_chromium_cookies,
    fetch_pipulse_blocks,
    check_pipulse_connection,
    validate_pushover,
)


class TestTomlValue:
    def test_bool_true(self):
        assert _toml_value(True) == "true"

    def test_bool_false(self):
        assert _toml_value(False) == "false"

    def test_int(self):
        assert _toml_value(5050) == "5050"

    def test_float(self):
        assert _toml_value(5.0) == "5.0"

    def test_string(self):
        assert _toml_value("hello") == '"hello"'

    def test_list(self):
        assert _toml_value([1, 2, 3]) == "[1, 2, 3]"

    def test_list_of_strings(self):
        assert _toml_value(["a", "b"]) == '["a", "b"]'


class TestMergeSection:
    def test_merge_new_section(self):
        config = {}
        _merge_section(config, "pushover", {"enabled": True, "api_token": "tok"})
        assert config["pushover"]["enabled"] is True
        assert config["pushover"]["api_token"] == "tok"

    def test_merge_existing_section(self):
        config = {"pushover": {"enabled": False, "daily_summary_hour": 8}}
        _merge_section(config, "pushover", {"enabled": True, "api_token": "tok"})
        assert config["pushover"]["enabled"] is True
        assert config["pushover"]["api_token"] == "tok"
        assert config["pushover"]["daily_summary_hour"] == 8

    def test_merge_overwrites(self):
        config = {"server": {"port": 5050}}
        _merge_section(config, "server", {"port": 8080})
        assert config["server"]["port"] == 8080


class TestLoadToml:
    def test_missing_file(self, tmp_path):
        result = _load_toml(tmp_path / "nonexistent.toml")
        assert result == {}

    def test_valid_file(self, tmp_path):
        f = tmp_path / "test.toml"
        f.write_text('[server]\nport = 5050\n')
        result = _load_toml(f)
        assert result["server"]["port"] == 5050


class TestWriteToml:
    def test_basic_roundtrip(self, tmp_path):
        f = tmp_path / "out.toml"
        data = {
            "server": {"host": "0.0.0.0", "port": 5050},
            "pushover": {"enabled": True, "api_token": "tok123"},
        }
        _write_toml(f, data)
        assert f.exists()
        content = f.read_text()
        assert "[server]" in content
        assert 'host = "0.0.0.0"' in content
        assert "port = 5050" in content
        assert "[pushover]" in content
        assert "enabled = true" in content
        assert 'api_token = "tok123"' in content

    def test_creates_parent_dirs(self, tmp_path):
        f = tmp_path / "sub" / "dir" / "picast.toml"
        _write_toml(f, {"server": {"port": 5050}})
        assert f.exists()

    def test_idempotent_rewrite(self, tmp_path):
        f = tmp_path / "test.toml"
        data = {"server": {"port": 5050}}
        _write_toml(f, data)
        content1 = f.read_text()
        _write_toml(f, data)
        content2 = f.read_text()
        assert content1 == content2

    def test_nested_tables(self, tmp_path):
        f = tmp_path / "nested.toml"
        data = {
            "autoplay": {
                "enabled": True,
                "themes": {
                    "focus": {"queries": ["lofi beats"], "max_results": 5}
                },
            }
        }
        _write_toml(f, data)
        content = f.read_text()
        assert "[autoplay]" in content
        assert "enabled = true" in content
        assert "[autoplay.themes.focus]" in content
        assert 'queries = ["lofi beats"]' in content


class TestValidatePushover:
    @patch("picast.setup_wizard.urllib.request.urlopen")
    def test_success(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"status": 1}).encode()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        ok, msg = validate_pushover("tok", "key")
        assert ok is True
        assert "sent" in msg.lower()

    @patch("picast.setup_wizard.urllib.request.urlopen")
    def test_invalid_token(self, mock_urlopen):
        import urllib.error
        error_body = json.dumps({"errors": ["invalid token"]}).encode()
        mock_urlopen.side_effect = urllib.error.HTTPError(
            "url", 401, "Unauthorized", {}, MagicMock(read=MagicMock(return_value=error_body))
        )

        ok, msg = validate_pushover("bad", "key")
        assert ok is False
        assert "invalid" in msg.lower() or "credentials" in msg.lower()

    @patch("picast.setup_wizard.urllib.request.urlopen")
    def test_connection_error(self, mock_urlopen):
        import urllib.error
        mock_urlopen.side_effect = urllib.error.URLError("network down")

        ok, msg = validate_pushover("tok", "key")
        assert ok is False
        assert "connection" in msg.lower() or "network" in msg.lower()


class TestDetectChromiumCookies:
    def test_no_cookies(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "picast.setup_wizard.CHROMIUM_COOKIE_PATHS",
            [tmp_path / "nonexistent" / "Cookies"],
        )
        assert detect_chromium_cookies() is None

    def test_cookies_exist(self, tmp_path, monkeypatch):
        cookie_file = tmp_path / "chromium" / "Default" / "Cookies"
        cookie_file.parent.mkdir(parents=True)
        cookie_file.write_text("fake")
        monkeypatch.setattr(
            "picast.setup_wizard.CHROMIUM_COOKIE_PATHS",
            [cookie_file],
        )
        assert detect_chromium_cookies() == "chromium"


class TestPipulseConnection:
    @patch("picast.setup_wizard.urllib.request.urlopen")
    def test_success(self, mock_urlopen):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"status": "ok", "version": "1.3.0"}).encode()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        ok, msg = check_pipulse_connection("10.0.0.103", 5055)
        assert ok is True
        assert "1.3.0" in msg

    @patch("picast.setup_wizard.urllib.request.urlopen")
    def test_connection_refused(self, mock_urlopen):
        import urllib.error
        mock_urlopen.side_effect = urllib.error.URLError("Connection refused")

        ok, msg = check_pipulse_connection("10.0.0.103", 5055)
        assert ok is False


class TestFetchPipulseBlocks:
    @patch("picast.setup_wizard.urllib.request.urlopen")
    def test_success(self, mock_urlopen):
        blocks = {
            "blocks": {
                "morning": {"display_name": "Morning Foundation", "emoji": "sunrise"},
            }
        }
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(blocks).encode()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        ok, data = fetch_pipulse_blocks("10.0.0.103", 5055)
        assert ok is True
        assert "morning" in data

    @patch("picast.setup_wizard.urllib.request.urlopen")
    def test_connection_error(self, mock_urlopen):
        import urllib.error
        mock_urlopen.side_effect = urllib.error.URLError("timeout")

        ok, msg = fetch_pipulse_blocks("10.0.0.103", 5055)
        assert ok is False
        assert isinstance(msg, str)


class TestSetupStatusEndpoint:
    """Test the /api/settings/setup-status endpoint via Flask test client."""

    @pytest.fixture
    def client(self, tmp_path):
        from picast.config import AutoplayConfig, PipulseConfig, ServerConfig
        from picast.server.app import create_app

        config = ServerConfig(
            db_file=str(tmp_path / "test.db"),
            data_dir=str(tmp_path),
            ytdl_cookies_from_browser="chromium",
        )
        app = create_app(config, pipulse_config=PipulseConfig())
        app.config["TESTING"] = True
        with app.test_client() as c:
            yield c

    def test_returns_all_features(self, client):
        r = client.get("/api/settings/setup-status")
        assert r.status_code == 200
        d = r.get_json()
        assert "pushover" in d
        assert "youtube" in d
        assert "pipulse" in d

    def test_youtube_detected_from_config(self, client):
        r = client.get("/api/settings/setup-status")
        d = r.get_json()
        assert d["youtube"]["configured"] is True
        assert d["youtube"]["method"] == "cookies"

    def test_pushover_not_configured_by_default(self, client):
        r = client.get("/api/settings/setup-status")
        d = r.get_json()
        assert d["pushover"]["configured"] is False

    def test_pipulse_not_configured_by_default(self, client):
        r = client.get("/api/settings/setup-status")
        d = r.get_json()
        assert d["pipulse"]["configured"] is False
