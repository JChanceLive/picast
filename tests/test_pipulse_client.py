"""Tests for PiPulse API client."""

import json
from unittest.mock import patch, MagicMock

from picast.server.pipulse_client import fetch_block_metadata


class TestFetchBlockMetadata:
    def test_successful_fetch(self):
        """Successful fetch returns block dict."""
        mock_data = {
            "blocks": {
                "morning-foundation": {
                    "display_name": "Morning Foundation",
                    "emoji": "🌅",
                }
            }
        }
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(mock_data).encode()

        with patch("picast.server.pipulse_client.urllib.request.urlopen", return_value=mock_resp):
            result = fetch_block_metadata("10.0.0.103", 5055)

        assert result is not None
        assert "morning-foundation" in result
        assert result["morning-foundation"]["display_name"] == "Morning Foundation"

    def test_timeout_returns_none(self):
        """Timeout returns None without raising."""
        with patch(
            "picast.server.pipulse_client.urllib.request.urlopen",
            side_effect=TimeoutError("timed out"),
        ):
            result = fetch_block_metadata("10.0.0.103", 5055)

        assert result is None

    def test_connection_error_returns_none(self):
        """Connection refused returns None without raising."""
        with patch(
            "picast.server.pipulse_client.urllib.request.urlopen",
            side_effect=ConnectionRefusedError("refused"),
        ):
            result = fetch_block_metadata("10.0.0.103", 5055)

        assert result is None

    def test_malformed_json_returns_none(self):
        """Malformed JSON returns None without raising."""
        mock_resp = MagicMock()
        mock_resp.read.return_value = b"not json"

        with patch("picast.server.pipulse_client.urllib.request.urlopen", return_value=mock_resp):
            result = fetch_block_metadata("10.0.0.103", 5055)

        assert result is None

    def test_missing_blocks_key_returns_empty_dict(self):
        """Response without 'blocks' key returns empty dict."""
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({"other": "data"}).encode()

        with patch("picast.server.pipulse_client.urllib.request.urlopen", return_value=mock_resp):
            result = fetch_block_metadata("10.0.0.103", 5055)

        assert result == {}
