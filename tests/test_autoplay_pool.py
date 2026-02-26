"""Tests for AutoPlay pool system."""

import json
from unittest.mock import patch

import pytest

from picast.server.autoplay_pool import AutoPlayPool, extract_video_id


# --- extract_video_id ---

class TestExtractVideoId:
    def test_standard_url(self):
        assert extract_video_id("https://www.youtube.com/watch?v=hlWiI4xVXKY") == "hlWiI4xVXKY"

    def test_short_url(self):
        assert extract_video_id("https://youtu.be/hlWiI4xVXKY") == "hlWiI4xVXKY"

    def test_embed_url(self):
        assert extract_video_id("https://www.youtube.com/embed/hlWiI4xVXKY") == "hlWiI4xVXKY"

    def test_non_youtube(self):
        assert extract_video_id("https://example.com/video") == ""

    def test_empty(self):
        assert extract_video_id("") == ""


# --- Pool fixtures ---

@pytest.fixture
def pool(db):
    return AutoPlayPool(db, avoid_recent=2)


SAMPLE_VIDEOS = [
    ("morning-foundation", "https://www.youtube.com/watch?v=hlWiI4xVXKY", "Sunny Mornings"),
    ("morning-foundation", "https://www.youtube.com/watch?v=CcsUYu0PVxY", "4 Hours Peaceful"),
    ("morning-foundation", "https://www.youtube.com/watch?v=wuLKvcn-c7A", "Morning Positive"),
    ("clean-mama", "https://www.youtube.com/watch?v=8pBB-s9nbB0", "Cleaning Day Vintage"),
    ("clean-mama", "https://www.youtube.com/watch?v=Rd8v2m2h5WI", "60s 70s Oldies"),
]


def _seed_pool(pool):
    """Add sample videos to pool."""
    for block, url, title in SAMPLE_VIDEOS:
        pool.add_video(block, url, title)


# --- add / get / remove ---

class TestPoolCRUD:
    def test_add_video(self, pool):
        result = pool.add_video("test-block", "https://www.youtube.com/watch?v=abc12345678", "Test Video")
        assert result is not None
        assert result["video_id"] == "abc12345678"
        assert result["block_name"] == "test-block"

    def test_add_duplicate_returns_none(self, pool):
        pool.add_video("test-block", "https://www.youtube.com/watch?v=abc12345678", "Test")
        result = pool.add_video("test-block", "https://www.youtube.com/watch?v=abc12345678", "Test Again")
        assert result is None

    def test_same_video_different_blocks(self, pool):
        r1 = pool.add_video("block-a", "https://www.youtube.com/watch?v=abc12345678", "Test")
        r2 = pool.add_video("block-b", "https://www.youtube.com/watch?v=abc12345678", "Test")
        assert r1 is not None
        assert r2 is not None

    def test_get_pool(self, pool):
        _seed_pool(pool)
        morning = pool.get_pool("morning-foundation")
        assert len(morning) == 3
        clean = pool.get_pool("clean-mama")
        assert len(clean) == 2

    def test_get_pool_empty(self, pool):
        assert pool.get_pool("nonexistent") == []

    def test_remove_video(self, pool):
        pool.add_video("test-block", "https://www.youtube.com/watch?v=abc12345678", "Test")
        ok = pool.remove_video("test-block", "abc12345678")
        assert ok is True
        # Pool should be empty (active only)
        assert len(pool.get_pool("test-block")) == 0
        # But visible with retired flag
        assert len(pool.get_pool("test-block", include_retired=True)) == 1

    def test_remove_nonexistent(self, pool):
        assert pool.remove_video("nope", "nope") is False

    def test_restore_video(self, pool):
        pool.add_video("test-block", "https://www.youtube.com/watch?v=abc12345678", "Test")
        pool.remove_video("test-block", "abc12345678")
        ok = pool.restore_video("test-block", "abc12345678")
        assert ok is True
        assert len(pool.get_pool("test-block")) == 1

    def test_non_youtube_url(self, pool):
        result = pool.add_video("test-block", "/local/file.mp4", "Local File")
        assert result is not None
        assert result["video_id"] == "/local/file.mp4"


# --- Rating ---

class TestRating:
    def test_rate_video(self, pool):
        pool.add_video("test-block", "https://www.youtube.com/watch?v=abc12345678", "Test")
        ok = pool.rate_video("test-block", "abc12345678", 1)
        assert ok is True
        video = pool.get_video("test-block", "abc12345678")
        assert video["rating"] == 1

    def test_rate_clamps_values(self, pool):
        pool.add_video("test-block", "https://www.youtube.com/watch?v=abc12345678", "Test")
        pool.rate_video("test-block", "abc12345678", 99)
        video = pool.get_video("test-block", "abc12345678")
        assert video["rating"] == 1

    def test_rate_nonexistent(self, pool):
        assert pool.rate_video("nope", "nope", 1) is False


# --- Selection ---

