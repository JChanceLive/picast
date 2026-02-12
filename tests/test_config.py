"""Tests for configuration loading and auth helpers."""

from picast.config import (
    ServerConfig,
    _parse_config,
    ytdl_auth_args,
    ytdl_raw_options_auth,
)


class TestServerConfigAuth:
    def test_default_auth_fields(self):
        config = ServerConfig()
        assert config.ytdl_cookies_from_browser == ""
        assert config.ytdl_po_token == ""

    def test_parse_cookies_from_browser(self):
        data = {"server": {"ytdl_cookies_from_browser": "chromium"}}
        config = _parse_config(data)
        assert config.server.ytdl_cookies_from_browser == "chromium"

    def test_parse_po_token(self):
        data = {"server": {"ytdl_po_token": "abc123"}}
        config = _parse_config(data)
        assert config.server.ytdl_po_token == "abc123"

    def test_parse_both_fields(self):
        data = {"server": {
            "ytdl_cookies_from_browser": "firefox",
            "ytdl_po_token": "tok123",
        }}
        config = _parse_config(data)
        assert config.server.ytdl_cookies_from_browser == "firefox"
        assert config.server.ytdl_po_token == "tok123"


class TestYtdlAuthArgs:
    def test_no_auth(self):
        config = ServerConfig()
        assert ytdl_auth_args(config) == []

    def test_cookies_from_browser(self):
        config = ServerConfig(ytdl_cookies_from_browser="chromium")
        args = ytdl_auth_args(config)
        assert args == ["--cookies-from-browser=chromium"]

    def test_po_token(self):
        config = ServerConfig(ytdl_po_token="mytoken")
        args = ytdl_auth_args(config)
        assert args == ["--extractor-args", "youtube:player-client=web;po_token=mytoken"]

    def test_cookies_takes_priority(self):
        """When both are set, cookies_from_browser wins."""
        config = ServerConfig(
            ytdl_cookies_from_browser="chromium",
            ytdl_po_token="mytoken",
        )
        args = ytdl_auth_args(config)
        assert args == ["--cookies-from-browser=chromium"]


class TestYtdlRawOptionsAuth:
    def test_no_auth(self):
        config = ServerConfig()
        assert ytdl_raw_options_auth(config) == ""

    def test_cookies_from_browser(self):
        config = ServerConfig(ytdl_cookies_from_browser="chromium")
        result = ytdl_raw_options_auth(config)
        assert result == "cookies-from-browser=chromium"

    def test_po_token(self):
        config = ServerConfig(ytdl_po_token="tok123")
        result = ytdl_raw_options_auth(config)
        assert result == "extractor-args=youtube:player-client=web;po_token=tok123"

    def test_cookies_takes_priority(self):
        config = ServerConfig(
            ytdl_cookies_from_browser="firefox",
            ytdl_po_token="tok123",
        )
        result = ytdl_raw_options_auth(config)
        assert result == "cookies-from-browser=firefox"
