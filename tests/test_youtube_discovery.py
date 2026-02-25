"""Tests for YouTube Discovery Agent."""

import subprocess
import time
from unittest.mock import MagicMock, patch

import pytest

from picast.config import AutoplayConfig, ServerConfig, ThemeConfig, _parse_config
from picast.server.autoplay_pool import AutoPlayPool
from picast.server.youtube_discovery import DiscoveryAgent, DiscoveryResult


# --- Fixtures ---

@pytest.fixture
def pool(db):
    """Create an AutoPlayPool backed by the test database."""
    return AutoPlayPool(db)


@pytest.fixture
def agent(pool):
    """Create a DiscoveryAgent with no delay for tests."""
    return DiscoveryAgent(pool=pool, server_config=None, delay=0)


def _make_ytdlp_output(*entries):
    """Build mock yt-dlp stdout from (id, title, duration) tuples."""
    lines = []
    for vid_id, title, dur in entries:
        lines.append(f"{vid_id}\t{title}\t{dur}")
    return "\n".join(lines)


def _mock_run_ok(stdout="", returncode=0):
    """Create a mock subprocess.run result."""
    result = MagicMock()
    result.stdout = stdout
    result.stderr = ""
    result.returncode = returncode
    return result


# --- TestSearchYouTube ---

class TestSearchYouTube:

    @patch("shutil.which", return_value="/usr/bin/yt-dlp")
    @patch("subprocess.run")
    def test_basic_search(self, mock_run, mock_which, agent):
        mock_run.return_value = _mock_run_ok(
            _make_ytdlp_output(
                ("abc123def45", "Chill Vibes", "3600"),
                ("xyz789ghi01", "Focus Music", "1800"),
            )
        )
        results = agent.search_youtube("chill music", max_results=5)
        assert len(results) == 2
        assert results[0].video_id == "abc123def45"
        assert results[0].title == "Chill Vibes"
        assert results[0].duration == 3600
        assert results[0].url == "https://www.youtube.com/watch?v=abc123def45"
        assert results[1].video_id == "xyz789ghi01"

    @patch("shutil.which", return_value="/usr/bin/yt-dlp")
    @patch("subprocess.run")
    def test_search_failure(self, mock_run, mock_which, agent):
        mock_run.return_value = _mock_run_ok(returncode=1)
        mock_run.return_value.stderr = "ERROR: something went wrong"
        results = agent.search_youtube("bad query")
        assert results == []

    @patch("shutil.which", return_value="/usr/bin/yt-dlp")
    @patch("subprocess.run", side_effect=subprocess.TimeoutExpired("yt-dlp", 60))
    def test_timeout(self, mock_run, mock_which, agent):
        results = agent.search_youtube("slow query")
        assert results == []

    @patch("shutil.which", return_value=None)
    def test_missing_ytdlp(self, mock_which, agent):
        results = agent.search_youtube("test query")
        assert results == []

    @patch("shutil.which", return_value="/usr/bin/yt-dlp")
    @patch("subprocess.run")
    def test_na_duration(self, mock_run, mock_which, agent):
        mock_run.return_value = _mock_run_ok(
            _make_ytdlp_output(("vid123456789", "Live Stream", "NA"))
        )
        results = agent.search_youtube("live")
        assert len(results) == 1
        assert results[0].duration == 0  # NA -> 0

    @patch("shutil.which", return_value="/usr/bin/yt-dlp")
    @patch("subprocess.run")
    def test_none_duration(self, mock_run, mock_which, agent):
        mock_run.return_value = _mock_run_ok(
            _make_ytdlp_output(("vid123456789", "Unknown", "None"))
        )
        results = agent.search_youtube("test")
        assert len(results) == 1
        assert results[0].duration == 0

    @patch("shutil.which", return_value="/usr/bin/yt-dlp")
    @patch("subprocess.run")
    def test_url_construction(self, mock_run, mock_which, agent):
        mock_run.return_value = _mock_run_ok(
            _make_ytdlp_output(("dQw4w9WgXcQ", "Never Gonna", "213"))
        )
        results = agent.search_youtube("rickroll")
        assert results[0].url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

    @patch("shutil.which", return_value="/usr/bin/yt-dlp")
    @patch("subprocess.run")
    def test_search_term_format(self, mock_run, mock_which, agent):
        """Verify yt-dlp is called with ytsearchN:query format."""
        mock_run.return_value = _mock_run_ok("")
        agent.search_youtube("focus music", max_results=10)
        cmd = mock_run.call_args[0][0]
        assert cmd[1] == "ytsearch10:focus music"

    @patch("shutil.which", return_value="/usr/bin/yt-dlp")
    @patch("subprocess.run")
    def test_auth_args_passed(self, mock_run, mock_which, pool):
        """Auth args from server config are passed to yt-dlp."""
        config = ServerConfig(ytdl_cookies_from_browser="chromium")
        agent = DiscoveryAgent(pool=pool, server_config=config, delay=0)
        mock_run.return_value = _mock_run_ok("")
        agent.search_youtube("test")
        cmd = mock_run.call_args[0][0]
        assert "--cookies-from-browser=chromium" in cmd

    @patch("shutil.which", return_value="/usr/bin/yt-dlp")
    @patch("subprocess.run")
    def test_malformed_lines_skipped(self, mock_run, mock_which, agent):
        mock_run.return_value = _mock_run_ok("badline\n\nvid123456789\tTitle\t120")
        results = agent.search_youtube("test")
        assert len(results) == 1
        assert results[0].video_id == "vid123456789"


