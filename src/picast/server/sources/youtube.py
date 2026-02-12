"""YouTube source handler."""

from __future__ import annotations

import logging
import subprocess
from urllib.parse import parse_qs, urlparse

from picast.server.sources.base import SourceHandler, SourceItem

logger = logging.getLogger(__name__)


class YouTubeSource(SourceHandler):
    """Handler for YouTube URLs using yt-dlp."""

    source_type = "youtube"

    def __init__(
        self,
        ytdl_format: str = "bestvideo[height<=720][fps<=30][vcodec^=avc]+bestaudio/best[height<=720]",
        config: "ServerConfig | None" = None,
    ):
        self.ytdl_format = ytdl_format
        self._config = config

    def _auth_args(self) -> list[str]:
        """Get yt-dlp auth arguments from config."""
        if self._config:
            from picast.config import ytdl_auth_args
            return ytdl_auth_args(self._config)
        return []

    def matches(self, url: str) -> bool:
        return any(domain in url for domain in [
            "youtube.com", "youtu.be", "youtube-nocookie.com",
        ])

    def is_playlist(self, url: str) -> bool:
        """Detect if a URL contains a playlist (has list= parameter)."""
        try:
            parsed = urlparse(url)
            params = parse_qs(parsed.query)
            return "list" in params
        except Exception:
            return False

    def extract_playlist(self, url: str) -> tuple[str, list[tuple[str, str]]]:
        """Extract playlist title and video URLs/titles using yt-dlp --flat-playlist.

        Returns (playlist_title, [(video_url, video_title), ...]).
        """
        try:
            result = subprocess.run(
                [
                    "yt-dlp",
                    "--flat-playlist",
                    "--no-warnings",
                    "--print", "%(playlist_title)s\t%(url)s\t%(title)s",
                    *self._auth_args(),
                    url,
                ],
                capture_output=True,
                text=True,
                timeout=60,
            )
            if result.returncode != 0:
                logger.warning("yt-dlp playlist extraction failed: %s", result.stderr.strip())
                return ("", [])

            playlist_title = ""
            items = []
            for line in result.stdout.strip().split("\n"):
                if not line.strip():
                    continue
                parts = line.split("\t", 2)
                if not playlist_title and len(parts) > 0:
                    playlist_title = parts[0].strip()
                video_url = parts[1].strip() if len(parts) > 1 else ""
                title = parts[2].strip() if len(parts) > 2 else ""
                if video_url:
                    # yt-dlp --flat-playlist may return just video IDs; ensure full URL
                    if not video_url.startswith("http"):
                        video_url = f"https://www.youtube.com/watch?v={video_url}"
                    items.append((video_url, title))
            return (playlist_title, items)
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as e:
            logger.warning("yt-dlp playlist extraction failed: %s", e)
            return ("", [])

    def get_metadata(self, url: str) -> SourceItem | None:
        """Get video metadata via yt-dlp."""
        try:
            result = subprocess.run(
                [
                    "yt-dlp",
                    "--no-warnings",
                    "--no-download",
                    "--print", "%(title)s\t%(duration)s\t%(thumbnail)s",
                    *self._auth_args(),
                    url,
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0 and result.stdout.strip():
                parts = result.stdout.strip().split("\t")
                title = parts[0] if len(parts) > 0 else ""
                duration = float(parts[1]) if len(parts) > 1 and parts[1] != "NA" else 0
                thumbnail = parts[2] if len(parts) > 2 else ""
                return SourceItem(
                    url=url,
                    title=title,
                    source_type="youtube",
                    duration=duration,
                    thumbnail=thumbnail,
                )
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as e:
            logger.warning("yt-dlp metadata fetch failed: %s", e)
        return None

    def get_mpv_args(self, url: str) -> list[str]:
        return [f"--ytdl-format={self.ytdl_format}"]
