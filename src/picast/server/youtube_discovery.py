"""YouTube Discovery Agent for AutoPlay pool enrichment.

Searches YouTube via yt-dlp to find new videos matching per-block
themes and adds them to the autoplay pool.

Named youtube_discovery (not discovery) to avoid conflict with
the existing discovery.py module (zeroconf/mDNS device discovery).
"""

import logging
import shutil
import subprocess
import time
from dataclasses import dataclass

from picast.config import ServerConfig, ThemeConfig, ytdl_auth_args
from picast.server.autoplay_pool import AutoPlayPool

logger = logging.getLogger(__name__)


@dataclass
class DiscoveryResult:
    """A single search result from yt-dlp."""

    video_id: str
    title: str
    duration: int  # seconds, 0 = unknown
    url: str


class DiscoveryAgent:
    """Finds new videos for autoplay pools via YouTube search."""

    def __init__(
        self,
        pool: AutoPlayPool,
        server_config: ServerConfig | None = None,
        delay: float = 5.0,
    ):
        self.pool = pool
        self.server_config = server_config
        self.delay = delay

    def search_youtube(self, query: str, max_results: int = 5) -> list[DiscoveryResult]:
        """Search YouTube via yt-dlp flat-playlist mode.

        Returns a list of DiscoveryResult. Handles missing yt-dlp,
        timeouts, and NA duration values gracefully.
        """
        if not shutil.which("yt-dlp"):
            logger.error("yt-dlp not found in PATH")
            return []

        search_term = f"ytsearch{max_results}:{query}"
        cmd = [
            "yt-dlp", search_term,
            "--flat-playlist",
            "--print", "%(id)s\t%(title)s\t%(duration)s",
            "--no-warnings",
        ]

        # Add auth args from config
        if self.server_config:
            cmd.extend(ytdl_auth_args(self.server_config))

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=60,
            )
        except subprocess.TimeoutExpired:
            logger.warning("yt-dlp search timed out for query: %s", query)
            return []
        except FileNotFoundError:
            logger.error("yt-dlp not found")
            return []

        if result.returncode != 0:
            logger.warning("yt-dlp search failed for '%s': %s", query, result.stderr.strip())
            return []

        results = []
        for line in result.stdout.strip().splitlines():
            parts = line.split("\t", 2)
            if len(parts) < 3:
                continue
            video_id, title, dur_str = parts
            # Duration can be 'NA', 'None', or a number
            try:
                duration = int(float(dur_str))
            except (ValueError, TypeError):
                duration = 0
            url = f"https://www.youtube.com/watch?v={video_id}"
            results.append(DiscoveryResult(
                video_id=video_id, title=title, duration=duration, url=url,
            ))

        return results

    def filter_by_duration(
        self,
        results: list[DiscoveryResult],
        min_duration: int = 0,
        max_duration: int = 0,
    ) -> list[DiscoveryResult]:
        """Filter results by duration bounds.

        Unknown duration (0) is kept unless max_duration is set.
        """
        filtered = []
        for r in results:
            if r.duration == 0:
                # Unknown duration: skip only if max_duration is set
                if max_duration > 0:
                    continue
                filtered.append(r)
                continue
            if min_duration > 0 and r.duration < min_duration:
                continue
            if max_duration > 0 and r.duration > max_duration:
                continue
            filtered.append(r)
        return filtered

    def discover_for_block(self, block_name: str, theme: ThemeConfig) -> dict:
        """Run discovery for a single block.

        Returns stats dict: {block, queries_run, found, added, skipped}.
        """
        stats = {
            "block": block_name,
            "queries_run": 0,
            "found": 0,
            "added": 0,
            "skipped": 0,
        }

        if not theme.queries:
            return stats

        for i, query in enumerate(theme.queries):
            if i > 0:
                time.sleep(self.delay)

            results = self.search_youtube(query, theme.max_results)
            stats["queries_run"] += 1
            stats["found"] += len(results)

            # Filter by duration
            results = self.filter_by_duration(
                results, theme.min_duration, theme.max_duration,
            )

            # Add to pool
            for r in results:
                added = self.pool.add_video(
                    block_name, r.url, r.title, source="discovery",
                    duration=r.duration,
                )
                if added:
                    stats["added"] += 1
                else:
                    stats["skipped"] += 1

        return stats

    def discover_all(self, themes: dict[str, ThemeConfig]) -> list[dict]:
        """Run discovery for all configured blocks.

        Returns list of per-block stats dicts.
        """
        all_stats = []
        for block_name, theme in themes.items():
            stats = self.discover_for_block(block_name, theme)
            all_stats.append(stats)
        return all_stats
