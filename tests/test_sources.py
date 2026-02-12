"""Tests for source handlers."""

import os

import pytest

from picast.server.sources.base import SourceHandler, SourceItem, SourceRegistry
from picast.server.sources.local import MEDIA_EXTENSIONS, LocalSource
from picast.server.sources.twitch import TwitchSource
from picast.server.sources.youtube import YouTubeSource


class TestSourceRegistry:
    def test_register_and_list(self):
        reg = SourceRegistry()
        reg.register(YouTubeSource())
        reg.register(LocalSource())
        reg.register(TwitchSource())
        sources = reg.list_sources()
        assert "youtube" in sources
        assert "local" in sources
        assert "twitch" in sources

    def test_detect_youtube(self):
        reg = SourceRegistry()
        reg.register(YouTubeSource())
        reg.register(LocalSource())
        assert reg.detect("https://www.youtube.com/watch?v=abc") == "youtube"
        assert reg.detect("https://youtu.be/abc") == "youtube"

    def test_detect_twitch(self):
        reg = SourceRegistry()
        reg.register(TwitchSource())
        assert reg.detect("https://www.twitch.tv/somechannel") == "twitch"

    def test_detect_local(self):
        reg = SourceRegistry()
        reg.register(LocalSource())
        assert reg.detect("/home/pi/video.mp4") == "local"
        assert reg.detect("file:///home/pi/video.mp4") == "local"

    def test_detect_fallback(self):
        reg = SourceRegistry()
        assert reg.detect("https://unknown.com/video") == "youtube"

    def test_get_handler(self):
        reg = SourceRegistry()
        yt = YouTubeSource()
        reg.register(yt)
        assert reg.get_handler("youtube") is yt

    def test_get_handler_nonexistent(self):
        reg = SourceRegistry()
        assert reg.get_handler("nonexistent") is None

    def test_get_handler_for_url(self):
        reg = SourceRegistry()
        yt = YouTubeSource()
        reg.register(yt)
        assert reg.get_handler_for_url("https://youtube.com/watch?v=abc") is yt


class TestYouTubeSource:
    def test_matches_youtube(self):
        yt = YouTubeSource()
        assert yt.matches("https://www.youtube.com/watch?v=abc") is True
        assert yt.matches("https://youtu.be/abc") is True
        assert yt.matches("https://youtube-nocookie.com/embed/abc") is True

    def test_no_match_other(self):
        yt = YouTubeSource()
        assert yt.matches("https://vimeo.com/123") is False
        assert yt.matches("/local/file.mp4") is False

    def test_mpv_args(self):
        yt = YouTubeSource("bestvideo+bestaudio")
        args = yt.get_mpv_args("https://youtube.com/watch?v=abc")
        assert "--ytdl-format=bestvideo+bestaudio" in args

    def test_is_playlist_with_list_param(self):
        yt = YouTubeSource()
        assert yt.is_playlist("https://www.youtube.com/playlist?list=PLrAXtmErZgOeiKm4sgNOknGvNjby9efdf") is True
        assert yt.is_playlist("https://www.youtube.com/watch?v=abc&list=PLrAXtmErZgOei") is True

    def test_is_playlist_without_list_param(self):
        yt = YouTubeSource()
        assert yt.is_playlist("https://www.youtube.com/watch?v=abc") is False
        assert yt.is_playlist("https://youtu.be/abc") is False

    def test_is_playlist_invalid_url(self):
        yt = YouTubeSource()
        assert yt.is_playlist("not a url") is False
        assert yt.is_playlist("") is False

    def test_extract_playlist_mocked(self, monkeypatch):
        """Test playlist extraction with mocked subprocess."""
        import subprocess

        def mock_run(*args, **kwargs):
            result = subprocess.CompletedProcess(
                args=args[0],
                returncode=0,
                stdout="My Playlist\thttps://www.youtube.com/watch?v=aaa\tFirst Video\nMy Playlist\thttps://www.youtube.com/watch?v=bbb\tSecond Video\n",
                stderr="",
            )
            return result

        yt = YouTubeSource()
        monkeypatch.setattr(subprocess, "run", mock_run)
        title, items = yt.extract_playlist("https://www.youtube.com/playlist?list=PLtest")
        assert title == "My Playlist"
        assert len(items) == 2
        assert items[0] == ("https://www.youtube.com/watch?v=aaa", "First Video")
        assert items[1] == ("https://www.youtube.com/watch?v=bbb", "Second Video")

    def test_extract_playlist_empty(self, monkeypatch):
        """Empty playlist returns empty tuple."""
        import subprocess

        def mock_run(*args, **kwargs):
            return subprocess.CompletedProcess(args=args[0], returncode=0, stdout="", stderr="")

        yt = YouTubeSource()
        monkeypatch.setattr(subprocess, "run", mock_run)
        title, items = yt.extract_playlist("https://www.youtube.com/playlist?list=PLtest")
        assert title == ""
        assert items == []

    def test_extract_playlist_failure(self, monkeypatch):
        """Failed yt-dlp returns empty tuple."""
        import subprocess

        def mock_run(*args, **kwargs):
            return subprocess.CompletedProcess(args=args[0], returncode=1, stdout="", stderr="ERROR")

        yt = YouTubeSource()
        monkeypatch.setattr(subprocess, "run", mock_run)
        title, items = yt.extract_playlist("https://www.youtube.com/playlist?list=PLtest")
        assert title == ""
        assert items == []

    def test_extract_playlist_bare_video_ids(self, monkeypatch):
        """Video IDs without full URLs get expanded."""
        import subprocess

        def mock_run(*args, **kwargs):
            return subprocess.CompletedProcess(
                args=args[0], returncode=0,
                stdout="Test PL\tabc123\tA Video\n", stderr="",
            )

        yt = YouTubeSource()
        monkeypatch.setattr(subprocess, "run", mock_run)
        title, items = yt.extract_playlist("https://www.youtube.com/playlist?list=PLtest")
        assert title == "Test PL"
        assert len(items) == 1
        assert items[0][0] == "https://www.youtube.com/watch?v=abc123"