class TestSelection:
    def test_select_from_pool(self, pool):
        _seed_pool(pool)
        result = pool.select_video("morning-foundation")
        assert result is not None
        assert result["block_name"] == "morning-foundation"

    def test_select_empty_pool(self, pool):
        assert pool.select_video("nonexistent") is None

    def test_select_updates_play_count(self, pool):
        pool.add_video("test-block", "https://www.youtube.com/watch?v=abc12345678", "Test")
        pool.select_video("test-block")
        video = pool.get_video("test-block", "abc12345678")
        assert video["play_count"] == 1
        assert video["last_played"] is not None

    def test_select_logs_history(self, pool):
        pool.add_video("test-block", "https://www.youtube.com/watch?v=abc12345678", "Test")
        pool.select_video("test-block")
        history = pool.get_history("test-block")
        assert len(history) == 1
        assert history[0]["video_id"] == "abc12345678"

    def test_avoid_recent(self, pool):
        """With avoid_recent=2 and 3 videos, same video shouldn't play twice in a row."""
        pool.add_video("test", "https://www.youtube.com/watch?v=aaaaaaaaaaa", "A")
        pool.add_video("test", "https://www.youtube.com/watch?v=bbbbbbbbbbb", "B")
        pool.add_video("test", "https://www.youtube.com/watch?v=ccccccccccc", "C")

        # Play all 3 times, check no immediate repeat
        plays = []
        for _ in range(10):
            result = pool.select_video("test")
            plays.append(result["video_id"])

        # With avoid_recent=2, no video should appear twice in a row
        for i in range(1, len(plays)):
            assert plays[i] != plays[i - 1], f"Repeated {plays[i]} at position {i}"

    def test_avoid_recent_fallback_when_all_recent(self, pool):
        """With only 1 video, it should still play even if recently played."""
        pool.add_video("test", "https://www.youtube.com/watch?v=aaaaaaaaaaa", "A")
        r1 = pool.select_video("test")
        r2 = pool.select_video("test")
        assert r1 is not None
        assert r2 is not None

    def test_liked_videos_selected_more(self, pool):
        """Liked videos should be selected more often than disliked."""
        pool.add_video("test", "https://www.youtube.com/watch?v=liked11111a", "Liked")
        pool.add_video("test", "https://www.youtube.com/watch?v=dislike1111", "Disliked")
        pool.rate_video("test", "liked11111a", 1)
        pool.rate_video("test", "dislike1111", -1)

        # avoid_recent=2 means with 2 videos they alternate, so set avoid_recent=0
        pool.avoid_recent = 0

        counts = {"liked11111a": 0, "dislike1111": 0}
        for _ in range(100):
            result = pool.select_video("test")
            counts[result["video_id"]] += 1

        # Liked (weight 3.0) should be selected much more than disliked (weight 0.1)
        assert counts["liked11111a"] > counts["dislike1111"] * 2


# --- History ---

class TestHistory:
    def test_get_history(self, pool):
        _seed_pool(pool)
        pool.select_video("morning-foundation")
        pool.select_video("clean-mama")
        history = pool.get_history()
        assert len(history) == 2

    def test_get_history_by_block(self, pool):
        _seed_pool(pool)
        pool.select_video("morning-foundation")
        pool.select_video("clean-mama")
        history = pool.get_history(block_name="morning-foundation")
        assert len(history) == 1

    def test_get_last_played(self, pool):
        _seed_pool(pool)
        pool.select_video("morning-foundation")
        last = pool.get_last_played()
        assert last is not None
        assert last["block_name"] == "morning-foundation"


# --- Seed from mappings ---

class TestSeed:
    def test_seed_from_mappings(self, pool):
        mappings = {
            "block-a": "https://www.youtube.com/watch?v=abc12345678",
            "block-b": "https://www.youtube.com/watch?v=def12345678",
        }
        count = pool.seed_from_mappings(mappings)
        assert count == 2
        assert len(pool.get_pool("block-a")) == 1

    def test_seed_idempotent(self, pool):
        mappings = {"block-a": "https://www.youtube.com/watch?v=abc12345678"}
        pool.seed_from_mappings(mappings)
        count = pool.seed_from_mappings(mappings)
        assert count == 0  # Already exists


# --- Block summary ---

class TestBlockSummary:
    def test_get_all_blocks(self, pool):
        _seed_pool(pool)
        blocks = pool.get_all_blocks()
        names = [b["block_name"] for b in blocks]
        assert "morning-foundation" in names
        assert "clean-mama" in names

    def test_block_counts(self, pool):
        _seed_pool(pool)
        pool.rate_video("morning-foundation", "hlWiI4xVXKY", 1)
        pool.rate_video("morning-foundation", "CcsUYu0PVxY", -1)
        blocks = pool.get_all_blocks()
        morning = next(b for b in blocks if b["block_name"] == "morning-foundation")
        assert morning["pool_size"] == 3
        assert morning["liked"] == 1
        assert morning["disliked"] == 1


# --- video_id_to_url ---

class TestVideoIdToUrl:
    def test_youtube_id(self, pool):
        assert pool.video_id_to_url("abc12345678") == "https://www.youtube.com/watch?v=abc12345678"

    def test_full_url_passthrough(self, pool):
        assert pool.video_id_to_url("https://example.com") == "https://example.com"

    def test_local_path_passthrough(self, pool):
        assert pool.video_id_to_url("/local/file.mp4") == "/local/file.mp4"


# --- Web UI integration tests ---

class TestAutoplayCurrentTracking:
    """Test that autoplay_current is set/cleared in status."""

    def test_status_includes_autoplay_current(self, client):
        resp = client.get("/api/status")
        data = json.loads(resp.data)
        assert "autoplay_current" in data
        assert data["autoplay_current"]["video_id"] is None

    def test_autoplay_current_cleared_on_manual_play(self, client):
        # Play something manually
        resp = client.post("/api/play", json={"url": "https://www.youtube.com/watch?v=test1234567"})
        data = json.loads(resp.data)
        # Check status - autoplay_current should be None
        resp = client.get("/api/status")
        data = json.loads(resp.data)
        assert data["autoplay_current"]["video_id"] is None

    def test_autoplay_current_cleared_on_stop(self, client):
        resp = client.post("/api/stop")
        data = json.loads(resp.data)
        assert data["ok"]
        resp = client.get("/api/status")
        data = json.loads(resp.data)
        assert data["autoplay_current"]["video_id"] is None


class TestPoolPage:
    """Test pool management page rendering."""

    def test_pool_page_renders(self, client):
        resp = client.get("/pool")
        assert resp.status_code == 200
        assert b"AutoPlay Pool" in resp.data

    def test_pool_page_has_pool_blocks_section(self, client):
        resp = client.get("/pool")
        assert b"pool-blocks" in resp.data

    def test_pool_page_has_history_section(self, client):
        resp = client.get("/pool")
        assert b"pool-history" in resp.data


