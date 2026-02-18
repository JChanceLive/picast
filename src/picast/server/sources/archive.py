"""Internet Archive (archive.org) source handler."""

import logging
import subprocess
from urllib.parse import urlparse

from picast.server.sources.base import SourceHandler, SourceItem

logger = logging.getLogger(__name__)


class ArchiveSource(SourceHandler):
    """Handler for Internet Archive URLs using yt-dlp.

    Supports videos from archive.org/details/... pages.
    No DRM — all content is freely streamable.
    """

    source_type = "archive"

    def matches(self, url: str) -> bool:
        return "archive.org" in url

    def validate(self, url: str) -> tuple[bool, str]:
        """Validate Archive.org URL format."""
        parsed = urlparse(url)
        host = parsed.hostname or ""
        if "archive.org" not in host:
            return False, f"Not an Archive.org URL: {host}"
        # Must have a /details/ path for playable items
        if "/details/" not in parsed.path and "/embed/" not in parsed.path:
            return False, (
                "Archive.org URL must be a /details/ or /embed/ page "
                "(e.g. https://archive.org/details/some-video)"
            )
        return True, ""

    def get_metadata(self, url: str) -> SourceItem | None:
        """Get video metadata via yt-dlp."""
        try:
            result = subprocess.run(
                [
                    "yt-dlp",
                    "--no-warnings",
                    "--no-download",
                    "--print", "%(title)s\t%(duration)s\t%(thumbnail)s",
                    url,
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0 and result.stdout.strip():
                # yt-dlp may return multiple lines for multi-file items;
                # take the first line as the primary item
                line = result.stdout.strip().split("\n")[0]
                parts = line.split("\t")
                title = parts[0] if len(parts) > 0 else ""
                duration = (
                    float(parts[1])
                    if len(parts) > 1 and parts[1] not in ("NA", "")
                    else 0
                )
                thumbnail = parts[2] if len(parts) > 2 else ""
                return SourceItem(
                    url=url,
                    title=title,
                    source_type="archive",
                    duration=duration,
                    thumbnail=thumbnail,
                )
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as e:
            logger.warning("yt-dlp metadata fetch failed for archive.org: %s", e)
        return None

    def get_mpv_args(self, url: str) -> list[str]:
        return []
