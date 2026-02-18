"""Internet Archive (archive.org) source handler."""

import json
import logging
import subprocess
import urllib.request
from urllib.parse import quote, urlparse

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

    def search(
        self,
        genre: str = "",
        decade: str = "",
        keyword: str = "",
        sort: str = "downloads",
        rows: int = 50,
        year_start: int = 0,
        year_end: int = 0,
    ) -> list[SourceItem]:
        """Search Archive.org feature_films collection.

        Args:
            genre: Genre filter (e.g. "horror", "comedy"). Empty = any.
            decade: Decade filter (e.g. "1960s"). Empty = any (legacy).
            keyword: Free-text title search. Empty = any.
            sort: Sort order — "downloads", "date", "title".
            rows: Max results to fetch.
            year_start: Start year for range filter (e.g. 1980). 0 = no limit.
            year_end: End year for range filter (e.g. 2025). 0 = no limit.

        Returns list of SourceItem for matching movies.
        """
        query_parts = ["collection:feature_films", "mediatype:movies"]
        if genre:
            query_parts.append(f"subject:{genre}")
        if year_start > 0 or year_end > 0:
            # Year range: year:[start TO end]
            start = year_start if year_start > 0 else 1900
            end = year_end if year_end > 0 else 2030
            query_parts.append(f"year:[{start} TO {end}]")
        elif decade:
            # Legacy decade support: "1960s" -> year:[1960 TO 1969]
            try:
                start = int(decade.rstrip("s"))
                end = start + 9
                query_parts.append(f"year:[{start} TO {end}]")
            except ValueError:
                pass
        if keyword:
            query_parts.append(f"title:({keyword})")

        sort_map = {
            "downloads": "downloads+desc",
            "date": "addeddate+desc",
            "title": "titleSorter+asc",
        }
        sort_param = sort_map.get(sort, "downloads+desc")

        query = " AND ".join(query_parts)
        url = (
            f"https://archive.org/advancedsearch.php?"
            f"q={quote(query)}&output=json&rows={rows}"
            f"&fl[]=identifier&fl[]=title&fl[]=year&fl[]=downloads"
            f"&sort[]={sort_param}"
        )

        try:
            req = urllib.request.Request(url, headers={"User-Agent": "PiCast/0.13"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                data = json.loads(resp.read().decode())
        except Exception as e:
            logger.warning("Archive.org search failed: %s", e)
            return []

        results = []
        for doc in data.get("response", {}).get("docs", []):
            identifier = doc.get("identifier", "")
            if not identifier:
                continue
            title = doc.get("title", identifier)
            year = doc.get("year", "")
            if year:
                title = f"{title} ({year})"
            results.append(SourceItem(
                url=f"https://archive.org/details/{identifier}",
                title=title,
                source_type="archive",
            ))
        return results

    def get_mpv_args(self, url: str) -> list[str]:
        return []