class TestPoolCLIParsing:
    """Test CLI argument parsing (no server needed)."""

    def test_import_exists(self):
        """Verify the run_pool_cli entry point is importable."""
        from picast.cli import run_pool_cli
        assert callable(run_pool_cli)


class TestNavPills:
    """Test that Pool nav pill appears on all pages."""

    def test_pool_pill_on_queue_page(self, client):
        resp = client.get("/")
        assert b'href="/pool"' in resp.data

    def test_pool_pill_active_on_pool_page(self, client):
        resp = client.get("/pool")
        assert b'btn-dice-active' in resp.data


# --- Self-Learning: record_completion ---

class TestRecordCompletion:
    def test_increments_completion_count(self, pool):
        pool.add_video("test", "https://www.youtube.com/watch?v=abc12345678", "Test")
        ok = pool.record_completion("test", "abc12345678")
        assert ok is True
        video = pool.get_video("test", "abc12345678")
        assert video["completion_count"] == 1

    def test_multiple_completions(self, pool):
        pool.add_video("test", "https://www.youtube.com/watch?v=abc12345678", "Test")
        pool.record_completion("test", "abc12345678")
        pool.record_completion("test", "abc12345678")
        pool.record_completion("test", "abc12345678")
        video = pool.get_video("test", "abc12345678")
        assert video["completion_count"] == 3

    def test_nonexistent_returns_false(self, pool):
        assert pool.record_completion("nope", "nope") is False


# --- Self-Learning: record_skip ---

class TestRecordSkip:
    def test_increments_skip_count(self, pool):
        pool.add_video("test", "https://www.youtube.com/watch?v=abc12345678", "Test")
        count = pool.record_skip("test", "abc12345678")
        assert count == 1
        video = pool.get_video("test", "abc12345678")
        assert video["skip_count"] == 1

    def test_nonexistent_returns_negative(self, pool):
        assert pool.record_skip("nope", "nope") == -1

    def test_auto_shelve_at_threshold(self, pool):
        pool.add_video("test", "https://www.youtube.com/watch?v=abc12345678", "Test")
        for _ in range(5):
            pool.record_skip("test", "abc12345678")
        # Video should be auto-shelved (active=0)
        active = pool.get_pool("test")
        assert len(active) == 0
        # But still exists when including retired
        all_vids = pool.get_pool("test", include_retired=True)
        assert len(all_vids) == 1
        assert all_vids[0]["skip_count"] == 5

    def test_no_auto_shelve_below_threshold(self, pool):
        pool.add_video("test", "https://www.youtube.com/watch?v=abc12345678", "Test")
        for _ in range(4):
            pool.record_skip("test", "abc12345678")
        active = pool.get_pool("test")
        assert len(active) == 1


# --- Self-Learning: update_last_history ---

class TestUpdateLastHistory:
    def test_updates_most_recent_history(self, pool):
        pool.add_video("test", "https://www.youtube.com/watch?v=abc12345678", "Test")
        pool.select_video("test")  # creates a history row
        ok = pool.update_last_history(
            "abc12345678", "test",
            duration_watched=120, completed=1, stop_reason="completed",
        )
        assert ok is True
        history = pool.get_history("test")
        assert history[0]["duration_watched"] == 120
        assert history[0]["completed"] == 1
        assert history[0]["stop_reason"] == "completed"

    def test_nonexistent_returns_false(self, pool):
        assert pool.update_last_history("nope", "nope") is False


# --- Self-Learning: weight formula ---

class TestSelfLearningWeights:
    def test_skip_penalty_reduces_selection(self, pool):
        """Videos with skips should be selected less often."""
        pool.add_video("test", "https://www.youtube.com/watch?v=skipped1111", "Skipped")
        pool.add_video("test", "https://www.youtube.com/watch?v=normal11111", "Normal")
        # Add 3 skips to one video
        for _ in range(3):
            pool.record_skip("test", "skipped1111")
        pool.avoid_recent = 0

        counts = {"skipped1111": 0, "normal11111": 0}
        for _ in range(200):
            result = pool.select_video("test")
            counts[result["video_id"]] += 1

        # Normal (weight 1.0) should heavily dominate skipped (weight 1.0 * 0.7^3 = 0.343)
        assert counts["normal11111"] > counts["skipped1111"]

    def test_completion_boost_increases_selection(self, pool):
        """Videos with completions should be selected more often."""
        pool.add_video("test", "https://www.youtube.com/watch?v=complet1111", "Completed")
        pool.add_video("test", "https://www.youtube.com/watch?v=normal11111", "Normal")
        for _ in range(5):
            pool.record_completion("test", "complet1111")
        pool.avoid_recent = 0

        counts = {"complet1111": 0, "normal11111": 0}
        for _ in range(200):
            result = pool.select_video("test")
            counts[result["video_id"]] += 1

        # Completed (weight 1.0 * 2.0 cap) should dominate normal (weight 1.0)
        assert counts["complet1111"] > counts["normal11111"]

    def test_completion_boost_capped_at_2x(self, pool):
        """Completion boost caps at 2.0x regardless of count."""
        pool.add_video("test", "https://www.youtube.com/watch?v=complet1111", "Completed")
        pool.add_video("test", "https://www.youtube.com/watch?v=normal11111", "Normal")
        # 100 completions should still cap at 2.0x
        for _ in range(100):
            pool.record_completion("test", "complet1111")
        pool.avoid_recent = 0

        counts = {"complet1111": 0, "normal11111": 0}
        for _ in range(300):
            result = pool.select_video("test")
            counts[result["video_id"]] += 1

        # With 2:1 weight ratio, completed should get roughly 2/3 of selections
        ratio = counts["complet1111"] / max(counts["normal11111"], 1)
        assert 1.2 < ratio < 4.0, f"Ratio {ratio} outside expected 2:1 range"


