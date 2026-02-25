"""Tests for AutoPlay pool system."""

import json

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
