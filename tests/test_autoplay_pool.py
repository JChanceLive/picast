"""Tests for AutoPlay pool system."""

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