# --- Schema v7 Migration ---

class TestSchemaV7:
    def test_new_columns_exist(self, db):
        """Verify schema v7 columns exist."""
        row = db.fetchone("SELECT skip_count, completion_count, duration FROM autoplay_videos LIMIT 0")
        # No error means columns exist (empty table returns None)

    def test_history_stop_reason_exists(self, db):
        """Verify stop_reason column in autoplay_history."""
        row = db.fetchone("SELECT stop_reason FROM autoplay_history LIMIT 0")

    def test_add_video_with_duration(self, pool):
        result = pool.add_video(
            "test", "https://www.youtube.com/watch?v=abc12345678",
            "Test", duration=3600,
        )
        assert result is not None
        assert result["duration"] == 3600


# --- Player Callback ---

class TestPlayerCallback:
    def test_on_item_complete_attribute(self):
        """Verify Player has on_item_complete attribute."""
        from picast.server.player import Player
        from unittest.mock import MagicMock
        mpv = MagicMock()
        queue = MagicMock()
        p = Player(mpv, queue)
        assert hasattr(p, "on_item_complete")
        assert p.on_item_complete is None


# --- Autoplay Snapshot (API) ---

class TestAutoplaySnapshot:
    def test_skip_clears_autoplay_current(self, client):
        """Verify /api/skip clears autoplay_current."""
        client.post("/api/skip")
        resp = client.get("/api/status")
        data = json.loads(resp.data)
        assert data["autoplay_current"]["video_id"] is None

    def test_stop_clears_autoplay_current(self, client):
        """Verify /api/stop clears autoplay_current."""
        client.post("/api/stop")
        resp = client.get("/api/status")
        data = json.loads(resp.data)
        assert data["autoplay_current"]["video_id"] is None


# --- Schema v9: Seasonal + Cross-Block Tables ---

class TestSchemaV9:
    def test_seasonal_tags_table_exists(self, db):
        """Verify autoplay_seasonal_tags table was created."""
        row = db.fetchone("SELECT COUNT(*) as cnt FROM autoplay_seasonal_tags")
        assert row["cnt"] == 0

    def test_cross_block_prefs_table_exists(self, db):
        """Verify autoplay_cross_block_prefs table was created."""
        row = db.fetchone("SELECT COUNT(*) as cnt FROM autoplay_cross_block_prefs")
        assert row["cnt"] == 0

    def test_seasonal_tags_unique_constraint(self, db):
        """Inserting same video+season pair should be ignored."""
        db.execute(
            "INSERT INTO autoplay_seasonal_tags (video_id, season) VALUES (?, ?)",
            ("vid1", "winter"),
        )
        db.commit()
        db.execute(
            "INSERT OR IGNORE INTO autoplay_seasonal_tags (video_id, season) VALUES (?, ?)",
            ("vid1", "winter"),
        )
        db.commit()
        rows = db.fetchall("SELECT * FROM autoplay_seasonal_tags WHERE video_id = 'vid1'")
        assert len(rows) == 1

    def test_cross_block_prefs_unique_constraint(self, db):
        """Inserting same video+block+signal combo should be ignored."""
        db.execute(
            "INSERT INTO autoplay_cross_block_prefs "
            "(video_id, source_block, signal_type, signal_strength, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("vid1", "morning", "liked", 1.0, "2026-01-01T00:00:00"),
        )
        db.commit()
        db.execute(
            "INSERT OR IGNORE INTO autoplay_cross_block_prefs "
            "(video_id, source_block, signal_type, signal_strength, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("vid1", "morning", "liked", 2.0, "2026-01-02T00:00:00"),
        )
        db.commit()
        rows = db.fetchall(
            "SELECT * FROM autoplay_cross_block_prefs WHERE video_id = 'vid1'"
        )
        assert len(rows) == 1
        assert rows[0]["signal_strength"] == 1.0  # Original kept

    def test_migration_from_v8(self, tmp_path):
        """Simulate migration from v8 to v9."""
        import sqlite3
        db_path = str(tmp_path / "migrate.db")
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        # Create minimal v8 schema with version table
        conn.execute("CREATE TABLE schema_version (version INTEGER NOT NULL)")
        conn.execute("INSERT INTO schema_version (version) VALUES (8)")
        # Minimal autoplay tables from v6+
        conn.execute("""
            CREATE TABLE autoplay_videos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                video_id TEXT NOT NULL, title TEXT NOT NULL DEFAULT '',
                block_name TEXT NOT NULL, tags TEXT NOT NULL DEFAULT '',
                rating INTEGER NOT NULL DEFAULT 0, play_count INTEGER NOT NULL DEFAULT 0,
                last_played TEXT, added_date TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'manual', active INTEGER NOT NULL DEFAULT 1,
                skip_count INTEGER NOT NULL DEFAULT 0,
                completion_count INTEGER NOT NULL DEFAULT 0,
                duration INTEGER NOT NULL DEFAULT 0,
                UNIQUE(video_id, block_name)
            )
        """)
        conn.execute("""
            CREATE TABLE autoplay_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                video_id TEXT NOT NULL, block_name TEXT NOT NULL,
                played_at TEXT NOT NULL, duration_watched INTEGER NOT NULL DEFAULT 0,
                completed INTEGER NOT NULL DEFAULT 0,
                stop_reason TEXT NOT NULL DEFAULT ''
            )
        """)
        conn.execute("""
            CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)
        """)
        conn.commit()
        conn.close()

        # Open with Database class — should auto-migrate to v9
        from picast.server.database import Database
        db = Database(db_path)
        version = db.fetchone("SELECT version FROM schema_version")
        assert version["version"] == 10
        # New tables should exist
        db.fetchone("SELECT COUNT(*) FROM autoplay_seasonal_tags")
        db.fetchone("SELECT COUNT(*) FROM autoplay_cross_block_prefs")


