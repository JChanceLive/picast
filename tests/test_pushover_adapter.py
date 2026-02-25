"""Tests for Pushover notification adapter."""

import urllib.request
from unittest.mock import MagicMock, patch

from picast.server.pushover_adapter import PUSHOVER_API_URL, SoundTier, create_pushover_send_fn


class TestPushoverAdapter:
    """Test create_pushover_send_fn routing and behavior."""

    @patch("picast.server.pushover_adapter.urllib.request.urlopen")
    def test_alert_sends_with_alert_tier(self, mock_urlopen):
        mock_urlopen.return_value.__enter__ = MagicMock(return_value=MagicMock(read=MagicMock(return_value=b"")))
        mock_urlopen.return_value.__exit__ = MagicMock(return_value=False)

        send_fn = create_pushover_send_fn("tok_abc", "user_xyz")
        send_fn(0, "\u26a0\ufe0f PiCast SD Card Alert\n\n3 disk I/O errors in the last hour.")

        req = mock_urlopen.call_args[0][0]
        assert req.full_url == PUSHOVER_API_URL
        body = req.data.decode("utf-8")
        assert "token=tok_abc" in body
        assert "user=user_xyz" in body
        assert "priority=1" in body
        assert "sound=falling" in body
        assert "title=PiCast+SD+Alert" in body

    @patch("picast.server.pushover_adapter.urllib.request.urlopen")
    def test_summary_sends_with_casual_tier(self, mock_urlopen):
        mock_urlopen.return_value.__enter__ = MagicMock(return_value=MagicMock(read=MagicMock(return_value=b"")))
        mock_urlopen.return_value.__exit__ = MagicMock(return_value=False)

        send_fn = create_pushover_send_fn("tok_abc", "user_xyz")
        send_fn(0, "\U0001f4fa PiCast Daily Summary\n\nWatch time: 2h 30m")

        req = mock_urlopen.call_args[0][0]
        body = req.data.decode("utf-8")
        assert "priority=0" in body
        assert "sound=classical" in body
        assert "title=PiCast" in body

    @patch("picast.server.pushover_adapter.urllib.request.urlopen")
    def test_chat_id_is_ignored(self, mock_urlopen):
        mock_urlopen.return_value.__enter__ = MagicMock(return_value=MagicMock(read=MagicMock(return_value=b"")))
        mock_urlopen.return_value.__exit__ = MagicMock(return_value=False)

        send_fn = create_pushover_send_fn("tok_abc", "user_xyz")
        send_fn(12345, "test message")
        send_fn(99999, "test message")
        assert mock_urlopen.call_count == 2

    @patch("picast.server.pushover_adapter.urllib.request.urlopen")
    def test_network_error_logged_not_raised(self, mock_urlopen):
        mock_urlopen.side_effect = urllib.request.URLError("Connection refused")

        send_fn = create_pushover_send_fn("tok_abc", "user_xyz")
        # Should not raise
        send_fn(0, "test message")

    @patch("picast.server.pushover_adapter.urllib.request.urlopen")
    def test_message_body_included(self, mock_urlopen):
        mock_urlopen.return_value.__enter__ = MagicMock(return_value=MagicMock(read=MagicMock(return_value=b"")))
        mock_urlopen.return_value.__exit__ = MagicMock(return_value=False)

        send_fn = create_pushover_send_fn("tok_abc", "user_xyz")
        send_fn(0, "Hello from PiCast")

        req = mock_urlopen.call_args[0][0]
        body = req.data.decode("utf-8")
        assert "message=Hello+from+PiCast" in body


class TestSoundTier:
    """Test SoundTier definitions."""

    def test_alert_tier_uses_falling(self):
        assert SoundTier.ALERT["sound"] == "falling"
        assert SoundTier.ALERT["priority"] == 1

    def test_casual_tier_uses_classical(self):
        assert SoundTier.CASUAL["sound"] == "classical"
        assert SoundTier.CASUAL["priority"] == 0
