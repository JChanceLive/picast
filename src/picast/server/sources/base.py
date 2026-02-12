"""Base source handler and registry."""

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class SourceItem:
    """A playable item from a source."""

    url: str
    title: str = ""
    source_type: str = ""
    duration: float = 0
    thumbnail: str = ""
    metadata: dict | None = None

    def to_dict(self) -> dict:
        return {
            "url": self.url,
            "title": self.title,
            "source_type": self.source_type,
            "duration": self.duration,
            "thumbnail": self.thumbnail,
        }


class SourceHandler:
    """Base class for source handlers."""

    source_type: str = ""

    def matches(self, url: str) -> bool:
        """Return True if this handler can handle the given URL."""
        return False

    def get_metadata(self, url: str) -> SourceItem | None:
        """Get metadata for a URL (title, duration, etc.)."""
        return None

    def get_mpv_args(self, url: str) -> list[str]:
        """Get extra mpv arguments for this source type."""
        return []

    def browse(self, path: str = "") -> list[SourceItem]:
        """Browse available items (for sources that support it)."""
        return []


class SourceRegistry:
    """Registry of source handlers with auto-detection."""

    def __init__(self):
        self._handlers: list[SourceHandler] = []

    def register(self, handler: SourceHandler):
        """Register a source handler."""
        self._handlers.append(handler)
        logger.info("Registered source handler: %s", handler.source_type)

    def detect(self, url: str) -> str:
        """Detect source type from URL."""
        for handler in self._handlers:
            if handler.matches(url):
                return handler.source_type
        return "youtube"  # default fallback

    def get_handler(self, source_type: str) -> SourceHandler | None:
        """Get a handler by source type."""
        for handler in self._handlers:
            if handler.source_type == source_type:
                return handler
        return None

    def get_handler_for_url(self, url: str) -> SourceHandler | None:
        """Get the handler that matches a URL."""
        for handler in self._handlers:
            if handler.matches(url):
                return handler
        return None

    def get_metadata(self, url: str) -> SourceItem | None:
        """Get metadata using the appropriate handler."""
        handler = self.get_handler_for_url(url)
        if handler:
            return handler.get_metadata(url)
        return None

    def list_sources(self) -> list[str]:
        """List registered source types."""
        return [h.source_type for h in self._handlers]