# --- Seasonal Tag CRUD ---

class TestSeasonalTagCRUD:
    def test_set_and_get(self, pool):
        pool.add_video("test", "https://www.youtube.com/watch?v=abc12345678", "Test")
        pool.set_seasonal_tags("abc12345678", ["winter", "holiday"])
        tags = pool.get_seasonal_tags("abc12345678")
        assert sorted(tags) == ["holiday", "winter"]

    def test_set_replaces_existing(self, pool):
        pool.add_video("test", "https://www.youtube.com/watch?v=abc12345678", "Test")
        pool.set_seasonal_tags("abc12345678", ["winter"])
        pool.set_seasonal_tags("abc12345678", ["summer", "fall"])
        tags = pool.get_seasonal_tags("abc12345678")
        assert sorted(tags) == ["fall", "summer"]
        assert "winter" not in tags

    def test_get_empty(self, pool):
        pool.add_video("test", "https://www.youtube.com/watch?v=abc12345678", "Test")
        assert pool.get_seasonal_tags("abc12345678") == []

    def test_remove_single_tag(self, pool):
        pool.add_video("test", "https://www.youtube.com/watch?v=abc12345678", "Test")
        pool.set_seasonal_tags("abc12345678", ["winter", "holiday"])
        removed = pool.remove_seasonal_tag("abc12345678", "winter")
        assert removed is True
        tags = pool.get_seasonal_tags("abc12345678")
        assert tags == ["holiday"]

    def test_remove_nonexistent_tag(self, pool):
        pool.add_video("test", "https://www.youtube.com/watch?v=abc12345678", "Test")
        assert pool.remove_seasonal_tag("abc12345678", "spring") is False

    def test_get_all_seasons(self, pool):
        pool.add_video("a", "https://www.youtube.com/watch?v=aaaaaaaaaaa", "A")
        pool.add_video("b", "https://www.youtube.com/watch?v=bbbbbbbbbbb", "B")
        pool.set_seasonal_tags("aaaaaaaaaaa", ["winter", "holiday"])
        pool.set_seasonal_tags("bbbbbbbbbbb", ["winter"])
        seasons = pool.get_all_seasons()
        season_map = {s["season"]: s["video_count"] for s in seasons}
        assert season_map["winter"] == 2
        assert season_map["holiday"] == 1

    def test_case_normalization(self, pool):
        pool.add_video("test", "https://www.youtube.com/watch?v=abc12345678", "Test")
        pool.set_seasonal_tags("abc12345678", ["Winter", " SUMMER "])
        tags = pool.get_seasonal_tags("abc12345678")
        assert sorted(tags) == ["summer", "winter"]

    def test_empty_strings_ignored(self, pool):
        pool.add_video("test", "https://www.youtube.com/watch?v=abc12345678", "Test")
        pool.set_seasonal_tags("abc12345678", ["winter", "", "  "])
        tags = pool.get_seasonal_tags("abc12345678")
        assert tags == ["winter"]


# --- Pool Export ---

class TestExportPools:
    def test_export_empty(self, pool):
        data = pool.export_pools()
        assert data["blocks"] == {}

    def test_export_includes_videos(self, pool):
        _seed_pool(pool)
        data = pool.export_pools()
        assert "morning-foundation" in data["blocks"]
        assert len(data["blocks"]["morning-foundation"]) == 3
        assert len(data["blocks"]["clean-mama"]) == 2

    def test_export_includes_ratings(self, pool):
        pool.add_video("test", "https://www.youtube.com/watch?v=abc12345678", "Test")
        pool.rate_video("test", "abc12345678", 1)
        data = pool.export_pools()
        video = data["blocks"]["test"][0]
        assert video["rating"] == 1

    def test_export_includes_seasonal_tags(self, pool):
        pool.add_video("test", "https://www.youtube.com/watch?v=abc12345678", "Test")
        pool.set_seasonal_tags("abc12345678", ["winter", "holiday"])
        data = pool.export_pools()
        video = data["blocks"]["test"][0]
        assert sorted(video["seasons"]) == ["holiday", "winter"]

    def test_export_excludes_ephemeral_state(self, pool):
        pool.add_video("test", "https://www.youtube.com/watch?v=abc12345678", "Test")
        pool.record_completion("test", "abc12345678")
        pool.record_skip("test", "abc12345678")
        pool.select_video("test")  # increments play_count
        data = pool.export_pools()
        video = data["blocks"]["test"][0]
        assert "skip_count" not in video
        assert "completion_count" not in video
        assert "play_count" not in video
        assert "last_played" not in video

    def test_export_includes_retired_videos(self, pool):
        pool.add_video("test", "https://www.youtube.com/watch?v=abc12345678", "Active")
        pool.add_video("test", "https://www.youtube.com/watch?v=def12345678", "Retired")
        pool.remove_video("test", "def12345678")
        data = pool.export_pools()
        videos = data["blocks"]["test"]
        assert len(videos) == 2
        active_states = {v["video_id"]: v["active"] for v in videos}
        assert active_states["abc12345678"] is True
        assert active_states["def12345678"] is False

    def test_export_no_seasons_key_when_empty(self, pool):
        pool.add_video("test", "https://www.youtube.com/watch?v=abc12345678", "Test")
        data = pool.export_pools()
        video = data["blocks"]["test"][0]
        assert "seasons" not in video


# --- Pool Import ---

