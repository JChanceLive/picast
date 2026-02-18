"""Source handlers for PiCast.

Each source type (YouTube, local files, Twitch, Archive.org) has a handler
that knows how to resolve URLs, get metadata, and prepare items for playback.
"""

from picast.server.sources.archive import ArchiveSource
from picast.server.sources.base import SourceHandler, SourceRegistry
from picast.server.sources.local import LocalSource
from picast.server.sources.twitch import TwitchSource
from picast.server.sources.youtube import YouTubeSource

__all__ = [
    "SourceHandler",
    "SourceRegistry",
    "YouTubeSource",
    "LocalSource",
    "TwitchSource",
    "ArchiveSource",
]
