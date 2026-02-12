"""Local file source handler."""

import logging
import os

from picast.server.sources.base import SourceHandler, SourceItem

logger = logging.getLogger(__name__)

VIDEO_EXTENSIONS = {
    ".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm",
    ".m4v", ".mpg", ".mpeg", ".ts", ".vob", ".3gp",
}

AUDIO_EXTENSIONS = {
    ".mp3", ".flac", ".wav", ".aac", ".ogg", ".m4a", ".wma", ".opus",
}

MEDIA_EXTENSIONS = VIDEO_EXTENSIONS | AUDIO_EXTENSIONS


class LocalSource(SourceHandler):
    """Handler for local files and directories.

    Supports browsing external drives and local directories for media files.
    """

    source_type = "local"

    def __init__(self, media_dirs: list[str] | None = None):
        self.media_dirs = media_dirs or self._default_media_dirs()

    @staticmethod
    def _default_media_dirs() -> list[str]:
        """Default directories to scan for media."""
        dirs = []
        home = os.path.expanduser("~")

        # Common media directories
        for name in ["Videos", "Music", "Movies", "Downloads"]:
            path = os.path.join(home, name)
            if os.path.isdir(path):
                dirs.append(path)

        # External drives (Linux / Raspberry Pi)
        for mount_base in ["/media", "/mnt"]:
            if os.path.isdir(mount_base):
                try:
                    for entry in os.scandir(mount_base):
                        if entry.is_dir():
                            # Check user-specific mounts like /media/pi/DRIVE
                            for sub in os.scandir(entry.path):
                                if sub.is_dir():
                                    dirs.append(sub.path)
                except PermissionError:
                    pass

        return dirs

    def matches(self, url: str) -> bool:
        if url.startswith(("file://", "/")):
            return True
        # Check by extension
        _, ext = os.path.splitext(url.lower())
        return ext in MEDIA_EXTENSIONS

    def get_metadata(self, url: str) -> SourceItem | None:
        path = url.replace("file://", "") if url.startswith("file://") else url
        if not os.path.exists(path):
            return None
        name = os.path.basename(path)
        title, _ = os.path.splitext(name)
        return SourceItem(
            url=path,
            title=title,
            source_type="local",
        )

    def get_mpv_args(self, url: str) -> list[str]:
        return []

    def browse(self, path: str = "") -> list[SourceItem]:
        """Browse a directory for media files and subdirectories.

        If path is empty, returns the list of configured media directories.
        """
        if not path:
            return [
                SourceItem(
                    url=d,
                    title=os.path.basename(d) or d,
                    source_type="local",
                )
                for d in self.media_dirs
                if os.path.isdir(d)
            ]

        if not os.path.isdir(path):
            return []

        items = []
        try:
            entries = sorted(os.scandir(path), key=lambda e: (not e.is_dir(), e.name.lower()))
            for entry in entries:
                if entry.name.startswith("."):
                    continue
                if entry.is_dir():
                    items.append(SourceItem(
                        url=entry.path,
                        title=entry.name + "/",
                        source_type="local",
                    ))
                elif entry.is_file():
                    _, ext = os.path.splitext(entry.name.lower())
                    if ext in MEDIA_EXTENSIONS:
                        title, _ = os.path.splitext(entry.name)
                        items.append(SourceItem(
                            url=entry.path,
                            title=title,
                            source_type="local",
                        ))
        except PermissionError:
            logger.warning("Permission denied browsing: %s", path)

        return items

    def scan_drives(self) -> list[dict]:
        """Scan for available external drives/mount points."""
        drives = []
        for mount_base in ["/media", "/mnt", "/Volumes"]:
            if not os.path.isdir(mount_base):
                continue
            try:
                for entry in os.scandir(mount_base):
                    if entry.is_dir() and not entry.name.startswith("."):
                        drives.append({
                            "path": entry.path,
                            "name": entry.name,
                            "mount": mount_base,
                        })
            except PermissionError:
                pass
        return drives