class TestImportPools:
    def test_import_basic(self, pool):
        data = {
            "blocks": {
                "my-block": [
                    {"video_id": "abc12345678", "title": "Test Video"},
                ]
            }
        }
        stats = pool.import_pools(data)
        assert stats["added"] == 1
        assert stats["blocks"] == 1
        videos = pool.get_pool("my-block")
        assert len(videos) == 1
        assert videos[0]["title"] == "Test Video"

    def test_import_with_ratings(self, pool):
        data = {
            "blocks": {
                "test": [
                    {"video_id": "abc12345678", "title": "Liked", "rating": 1},
                    {"video_id": "def12345678", "title": "Disliked", "rating": -1},
                ]
            }
        }
        pool.import_pools(data)
        liked = pool.get_video("test", "abc12345678")
        assert liked["rating"] == 1
        disliked = pool.get_video("test", "def12345678")
        assert disliked["rating"] == -1

    def test_import_with_seasonal_tags(self, pool):
        data = {
            "blocks": {
                "test": [
                    {"video_id": "abc12345678", "title": "Winter", "seasons": ["winter", "holiday"]},
                ]
            }
        }
        pool.import_pools(data)
        tags = pool.get_seasonal_tags("abc12345678")
        assert sorted(tags) == ["holiday", "winter"]

    def test_import_merge_adds_new(self, pool):
        """Merge mode adds new videos without removing existing."""
        pool.add_video("test", "https://www.youtube.com/watch?v=existing_v1", "Existing")
        data = {
            "blocks": {
                "test": [
                    {"video_id": "newvideo_v1", "title": "New"},
                ]
            }
        }
        stats = pool.import_pools(data, merge=True)
        assert stats["added"] == 1
        videos = pool.get_pool("test")
        assert len(videos) == 2

    def test_import_merge_skips_duplicates(self, pool):
        pool.add_video("test", "https://www.youtube.com/watch?v=abc12345678", "Existing")
        data = {
            "blocks": {
                "test": [
                    {"video_id": "abc12345678", "title": "Duplicate"},
                ]
            }
        }
        stats = pool.import_pools(data, merge=True)
        assert stats["skipped"] == 1
        assert stats["added"] == 0

    def test_import_replace_deactivates_existing(self, pool):
        """Replace mode deactivates existing videos before importing."""
        pool.add_video("test", "https://www.youtube.com/watch?v=old_video_1", "Old")
        data = {
            "blocks": {
                "test": [
                    {"video_id": "new_video_1", "title": "New"},
                ]
            }
        }
        stats = pool.import_pools(data, merge=False)
        assert stats["added"] == 1
        # Old video should be deactivated
        active = pool.get_pool("test")
        assert len(active) == 1
        assert active[0]["video_id"] == "new_video_1"
        # Old still exists as retired
        all_vids = pool.get_pool("test", include_retired=True)
        assert len(all_vids) == 2

    def test_import_inactive_video(self, pool):
        """Importing with active=false creates then retires the video."""
        data = {
            "blocks": {
                "test": [
                    {"video_id": "abc12345678", "title": "Shelved", "active": False},
                ]
            }
        }
        pool.import_pools(data)
        active = pool.get_pool("test")
        assert len(active) == 0
        retired = pool.get_pool("test", include_retired=True)
        assert len(retired) == 1

    def test_import_skips_empty_video_id(self, pool):
        data = {
            "blocks": {
                "test": [
                    {"video_id": "", "title": "No ID"},
                    {"title": "Missing ID field"},
                ]
            }
        }
        stats = pool.import_pools(data)
        assert stats["added"] == 0

    def test_export_import_round_trip(self, pool):
        """Export -> import into fresh pool should reproduce identical data."""
        # Seed with diverse data (all video IDs must be exactly 11 chars)
        pool.add_video("morning", "https://www.youtube.com/watch?v=mornJazz111", "Morning Jazz", tags="jazz,calm")
        pool.add_video("morning", "https://www.youtube.com/watch?v=mornClass11", "Morning Classical")
        pool.add_video("evening", "https://www.youtube.com/watch?v=eveChill111", "Evening Chill")
        pool.rate_video("morning", "mornJazz111", 1)
        pool.rate_video("morning", "mornClass11", -1)
        pool.set_seasonal_tags("mornJazz111", ["winter", "holiday"])
        pool.set_seasonal_tags("eveChill111", ["summer"])
        pool.remove_video("morning", "mornClass11")  # Retire one

        # Export
        exported = pool.export_pools()

        # Import into fresh pool
        from picast.server.database import Database
        db2 = Database(str(pool.db.db_path).replace("test.db", "test2.db"))
        pool2 = AutoPlayPool(db2, avoid_recent=2)
        stats = pool2.import_pools(exported)

        # Verify round-trip
        assert stats["blocks"] == 2
        morning = pool2.get_pool("morning", include_retired=True)
        assert len(morning) == 2
        liked = pool2.get_video("morning", "mornJazz111")
        assert liked["rating"] == 1
        disliked = pool2.get_video("morning", "mornClass11")
        assert disliked["rating"] == -1
        assert disliked["active"] == 0  # Still retired

        evening = pool2.get_pool("evening")
        assert len(evening) == 1

        # Seasonal tags preserved
        tags1 = pool2.get_seasonal_tags("mornJazz111")
        assert sorted(tags1) == ["holiday", "winter"]
        tags2 = pool2.get_seasonal_tags("eveChill111")
        assert tags2 == ["summer"]


# --- API Endpoint Tests: Export / Import ---