class TestLocalSource:
    def test_matches_absolute_path(self):
        local = LocalSource()
        assert local.matches("/home/pi/video.mp4") is True
        assert local.matches("/mnt/usb/movie.mkv") is True

    def test_matches_file_uri(self):
        local = LocalSource()
        assert local.matches("file:///home/pi/video.mp4") is True

    def test_matches_by_extension(self):
        local = LocalSource()
        assert local.matches("movie.mp4") is True
        assert local.matches("song.mp3") is True

    def test_no_match_url(self):
        local = LocalSource()
        assert local.matches("https://youtube.com/watch?v=abc") is False

    def test_browse_root_returns_dirs(self):
        local = LocalSource(media_dirs=["/tmp"])
        items = local.browse()
        assert len(items) >= 1
        assert items[0].source_type == "local"

    def test_browse_directory(self, tmp_path):
        # Create some test files
        (tmp_path / "video.mp4").touch()
        (tmp_path / "audio.mp3").touch()
        (tmp_path / "text.txt").touch()
        (tmp_path / "subdir").mkdir()

        local = LocalSource()
        items = local.browse(str(tmp_path))

        names = [i.title for i in items]
        assert "subdir/" in names
        assert "video" in names
        assert "audio" in names
        # .txt should be excluded
        assert "text" not in names

    def test_browse_nonexistent(self):
        local = LocalSource()
        items = local.browse("/nonexistent/path/xyz")
        assert items == []

    def test_browse_skips_hidden(self, tmp_path):
        (tmp_path / ".hidden.mp4").touch()
        (tmp_path / "visible.mp4").touch()
        local = LocalSource()
        items = local.browse(str(tmp_path))
        names = [i.title for i in items]
        assert "visible" in names
        assert ".hidden" not in names

    def test_get_metadata_local_file(self, tmp_path):
        video = tmp_path / "my_video.mp4"
        video.touch()
        local = LocalSource()
        meta = local.get_metadata(str(video))
        assert meta is not None
        assert meta.title == "my_video"
        assert meta.source_type == "local"

    def test_get_metadata_nonexistent(self):
        local = LocalSource()
        assert local.get_metadata("/nonexistent/file.mp4") is None

    def test_scan_drives(self):
        local = LocalSource()
        drives = local.scan_drives()
        # Just check it returns a list, actual drives depend on the system
        assert isinstance(drives, list)

    def test_media_extensions_complete(self):
        assert ".mp4" in MEDIA_EXTENSIONS
        assert ".mkv" in MEDIA_EXTENSIONS
        assert ".mp3" in MEDIA_EXTENSIONS
        assert ".flac" in MEDIA_EXTENSIONS


class TestTwitchSource:
    def test_matches_twitch(self):
        tw = TwitchSource()
        assert tw.matches("https://www.twitch.tv/somechannel") is True
        assert tw.matches("https://twitch.tv/somechannel") is True

    def test_no_match_other(self):
        tw = TwitchSource()
        assert tw.matches("https://youtube.com") is False

    def test_get_metadata_fallback(self):
        tw = TwitchSource()
        meta = tw.get_metadata("https://www.twitch.tv/testchannel")
        assert meta is not None
        assert "testchannel" in meta.title
        assert meta.source_type == "twitch"


class TestSourceItem:
    def test_to_dict(self):
        item = SourceItem(url="http://a", title="A", source_type="youtube")
        d = item.to_dict()
        assert d["url"] == "http://a"
        assert d["title"] == "A"
        assert d["source_type"] == "youtube"
        assert "duration" in d
