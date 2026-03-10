"""Twitch live stream discovery stub.

Placeholder for future Twitch integration. When implemented, this will
browse Twitch categories matching the user's taste profile to find
ambient live streams (e.g., music, nature cams, art streams).

Not implemented — all methods return empty/False.
"""

import logging

logger = logging.getLogger(__name__)


class TwitchDiscovery:
    """Stub for Twitch live stream browsing. Not implemented."""

    def __init__(self, categories: list[str] | None = None):
        self.categories = categories or []
        self.enabled = False

    def find_live_streams(self, block_name: str) -> list[dict]:
        """Find matching live streams for a block.

        Returns empty list (not implemented).
        """
        return []

    def is_available(self) -> bool:
        """Check if Twitch integration is configured and ready."""
        return False