class TestExportImportAPI:
    def test_export_empty(self, client):
        resp = client.get("/api/autoplay/export")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert "blocks" in data

    def test_export_with_data(self, client):
        client.post("/api/autoplay/pool/test", json={
            "url": "https://www.youtube.com/watch?v=abc12345678", "title": "Test",
        })
        resp = client.get("/api/autoplay/export")
        data = json.loads(resp.data)
        assert "test" in data["blocks"]
        assert len(data["blocks"]["test"]) == 1

    def test_import_json(self, client):
        import_data = {
            "blocks": {
                "imported": [
                    {"video_id": "imp_video_1", "title": "Imported Video"},
                ]
            }
        }
        resp = client.post(
            "/api/autoplay/import",
            json=import_data,
            content_type="application/json",
        )
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["ok"] is True
        assert data["added"] == 1
        assert data["blocks"] == 1

        # Verify it's in the pool
        resp = client.get("/api/autoplay/pool/imported")
        pool_data = json.loads(resp.data)
        assert len(pool_data) == 1

    def test_import_merge_param(self, client):
        # Add existing video
        client.post("/api/autoplay/pool/test", json={
            "url": "https://www.youtube.com/watch?v=existing_v1", "title": "Existing",
        })
        # Import with merge (default)
        import_data = {
            "blocks": {
                "test": [
                    {"video_id": "newvideo_v1", "title": "New"},
                ]
            }
        }
        resp = client.post("/api/autoplay/import", json=import_data)
        data = json.loads(resp.data)
        assert data["added"] == 1

        # Both should exist
        resp = client.get("/api/autoplay/pool/test")
        pool_data = json.loads(resp.data)
        assert len(pool_data) == 2

    def test_import_replace_mode(self, client):
        # Add existing video
        client.post("/api/autoplay/pool/test", json={
            "url": "https://www.youtube.com/watch?v=existing_v1", "title": "Existing",
        })
        import_data = {
            "blocks": {
                "test": [
                    {"video_id": "replace_vid", "title": "Replacement"},
                ]
            }
        }
        resp = client.post("/api/autoplay/import?merge=0", json=import_data)
        data = json.loads(resp.data)
        assert data["added"] == 1

        # Only replacement should be active
        resp = client.get("/api/autoplay/pool/test")
        pool_data = json.loads(resp.data)
        assert len(pool_data) == 1
        assert pool_data[0]["video_id"] == "replace_vid"

    def test_import_invalid_body(self, client):
        resp = client.post(
            "/api/autoplay/import",
            data="not json",
            content_type="application/json",
        )
        assert resp.status_code == 400

    def test_export_import_round_trip_api(self, client):
        """Full round-trip: add videos -> export -> import into same pool."""
        # Add some videos
        client.post("/api/autoplay/pool/test", json={
            "url": "https://www.youtube.com/watch?v=roundTrip_1", "title": "Round Trip",
        })

        # Export
        resp = client.get("/api/autoplay/export")
        exported = json.loads(resp.data)
        assert "test" in exported["blocks"]

        # Re-import (merge — duplicate gets skipped)
        resp = client.post("/api/autoplay/import", json=exported)
        data = json.loads(resp.data)
        assert data["ok"] is True
        assert data["skipped"] == 1  # Already exists


# --- Cross-Block Signal Emission ---

class TestCrossBlockSignals:
    def test_like_emits_signal(self, pool):
        """Liking a video should emit a cross-block signal."""
        pool.add_video("morning", "https://www.youtube.com/watch?v=abc12345678", "Test")
        pool.rate_video("morning", "abc12345678", 1)
        signals = pool.get_cross_block_signals("abc12345678")
        assert len(signals) == 1
        assert signals[0]["source_block"] == "morning"
        assert signals[0]["signal_type"] == "liked"
        assert signals[0]["signal_strength"] == 1.5

    def test_dislike_no_signal(self, pool):
        """Disliking should NOT emit a cross-block signal."""
        pool.add_video("morning", "https://www.youtube.com/watch?v=abc12345678", "Test")
        pool.rate_video("morning", "abc12345678", -1)
        signals = pool.get_cross_block_signals("abc12345678")
        assert len(signals) == 0

    def test_neutral_no_signal(self, pool):
        """Neutral rating should NOT emit a cross-block signal."""
        pool.add_video("morning", "https://www.youtube.com/watch?v=abc12345678", "Test")
        pool.rate_video("morning", "abc12345678", 0)
        signals = pool.get_cross_block_signals("abc12345678")
        assert len(signals) == 0

    def test_completion_5x_emits_signal(self, pool):
        """5 completions should emit a cross-block signal."""
        pool.add_video("morning", "https://www.youtube.com/watch?v=abc12345678", "Test")
        for _ in range(4):
            pool.record_completion("morning", "abc12345678")
        # No signal yet at 4
        signals = pool.get_cross_block_signals("abc12345678")
        assert len(signals) == 0
        # 5th completion triggers signal
        pool.record_completion("morning", "abc12345678")
        signals = pool.get_cross_block_signals("abc12345678")
        assert len(signals) == 1
        assert signals[0]["signal_type"] == "completed_5x"

    def test_no_signal_when_disabled(self, db):
        """Cross-block signals should not emit when disabled."""
        pool = AutoPlayPool(db, avoid_recent=2, cross_block_learning=False)
        pool.add_video("morning", "https://www.youtube.com/watch?v=abc12345678", "Test")
        pool.rate_video("morning", "abc12345678", 1)
        signals = pool.get_cross_block_signals("abc12345678")
        assert len(signals) == 0

    def test_signal_replaces_on_same_type(self, pool):
        """Repeated like should update signal, not create duplicate."""
        pool.add_video("morning", "https://www.youtube.com/watch?v=abc12345678", "Test")
        pool.rate_video("morning", "abc12345678", 1)
        pool.rate_video("morning", "abc12345678", 0)  # neutral, no signal
        pool.rate_video("morning", "abc12345678", 1)  # like again
        signals = pool.get_cross_block_signals("abc12345678")
        assert len(signals) == 1  # replaced, not duplicated


# --- Cross-Block Suggestions ---

