"""Tests for TUI components.

Tests the API client logic and widget helpers without requiring
a running server or terminal.
"""

import pytest

from picast.tui.api_client import PiCastClient, PiCastAPIError
from picast.tui.widgets.now_playing import _format_time


class TestFormatTime:
    def test_zero(self):
        assert _format_time(0) == "0:00"

    def test_seconds(self):
        assert _format_time(45) == "0:45"

    def test_minutes(self):
        assert _format_time(125) == "2:05"

    def test_hours(self):
        assert _format_time(3661) == "1:01:01"

    def test_negative(self):
        assert _format_time(-5) == "0:00"

    def test_float(self):
        assert _format_time(90.7) == "1:30"


class TestPiCastClient:
    def test_base_url(self):
        client = PiCastClient("mypi.local", 5000)
        assert client.base_url == "http://mypi.local:5000"
        client.close()

    def test_connection_error(self):
        client = PiCastClient("nonexistent.local", 9999)
        with pytest.raises(PiCastAPIError, match="Cannot connect"):
            client.get_status()
        client.close()
