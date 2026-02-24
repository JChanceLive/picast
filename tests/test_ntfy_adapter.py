"""Tests for ntfy.sh notification adapter."""

import urllib.request
from unittest.mock import MagicMock, patch

from picast.server.ntfy_adapter import create_ntfy_send_fn


class TestNtfyAdapter:
    """Test create_ntfy_send_fn routing and behavior."""

    @patch("picast.server.ntfy_adapter.urllib.request.urlopen")
    def test_alert_routes_to_alert_topic(self, mock_urlopen):
        mock_urlopen.return_value.__enter__ = MagicMock(return_value=MagicMock(read=MagicMock(return_value=b"")))
        mock_urlopen.return_value.__exit__ = MagicMock(return_value=False)

        send_fn = create_ntfy_send_fn("http://localhost:5555")
        send_fn(0, "⚠️ PiCast SD Card Alert\n\n3 disk I/O errors in the last hour.")

        req = mock_urlopen.call_args[0][0]
        assert req.full_url == "http://localhost:5555/picast-alerts"
        assert req.get_header("Priority") == "4"
        assert req.get_header("Tags") == "warning"

    @patch("picast.server.ntfy_adapter.urllib.request.urlopen")
    def test_summary_routes_to_summary_topic(self, mock_urlopen):
        mock_urlopen.return_value.__enter__ = MagicMock(return_value=MagicMock(read=MagicMock(return_value=b"")))
        mock_urlopen.return_value.__exit__ = MagicMock(return_value=False)

        send_fn = create_ntfy_send_fn("http://localhost:5555")
        send_fn(0, "📺 PiCast Daily Summary\n\nWatch time: 2h 30m")

        req = mock_urlopen.call_args[0][0]
        assert req.full_url == "http://localhost:5555/picast-health"
        assert req.get_header("Priority") == "3"
        assert req.get_header("Tags") == "tv"

    @patch("picast.server.ntfy_adapter.urllib.request.urlopen")
    def test_chat_id_is_ignored(self, mock_urlopen):
        mock_urlopen.return_value.__enter__ = MagicMock(return_value=MagicMock(read=MagicMock(return_value=b"")))
        mock_urlopen.return_value.__exit__ = MagicMock(return_value=False)

        send_fn = create_ntfy_send_fn("http://localhost:5555")
        # Different chat_ids should all work the same
        send_fn(12345, "test message")
        send_fn(99999, "test message")
        assert mock_urlopen.call_count == 2

    @patch("picast.server.ntfy_adapter.urllib.request.urlopen")
    def test_custom_topics(self, mock_urlopen):
        mock_urlopen.return_value.__enter__ = MagicMock(return_value=MagicMock(read=MagicMock(return_value=b"")))
        mock_urlopen.return_value.__exit__ = MagicMock(return_value=False)

        send_fn = create_ntfy_send_fn(
            "http://localhost:5555",
            alert_topic="my-alerts",
            summary_topic="my-summary",
        )
        send_fn(0, "⚠️ PiCast SD Card Alert\nBad disk")
        req = mock_urlopen.call_args[0][0]
        assert req.full_url == "http://localhost:5555/my-alerts"

    @patch("picast.server.ntfy_adapter.urllib.request.urlopen")
    def test_network_error_logged_not_raised(self, mock_urlopen):
        mock_urlopen.side_effect = urllib.request.URLError("Connection refused")

        send_fn = create_ntfy_send_fn("http://localhost:5555")
        # Should not raise
        send_fn(0, "test message")

    @patch("picast.server.ntfy_adapter.urllib.request.urlopen")
    def test_trailing_slash_handled(self, mock_urlopen):
        mock_urlopen.return_value.__enter__ = MagicMock(return_value=MagicMock(read=MagicMock(return_value=b"")))
        mock_urlopen.return_value.__exit__ = MagicMock(return_value=False)

        send_fn = create_ntfy_send_fn("http://localhost:5555/")
        send_fn(0, "test message")

        req = mock_urlopen.call_args[0][0]
        assert req.full_url == "http://localhost:5555/picast-health"
        assert "//" not in req.full_url.split("://")[1]