class TestCrossBlockSuggestions:
    def test_no_suggestions_empty(self, pool):
        suggestions = pool.get_cross_block_suggestions("evening")
        assert suggestions == []

    def test_liked_video_suggests_to_other_block(self, pool):
        """A liked video in morning should appear as suggestion for evening."""
        pool.add_video("morning", "https://www.youtube.com/watch?v=abc12345678", "Morning Fav")
        pool.rate_video("morning", "abc12345678", 1)  # emits cross-block signal
        suggestions = pool.get_cross_block_suggestions("evening")
        assert len(suggestions) == 1
        assert suggestions[0]["video_id"] == "abc12345678"
        assert suggestions[0]["source_block"] == "morning"

    def test_no_self_suggest(self, pool):
        """A video should NOT be suggested for its own block."""
        pool.add_video("morning", "https://www.youtube.com/watch?v=abc12345678", "Test")
        pool.rate_video("morning", "abc12345678", 1)
        suggestions = pool.get_cross_block_suggestions("morning")
        assert len(suggestions) == 0

    def test_already_in_target_not_suggested(self, pool):
        """A video already in the target block should not be suggested."""
        pool.add_video("morning", "https://www.youtube.com/watch?v=abc12345678", "Test")
        pool.add_video("evening", "https://www.youtube.com/watch?v=abc12345678", "Test")
        pool.rate_video("morning", "abc12345678", 1)
        suggestions = pool.get_cross_block_suggestions("evening")
        assert len(suggestions) == 0

    def test_suggestions_sorted_by_strength(self, pool):
        """Suggestions should be sorted by aggregate signal strength."""
        pool.add_video("morning", "https://www.youtube.com/watch?v=weak_vid_11", "Weak")
        pool.add_video("morning", "https://www.youtube.com/watch?v=strong_vid1", "Strong")
        pool.rate_video("morning", "weak_vid_11", 1)  # 1.5 strength
        pool.rate_video("morning", "strong_vid1", 1)  # 1.5 strength
        # Add extra signal for strong video
        for _ in range(5):
            pool.record_completion("morning", "strong_vid1")  # +1.0 at 5th
        suggestions = pool.get_cross_block_suggestions("evening")
        assert len(suggestions) == 2
        # Strong should be first (1.5 liked + 1.0 completed_5x = 2.5)
        assert suggestions[0]["video_id"] == "strong_vid1"

    def test_suggestions_limit(self, pool):
        suggestions = pool.get_cross_block_suggestions("evening", limit=1)
        assert len(suggestions) <= 1


# --- Cross-Block API Endpoints ---

class TestCrossBlockAPI:
    def test_suggestions_empty(self, client):
        resp = client.get("/api/autoplay/suggestions/test-block")
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data == []

    def test_suggestions_with_data(self, client):
        # Add and like a video in block-a
        client.post("/api/autoplay/pool/block-a", json={
            "url": "https://www.youtube.com/watch?v=abc12345678", "title": "Fav",
        })
        client.post("/api/autoplay/rate", json={
            "video_id": "abc12345678", "block_name": "block-a", "rating": 1,
        })
        # Check suggestions for block-b
        resp = client.get("/api/autoplay/suggestions/block-b")
        data = json.loads(resp.data)
        assert len(data) == 1
        assert data[0]["video_id"] == "abc12345678"

    def test_accept_suggestion(self, client):
        # Add and like in block-a
        client.post("/api/autoplay/pool/block-a", json={
            "url": "https://www.youtube.com/watch?v=abc12345678", "title": "Fav",
        })
        client.post("/api/autoplay/rate", json={
            "video_id": "abc12345678", "block_name": "block-a", "rating": 1,
        })
        # Accept for block-b
        resp = client.post("/api/autoplay/suggestions/block-b/accept", json={
            "video_id": "abc12345678", "source_block": "block-a",
        })
        assert resp.status_code == 201
        data = json.loads(resp.data)
        assert data["video_id"] == "abc12345678"
        assert data["block_name"] == "block-b"

        # Should now be in block-b's pool
        resp = client.get("/api/autoplay/pool/block-b")
        pool_data = json.loads(resp.data)
        assert len(pool_data) == 1

    def test_accept_duplicate_returns_409(self, client):
        # Add video to both blocks
        client.post("/api/autoplay/pool/block-a", json={
            "url": "https://www.youtube.com/watch?v=abc12345678", "title": "Fav",
        })
        client.post("/api/autoplay/pool/block-b", json={
            "url": "https://www.youtube.com/watch?v=abc12345678", "title": "Fav",
        })
        # Try to accept — already exists
        resp = client.post("/api/autoplay/suggestions/block-b/accept", json={
            "video_id": "abc12345678", "source_block": "block-a",
        })
        assert resp.status_code == 409

    def test_dismiss_suggestion(self, client):
        resp = client.post("/api/autoplay/suggestions/block-b/dismiss", json={
            "video_id": "abc12345678",
        })
        assert resp.status_code == 200
        data = json.loads(resp.data)
        assert data["ok"] is True

    def test_accept_missing_video_id(self, client):
        resp = client.post("/api/autoplay/suggestions/block-b/accept", json={})
        assert resp.status_code == 400


# --- Config Flags ---

class TestConfigFlags:
    def test_cross_block_learning_default_true(self):
        from picast.config import AutoplayConfig
        c = AutoplayConfig()
        assert c.cross_block_learning is True

    def test_cross_block_disabled(self, db):
        pool = AutoPlayPool(db, avoid_recent=2, cross_block_learning=False)
        pool.add_video("test", "https://www.youtube.com/watch?v=abc12345678", "Test")
        pool.rate_video("test", "abc12345678", 1)
        # With cross_block_learning disabled, no signal should be emitted
        signals = pool.get_cross_block_signals("abc12345678")
        assert len(signals) == 0

    def test_config_parsing(self):
        from picast.config import _parse_config
        data = {
            "autoplay": {
                "enabled": True,
                "pool_mode": True,
                "cross_block_learning": False,
            }
        }
        config = _parse_config(data)
        assert config.autoplay.cross_block_learning is False
