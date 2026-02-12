"""Smoke tests for TUI modules.

Tests import correctness, widget construction, and PiCastClient logic
with mocked HTTP responses. Does NOT test full Textual app rendering.
"""

from unittest.mock import MagicMock, patch

import httpx
import pytest  # noqa: I001

# --- Import Tests ---

class TestTUIImports:
    """Verify all TUI modules can be imported without error."""

    def test_import_api_client(self):
        from picast.tui.api_client import (
            AsyncPiCastClient,
            PiCastAPIError,
            PiCastClient,
        )
        assert PiCastClient is not None
        assert AsyncPiCastClient is not None
        assert PiCastAPIError is not None

    def test_import_app(self):
        from picast.tui.app import PiCastApp
        assert PiCastApp is not None

    def test_import_now_playing(self):
        from picast.tui.widgets.now_playing import NowPlaying, _format_time
        assert NowPlaying is not None
        assert _format_time is not None

    def test_import_queue_list(self):
        from picast.tui.widgets.queue_list import QueueList
        assert QueueList is not None

    def test_import_library_list(self):
        from picast.tui.widgets.library_list import LibraryList
        assert LibraryList is not None

    def test_import_playlist_list(self):
        from picast.tui.widgets.playlist_list import PlaylistList
        assert PlaylistList is not None

    def test_import_header_bar(self):
        from picast.tui.widgets.header_bar import HeaderBar
        assert HeaderBar is not None

    def test_import_controls(self):
        from picast.tui.widgets.controls import ControlsBar
        assert ControlsBar is not None


# --- PiCastClient Unit Tests (mocked HTTP) ---

class TestPiCastClientMocked:
    """Test PiCastClient methods with mocked httpx responses."""

    @pytest.fixture()
    def client(self):
        c = PiCastClient("testhost", 5050)
        yield c
        c.close()

    @pytest.fixture(autouse=True)
    def _import(self):
        global PiCastClient, PiCastAPIError
        from picast.tui.api_client import PiCastAPIError, PiCastClient

    def test_base_url_construction(self):
        c = PiCastClient("mypi.local", 8080)
        assert c.base_url == "http://mypi.local:8080"
        c.close()

    def test_get_status_success(self, client):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"idle": True, "volume": 100}
        mock_resp.raise_for_status = MagicMock()
        with patch.object(client._client, "get", return_value=mock_resp):
            result = client.get_status()
        assert result == {"idle": True, "volume": 100}

    def test_get_queue_success(self, client):
        mock_resp = MagicMock()
        mock_resp.json.return_value = [{"id": 1, "url": "http://yt/a"}]
        mock_resp.raise_for_status = MagicMock()
        with patch.object(client._client, "get", return_value=mock_resp):
            result = client.get_queue()
        assert len(result) == 1
        assert result[0]["id"] == 1

    def test_add_to_queue_success(self, client):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"id": 5, "url": "http://yt/b"}
        mock_resp.raise_for_status = MagicMock()
        with patch.object(client._client, "post", return_value=mock_resp):
            result = client.add_to_queue("http://yt/b")
        assert result["id"] == 5

    def test_pause_success(self, client):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"ok": True}
        mock_resp.raise_for_status = MagicMock()
        with patch.object(client._client, "post", return_value=mock_resp):
            result = client.pause()
        assert result["ok"] is True

    def test_set_volume_success(self, client):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"volume": 75}
        mock_resp.raise_for_status = MagicMock()
        with patch.object(client._client, "post", return_value=mock_resp):
            result = client.set_volume(75)
        assert result["volume"] == 75

    def test_remove_from_queue_success(self, client):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"ok": True}
        mock_resp.raise_for_status = MagicMock()
        with patch.object(client._client, "delete", return_value=mock_resp):
            result = client.remove_from_queue(3)
        assert result["ok"] is True

    def test_connect_error_raises(self, client):
        from picast.tui.api_client import PiCastAPIError
        with patch.object(
            client._client, "get", side_effect=httpx.ConnectError("refused"),
        ):
            with pytest.raises(PiCastAPIError, match="Cannot connect"):
                client.get_status()

    def test_timeout_error_raises(self, client):
        from picast.tui.api_client import PiCastAPIError
        with patch.object(
            client._client, "get", side_effect=httpx.ReadTimeout("timeout"),
        ):
            with pytest.raises(PiCastAPIError, match="timed out"):
                client.get_status()

    def test_http_status_error_raises(self, client):
        from picast.tui.api_client import PiCastAPIError
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        error = httpx.HTTPStatusError(
            "Server Error", request=MagicMock(), response=mock_resp,
        )
        with patch.object(client._client, "get", side_effect=error):
            with pytest.raises(PiCastAPIError) as exc_info:
                client.get_status()
            assert exc_info.value.status_code == 500

    def test_get_library_success(self, client):
        mock_resp = MagicMock()
        mock_resp.json.return_value = [
            {"id": 1, "title": "Test", "url": "http://yt/a"},
        ]
        mock_resp.raise_for_status = MagicMock()
        with patch.object(client._client, "get", return_value=mock_resp):
            result = client.get_library(sort="title", limit=10)
        assert len(result) == 1

    def test_create_playlist_success(self, client):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"id": 1, "name": "My List"}
        mock_resp.raise_for_status = MagicMock()
        with patch.object(client._client, "post", return_value=mock_resp):
            result = client.create_playlist("My List")
        assert result["name"] == "My List"

    def test_post_connect_error_raises(self, client):
        from picast.tui.api_client import PiCastAPIError
        with patch.object(
            client._client, "post", side_effect=httpx.ConnectError("refused"),
        ):
            with pytest.raises(PiCastAPIError, match="Cannot connect"):
                client.pause()

    def test_delete_connect_error_raises(self, client):
        from picast.tui.api_client import PiCastAPIError
        with patch.object(
            client._client, "delete",
            side_effect=httpx.ConnectError("refused"),
        ):
            with pytest.raises(PiCastAPIError, match="Cannot connect"):
                client.remove_from_queue(1)


# --- Utility Function Tests ---

class TestFormatTimeExtended:
    """Extended tests for _format_time (beyond test_tui.py)."""

    def test_exact_minute(self):
        from picast.tui.widgets.now_playing import _format_time
        assert _format_time(60) == "1:00"

    def test_exact_hour(self):
        from picast.tui.widgets.now_playing import _format_time
        assert _format_time(3600) == "1:00:00"

    def test_large_value(self):
        from picast.tui.widgets.now_playing import _format_time
        # 2h 30m 45s
        assert _format_time(9045) == "2:30:45"

    def test_just_under_hour(self):
        from picast.tui.widgets.now_playing import _format_time
        assert _format_time(3599) == "59:59"


# --- PiCastAPIError Tests ---

class TestPiCastAPIError:
    def test_message(self):
        from picast.tui.api_client import PiCastAPIError
        err = PiCastAPIError("test error")
        assert str(err) == "test error"
        assert err.status_code is None

    def test_with_status_code(self):
        from picast.tui.api_client import PiCastAPIError
        err = PiCastAPIError("not found", status_code=404)
        assert err.status_code == 404
        assert "not found" in str(err)
