"""Twitch source handler using streamlink."""

import logging
import shutil
import subprocess

from picast.server.sources.base import SourceHandler, SourceItem

logger = logging.getLogger(__name__)


class TwitchSource(SourceHandler):
    """Handler for Twitch streams using streamlink.

    Requires streamlink to be installed: pip install streamlink
    """

    source_type = "twitch"

    def __init__(self, quality: str = "best"):
        self.quality = quality
        self._streamlink_available = shutil.which("streamlink") is not None

    def matches(self, url: str) -> bool:
        return "twitch.tv" in url

    def get_metadata(self, url: str) -> SourceItem | None:
        """Get stream info via streamlink."""
        if not self._streamlink_available:
            # Extract channel name from URL as fallback
            channel = url.rstrip("/").split("/")[-1]
            return SourceItem(
                url=url,
                title=f"{channel} (Twitch)",
                source_type="twitch",
            )

        try:
            result = subprocess.run(
                ["streamlink", "--json", url],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if result.returncode == 0:
                import json
                data = json.loads(result.stdout)
                title = data.get("metadata", {}).get("title", "")
                author = data.get("metadata", {}).get("author", "")
                display = f"{author} - {title}" if author and title else title or author
                channel = url.rstrip("/").split("/")[-1]
                return SourceItem(
                    url=url,
                    title=display or f"{channel} (Twitch)",
                    source_type="twitch",
                )
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as e:
            logger.warning("streamlink metadata failed: %s", e)

        channel = url.rstrip("/").split("/")[-1]
        return SourceItem(
            url=url,
            title=f"{channel} (Twitch)",
            source_type="twitch",
        )

    def get_mpv_args(self, url: str) -> list[str]:
        """Twitch streams need special handling via streamlink or yt-dlp."""
        return []

    def get_stream_url(self, url: str) -> str | None:
        """Resolve Twitch URL to a stream URL via streamlink."""
        if not self._streamlink_available:
            return url  # Let mpv/yt-dlp try directly

        try:
            result = subprocess.run(
                ["streamlink", "--stream-url", url, self.quality],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as e:
            logger.warning("streamlink URL resolution failed: %s", e)

        return url  # Fallback