# --- TestFilterByDuration ---

class TestFilterByDuration:

    def test_no_filter(self, agent):
        results = [
            DiscoveryResult("a", "A", 100, "url_a"),
            DiscoveryResult("b", "B", 200, "url_b"),
        ]
        filtered = agent.filter_by_duration(results)
        assert len(filtered) == 2

    def test_min_duration(self, agent):
        results = [
            DiscoveryResult("a", "A", 100, "url_a"),
            DiscoveryResult("b", "B", 200, "url_b"),
        ]
        filtered = agent.filter_by_duration(results, min_duration=150)
        assert len(filtered) == 1
        assert filtered[0].video_id == "b"

    def test_max_duration(self, agent):
        results = [
            DiscoveryResult("a", "A", 100, "url_a"),
            DiscoveryResult("b", "B", 200, "url_b"),
        ]
        filtered = agent.filter_by_duration(results, max_duration=150)
        assert len(filtered) == 1
        assert filtered[0].video_id == "a"

    def test_both_bounds(self, agent):
        results = [
            DiscoveryResult("a", "A", 50, "url_a"),
            DiscoveryResult("b", "B", 150, "url_b"),
            DiscoveryResult("c", "C", 300, "url_c"),
        ]
        filtered = agent.filter_by_duration(results, min_duration=100, max_duration=200)
        assert len(filtered) == 1
        assert filtered[0].video_id == "b"

    def test_unknown_duration_kept_no_max(self, agent):
        results = [DiscoveryResult("a", "A", 0, "url_a")]
        filtered = agent.filter_by_duration(results, min_duration=100)
        assert len(filtered) == 1

    def test_unknown_duration_skipped_with_max(self, agent):
        results = [DiscoveryResult("a", "A", 0, "url_a")]
        filtered = agent.filter_by_duration(results, max_duration=300)
        assert len(filtered) == 0


# --- TestDiscoverForBlock ---

class TestDiscoverForBlock:

    @patch("shutil.which", return_value="/usr/bin/yt-dlp")
    @patch("subprocess.run")
    def test_adds_videos(self, mock_run, mock_which, agent):
        mock_run.return_value = _mock_run_ok(
            _make_ytdlp_output(("vid_a1234567", "Video A", "120"))
        )
        theme = ThemeConfig(queries=["test query"], max_results=5)
        stats = agent.discover_for_block("focus", theme)
        assert stats["added"] == 1
        assert stats["found"] == 1
        assert stats["queries_run"] == 1

    @patch("shutil.which", return_value="/usr/bin/yt-dlp")
    @patch("subprocess.run")
    def test_skips_duplicates(self, mock_run, mock_which, agent, pool):
        # Pre-add the video
        pool.add_video("focus", "https://www.youtube.com/watch?v=vid_a1234567", "Video A")
        mock_run.return_value = _mock_run_ok(
            _make_ytdlp_output(("vid_a1234567", "Video A", "120"))
        )
        theme = ThemeConfig(queries=["test"], max_results=5)
        stats = agent.discover_for_block("focus", theme)
        assert stats["added"] == 0
        assert stats["skipped"] == 1

    @patch("shutil.which", return_value="/usr/bin/yt-dlp")
    @patch("subprocess.run")
    def test_filters_duration(self, mock_run, mock_which, agent):
        mock_run.return_value = _mock_run_ok(
            _make_ytdlp_output(
                ("short1234567", "Short", "30"),
                ("long12345678", "Long Enough", "300"),
            )
        )
        theme = ThemeConfig(queries=["test"], min_duration=60, max_results=5)
        stats = agent.discover_for_block("focus", theme)
        assert stats["found"] == 2
        assert stats["added"] == 1

    def test_empty_queries(self, agent):
        theme = ThemeConfig(queries=[], max_results=5)
        stats = agent.discover_for_block("focus", theme)
        assert stats["queries_run"] == 0
        assert stats["found"] == 0

    @patch("shutil.which", return_value="/usr/bin/yt-dlp")
    @patch("subprocess.run")
    def test_multiple_queries(self, mock_run, mock_which, agent):
        mock_run.side_effect = [
            _mock_run_ok(_make_ytdlp_output(("vid_q1_12345", "Q1 Result", "120"))),
            _mock_run_ok(_make_ytdlp_output(("vid_q2_12345", "Q2 Result", "180"))),
        ]
        theme = ThemeConfig(queries=["query one", "query two"], max_results=5)
        stats = agent.discover_for_block("focus", theme)
        assert stats["queries_run"] == 2
        assert stats["added"] == 2

    @patch("shutil.which", return_value="/usr/bin/yt-dlp")
    @patch("subprocess.run")
    def test_source_is_discovery(self, mock_run, mock_which, agent, pool):
        mock_run.return_value = _mock_run_ok(
            _make_ytdlp_output(("vidsrc12345", "Src Test", "120"))
        )
        theme = ThemeConfig(queries=["test"], max_results=5)
        agent.discover_for_block("focus", theme)
        video = pool.get_video("focus", "vidsrc12345")
        assert video is not None
        assert video["source"] == "discovery"


# --- TestDiscoverAll ---

class TestDiscoverAll:

    @patch("shutil.which", return_value="/usr/bin/yt-dlp")
    @patch("subprocess.run")
    def test_all_blocks_processed(self, mock_run, mock_which, agent):
        mock_run.return_value = _mock_run_ok(
            _make_ytdlp_output(("vid_all12345", "All Block", "120"))
        )
        themes = {
            "focus": ThemeConfig(queries=["focus music"], max_results=2),
            "clean": ThemeConfig(queries=["cleaning music"], max_results=2),
        }
        all_stats = agent.discover_all(themes)
        assert len(all_stats) == 2
        assert all_stats[0]["block"] == "focus"
        assert all_stats[1]["block"] == "clean"


# --- TestRateLimiting ---

class TestRateLimiting:

    @patch("shutil.which", return_value="/usr/bin/yt-dlp")
    @patch("subprocess.run")
    @patch("time.sleep")
    def test_delay_between_queries(self, mock_sleep, mock_run, mock_which, pool):
        agent = DiscoveryAgent(pool=pool, delay=0.1)
        mock_run.return_value = _mock_run_ok("")
        theme = ThemeConfig(queries=["q1", "q2", "q3"], max_results=2)
        agent.discover_for_block("test", theme)
        # Sleep called between queries (not before the first)
        assert mock_sleep.call_count == 2
        mock_sleep.assert_any_call(0.1)


# --- TestDiscoverEndpoints ---

class TestDiscoverEndpoints:

    @pytest.fixture
    def themed_app(self, tmp_path):
        """Create a Flask app with discovery themes configured."""
        from picast.config import AutoplayConfig, ServerConfig
        from picast.server.app import create_app

        config = ServerConfig(
            mpv_socket="/tmp/picast-test-socket",
            db_file=str(tmp_path / "test.db"),
            data_dir=str(tmp_path / "data"),
        )
        autoplay = AutoplayConfig(
            enabled=True,
            pool_mode=True,
            themes={
                "focus": ThemeConfig(queries=["focus music"], max_results=3),
            },
            discovery_delay=0,
        )
        app = create_app(config, autoplay_config=autoplay)
        app.player.stop()
        app.config["TESTING"] = True
        return app

    @pytest.fixture
    def themed_client(self, themed_app):
        return themed_app.test_client()

    @patch("shutil.which", return_value="/usr/bin/yt-dlp")
    @patch("subprocess.run")
    def test_discover_single_block(self, mock_run, mock_which, themed_client):
        mock_run.return_value = _mock_run_ok(
            _make_ytdlp_output(("ep_vid123456", "Endpoint Test", "120"))
        )
        resp = themed_client.post("/api/autoplay/discover/focus")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["block"] == "focus"
        assert data["added"] >= 0

    @patch("shutil.which", return_value="/usr/bin/yt-dlp")
    @patch("subprocess.run")
    def test_discover_all_blocks(self, mock_run, mock_which, themed_client):
        mock_run.return_value = _mock_run_ok(
            _make_ytdlp_output(("ep_all123456", "All Blocks", "120"))
        )
        resp = themed_client.post("/api/autoplay/discover")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert "blocks" in data
        assert "total_added" in data

    def test_discover_unconfigured_block(self, themed_client):
        resp = themed_client.post("/api/autoplay/discover/nonexistent")
        assert resp.status_code == 404

    @patch("shutil.which", return_value="/usr/bin/yt-dlp")
    @patch("subprocess.run")
    def test_discover_with_overrides(self, mock_run, mock_which, themed_client):
        mock_run.return_value = _mock_run_ok(
            _make_ytdlp_output(("ov_vid1234567", "Override", "120"))
        )
        resp = themed_client.post(
            "/api/autoplay/discover/focus",
            json={"queries": ["custom query"], "max_results": 2},
        )
        assert resp.status_code == 200
        cmd = mock_run.call_args[0][0]
        assert "ytsearch2:custom query" in cmd[1]

    @patch("shutil.which", return_value="/usr/bin/yt-dlp")
    @patch("subprocess.run")
    def test_discover_no_themes(self, mock_run, mock_which, tmp_path):
        """App with no themes returns 404 for discover-all."""
        from picast.config import AutoplayConfig, ServerConfig
        from picast.server.app import create_app

        config = ServerConfig(
            mpv_socket="/tmp/picast-test-socket",
            db_file=str(tmp_path / "test_notheme.db"),
            data_dir=str(tmp_path / "data_notheme"),
        )
        autoplay = AutoplayConfig(enabled=True, pool_mode=True)
        app = create_app(config, autoplay_config=autoplay)
        app.player.stop()
        app.config["TESTING"] = True
        client = app.test_client()

        resp = client.post("/api/autoplay/discover")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["total_added"] == 0


# --- TestThemeConfigParsing ---

class TestThemeConfigParsing:

    def test_parse_themes(self):
        data = {
            "autoplay": {
                "enabled": True,
                "pool_mode": True,
                "themes": {
                    "focus": {
                        "queries": ["lofi beats", "focus music"],
                        "min_duration": 1800,
                        "max_duration": 7200,
                        "max_results": 3,
                    },
                    "clean": {
                        "queries": ["cleaning music"],
                    },
                },
                "discovery_delay": 10.0,
            }
        }
        config = _parse_config(data)
        assert "focus" in config.autoplay.themes
        assert "clean" in config.autoplay.themes
        focus = config.autoplay.themes["focus"]
        assert focus.queries == ["lofi beats", "focus music"]
        assert focus.min_duration == 1800
        assert focus.max_duration == 7200
        assert focus.max_results == 3
        clean = config.autoplay.themes["clean"]
        assert clean.queries == ["cleaning music"]
        assert clean.min_duration == 0  # default
        assert clean.max_results == 5  # default
        assert config.autoplay.discovery_delay == 10.0

    def test_parse_no_themes(self):
        data = {"autoplay": {"enabled": True}}
        config = _parse_config(data)
        assert config.autoplay.themes == {}
        assert config.autoplay.discovery_delay == 5.0

    def test_parse_theme_defaults(self):
        data = {
            "autoplay": {
                "themes": {
                    "block": {"queries": ["test"]},
                },
            }
        }
        config = _parse_config(data)
        theme = config.autoplay.themes["block"]
        assert theme.min_duration == 0
        assert theme.max_duration == 0
        assert theme.max_results == 5
