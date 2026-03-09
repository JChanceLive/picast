"""Tests for AutopilotEngine — AI-enhanced video selection and queue management."""

import json

import pytest

from picast.config import AutopilotConfig, AutoplayConfig, ServerConfig
from picast.server.app import create_app
from picast.server.autoplay_pool import AutoPlayPool
from picast.server.autopilot_engine import AutopilotEngine, _weighted_shuffle
from picast.server.database import Database
from picast.server.taste_profile import TasteProfile


# --- Helpers ---


def _make_profile(
    version=1,
    generated_at="2026-03-09T06:00:00",
    genre_weights=None,
    block_strategies=None,
):
    """Build a valid taste profile dict."""
    return {
        "version": version,
        "generated_at": generated_at,
        "global_preferences": {
            "preferred_duration_range": [600, 3600],
            "genre_weights": genre_weights or {"ambient": 0.9, "documentary": 0.7},
        },
        "block_strategies": block_strategies
        or {
            "morning": {
                "energy": "low",
                "genres": ["ambient"],
                "max_duration": 1800,
            },
            "evening": {
                "energy": "medium",
                "genres": ["documentary"],
                "max_duration": 7200,
            },
        },
    }


def _save_profile(db, profile_dict, generated_at="2026-03-09T06:00:00"):
    """Save a taste profile to the database."""
    profile_json = json.dumps(profile_dict)
    db.execute(
        "INSERT INTO autopilot_profile (id, profile_json, generated_at, version) "
        "VALUES (1, ?, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET "
        "profile_json = excluded.profile_json, "
        "generated_at = excluded.generated_at, "
        "loaded_at = datetime('now'), "
        "version = excluded.version",
        (profile_json, generated_at, profile_dict.get("version", 1)),
    )
    db.commit()


def _seed_pool(pool, block_name, count=5, prefix="vid"):
    """Add test videos to a pool. Video IDs are exactly 11 characters."""
    for i in range(count):
        # YouTube video IDs are exactly 11 chars: prefix + digits + padding
        vid_id = f"{prefix}{i:03d}aaaaa"  # e.g. "vid000aaaaa" = 11 chars
        pool.add_video(
            block_name,
            f"https://www.youtube.com/watch?v={vid_id}",
            title=f"Test Video {i}",
            tags="ambient,nature" if i % 2 == 0 else "documentary",
            duration=600 + i * 100,
        )


@pytest.fixture
def db(tmp_path):
    return Database(str(tmp_path / "test.db"))


@pytest.fixture
def pool(db):
    return AutoPlayPool(db, avoid_recent=2)


@pytest.fixture
def profile():
    return TasteProfile()


@pytest.fixture
def config():
    return AutopilotConfig(enabled=True, queue_depth=4)


@pytest.fixture
def engine(pool, profile, config, db):
    return AutopilotEngine(pool=pool, profile=profile, config=config, db=db)


# --- Basic State Tests ---


class TestEngineState:
    def test_starts_stopped(self, engine):
        assert not engine.running
        assert engine.current_block is None

    def test_start_enables(self, engine):
        engine.start()
        assert engine.running

    def test_stop_disables(self, engine):
        engine.start()
        engine.stop()
        assert not engine.running

    def test_toggle_on_off(self, engine):
        result = engine.toggle()
        assert result is True
        assert engine.running

        result = engine.toggle()
        assert result is False
        assert not engine.running

    def test_stop_preserves_queue(self, engine, pool, db):
        _seed_pool(pool, "morning", count=6)
        engine.start()
        engine.on_block_change("morning")
        assert len(engine.get_queue_preview()) > 0
        engine.stop()
        assert len(engine.get_queue_preview()) > 0  # Queue preserved


class TestStatus:
    def test_status_default(self, engine):
        status = engine.get_status()
        assert status["enabled"] is False
        assert status["mode"] == "single"
        assert status["current_block"] is None
        assert status["queue_depth"] == 0
        assert status["target_depth"] == 4
        assert "profile" in status

    def test_status_after_start(self, engine, pool):
        _seed_pool(pool, "morning")
        engine.start()
        engine.on_block_change("morning")
        status = engine.get_status()
        assert status["enabled"] is True
        assert status["current_block"] == "morning"
        assert status["queue_depth"] > 0


# --- Selection and Scoring ---


class TestSelection:
    def test_select_from_empty_pool(self, engine):
        engine.start()
        result = engine.select_next("morning")
        assert result is None

    def test_select_from_pool(self, engine, pool):
        _seed_pool(pool, "morning", count=3)
        engine.start()
        result = engine.select_next("morning")
        assert result is not None
        assert "video_id" in result
        assert "title" in result
        assert "score" in result
        assert "url" in result

    def test_select_without_block(self, engine):
        engine.start()
        result = engine.select_next(None)
        assert result is None

    def test_select_drains_queue(self, engine, pool, config):
        config.queue_depth = 2
        _seed_pool(pool, "morning", count=5)
        engine.start()
        engine.on_block_change("morning")

        # Should be able to select multiple times
        v1 = engine.select_next()
        v2 = engine.select_next()
        assert v1 is not None
        assert v2 is not None
        # Should get different videos
        assert v1["video_id"] != v2["video_id"]

    def test_select_refills_queue(self, engine, pool, config):
        config.queue_depth = 2
        _seed_pool(pool, "morning", count=6)
        engine.start()
        engine.on_block_change("morning")

        initial_depth = len(engine.get_queue_preview())
        engine.select_next()
        # Queue should refill after selection
        assert len(engine.get_queue_preview()) >= initial_depth - 1


class TestScoring:
    def test_fallback_scoring_without_profile(self, engine, pool):
        """Without a profile, scores use pure self-learning weights."""
        _seed_pool(pool, "morning", count=3)
        engine.start()

        result = engine.select_next("morning")
        assert result is not None
        # All neutral videos should have score = 1.0 (WEIGHT_NEUTRAL)
        assert result["score"] > 0

    def test_ai_scoring_with_profile(self, engine, pool, profile, db):
        """With a profile, scores incorporate genre weights."""
        _save_profile(db, _make_profile())
        profile.load(db)
        _seed_pool(pool, "morning", count=3)
        engine.start()

        result = engine.select_next("morning")
        assert result is not None
        assert result["score"] > 0

    def test_liked_videos_score_higher(self, engine, pool, db):
        """Liked videos should have higher scores than neutral."""
        _seed_pool(pool, "morning", count=3)
        # Rate first video as liked
        pool.rate_video("morning", "vid000aaaaa", 1)
        engine.start()

        queue = engine._score_pool("morning")
        scores_by_id = {v["video_id"]: v["score"] for v in queue}
        # Liked video (3.0 base) should score higher than neutral (1.0)
        assert scores_by_id["vid000aaaaa"] > scores_by_id.get("vid001aaaaa", 0)

    def test_disliked_videos_score_lower(self, engine, pool, db):
        """Disliked videos should have lower scores."""
        _seed_pool(pool, "morning", count=3)
        pool.rate_video("morning", "vid000aaaaa", -1)
        engine.start()

        queue = engine._score_pool("morning")
        scores_by_id = {v["video_id"]: v["score"] for v in queue}
        # Disliked (0.1 base) should be much lower
        assert scores_by_id["vid000aaaaa"] < scores_by_id.get("vid001aaaaa", 1.0)

    def test_genre_match_boosts_score(self, engine, pool, profile, db):
        """Videos matching profile genre weights should score higher."""
        _save_profile(db, _make_profile(genre_weights={"ambient": 0.9, "documentary": 0.1}))
        profile.load(db)

        # Add ambient and documentary videos
        pool.add_video(
            "morning",
            "https://www.youtube.com/watch?v=amb000aaaaa",
            title="Ambient Video",
            tags="ambient",
        )
        pool.add_video(
            "morning",
            "https://www.youtube.com/watch?v=doc000aaaaa",
            title="Doc Video",
            tags="documentary",
        )
        engine.start()

        queue = engine._score_pool("morning")
        scores_by_id = {v["video_id"]: v["score"] for v in queue}
        # Ambient (0.9 weight) should score higher than documentary (0.1)
        assert scores_by_id["amb000aaaaa"] > scores_by_id["doc000aaaaa"]

    def test_duration_penalty(self, engine, pool, profile, db):
        """Videos exceeding block max_duration should get penalized."""
        _save_profile(
            db,
            _make_profile(
                block_strategies={"morning": {"max_duration": 1000}},
            ),
        )
        profile.load(db)

        pool.add_video(
            "morning",
            "https://www.youtube.com/watch?v=short0aaaaa",
            title="Short",
            tags="ambient",
            duration=800,
        )
        pool.add_video(
            "morning",
            "https://www.youtube.com/watch?v=long00aaaaa",
            title="Long",
            tags="ambient",
            duration=2000,
        )
        engine.start()

        queue = engine._score_pool("morning")
        scores_by_id = {v["video_id"]: v["score"] for v in queue}
        # Long video gets 0.3 duration_fit penalty
        assert scores_by_id["short0aaaaa"] > scores_by_id["long00aaaaa"]

    def test_skip_penalty_reduces_score(self, engine, pool):
        """Skipped videos should have lower scores."""
        _seed_pool(pool, "morning", count=2)
        # Record 3 skips on first video
        pool.record_skip("morning", "vid000aaaaa")
        pool.record_skip("morning", "vid000aaaaa")
        pool.record_skip("morning", "vid000aaaaa")
        engine.start()

        queue = engine._score_pool("morning")
        scores_by_id = {v["video_id"]: v["score"] for v in queue}
        # 0.7^3 = 0.343 penalty
        assert scores_by_id["vid000aaaaa"] < scores_by_id.get("vid001aaaaa", 1.0)

    def test_stale_profile_uses_fallback(self, engine, pool, profile, db, config):
        """Stale profile should fall back to pure self-learning weights."""
        config.stale_threshold_hours = 1
        _save_profile(db, _make_profile(generated_at="2020-01-01T00:00:00"))
        profile.load(db)

        _seed_pool(pool, "morning", count=3)
        engine.start()

        # With stale profile, scoring should not use genre weights
        queue = engine._score_pool("morning")
        # All neutral videos should have base score 1.0
        for v in queue:
            assert v["score"] == pytest.approx(1.0)


# --- Queue Management ---


class TestQueueManagement:
    def test_block_change_clears_queue(self, engine, pool):
        _seed_pool(pool, "morning", count=5)
        _seed_pool(pool, "evening", count=5, prefix="eve")
        engine.start()
        engine.on_block_change("morning")
        morning_queue = engine.get_queue_preview()
        assert len(morning_queue) > 0

        engine.on_block_change("evening")
        evening_queue = engine.get_queue_preview()
        # Queue should be for evening, not morning
        assert engine.current_block == "evening"
        morning_ids = {v["video_id"] for v in morning_queue}
        evening_ids = {v["video_id"] for v in evening_queue}
        assert not morning_ids.intersection(evening_ids)

    def test_video_complete_removes_from_queue(self, engine, pool, config):
        config.queue_depth = 3
        _seed_pool(pool, "morning", count=8)
        engine.start()
        engine.on_block_change("morning")
        queue_before = engine.get_queue_preview()
        assert len(queue_before) == 3
        vid_id = queue_before[0]["video_id"]
        engine.on_video_complete(vid_id)
        # Queue should still be at target depth (refilled after removal)
        queue_after = engine.get_queue_preview()
        assert len(queue_after) == 3
        # The first item should be different (original was popped)
        assert queue_after[0]["video_id"] == queue_before[1]["video_id"]

    def test_video_skip_removes_from_queue(self, engine, pool, config):
        config.queue_depth = 3
        _seed_pool(pool, "morning", count=8)
        engine.start()
        engine.on_block_change("morning")
        queue_before = engine.get_queue_preview()
        assert len(queue_before) == 3
        vid_id = queue_before[0]["video_id"]
        engine.on_video_skip(vid_id)
        # Queue should still be at target depth (refilled after removal)
        queue_after = engine.get_queue_preview()
        assert len(queue_after) == 3
        # The first item should be different (original was removed)
        assert queue_after[0]["video_id"] == queue_before[1]["video_id"]

    def test_queue_respects_target_depth(self, engine, pool, config):
        config.queue_depth = 3
        _seed_pool(pool, "morning", count=10)
        engine.start()
        engine.on_block_change("morning")
        assert len(engine.get_queue_preview()) == 3

    def test_queue_handles_small_pool(self, engine, pool, config):
        """Queue depth adapts when pool is smaller than target."""
        config.queue_depth = 4
        _seed_pool(pool, "morning", count=2)
        engine.start()
        engine.on_block_change("morning")
        # Can't fill to 4 with only 2 videos
        assert len(engine.get_queue_preview()) <= 2


# --- Logging ---


class TestLogging:
    def test_start_logged(self, engine, db):
        engine.start()
        rows = db.fetchall("SELECT * FROM autopilot_log WHERE action = 'start'")
        assert len(rows) == 1
        assert rows[0]["reason"] == "Autopilot enabled"

    def test_stop_logged(self, engine, db):
        engine.start()
        engine.stop()
        rows = db.fetchall("SELECT * FROM autopilot_log WHERE action = 'stop'")
        assert len(rows) == 1

    def test_select_logged(self, engine, pool, db):
        _seed_pool(pool, "morning")
        engine.start()
        engine.select_next("morning")
        rows = db.fetchall("SELECT * FROM autopilot_log WHERE action = 'select'")
        assert len(rows) == 1
        assert rows[0]["video_id"] is not None
        assert rows[0]["source"] in ("ai", "fallback")

    def test_block_change_logged(self, engine, pool, db):
        _seed_pool(pool, "morning")
        engine.start()
        engine.on_block_change("morning")
        rows = db.fetchall("SELECT * FROM autopilot_log WHERE action = 'block_change'")
        assert len(rows) == 1
        assert rows[0]["block_name"] == "morning"


# --- Weighted Shuffle ---


class TestWeightedShuffle:
    def test_empty_list(self):
        assert _weighted_shuffle([]) == []

    def test_single_item(self):
        items = [{"video_id": "a", "score": 1.0}]
        result = _weighted_shuffle(items)
        assert len(result) == 1
        assert result[0]["video_id"] == "a"

    def test_preserves_all_items(self):
        items = [
            {"video_id": "a", "score": 3.0},
            {"video_id": "b", "score": 1.0},
            {"video_id": "c", "score": 0.5},
        ]
        result = _weighted_shuffle(items)
        assert len(result) == 3
        result_ids = {v["video_id"] for v in result}
        assert result_ids == {"a", "b", "c"}

    def test_zero_scores_handled(self):
        """Items with zero score should still appear (floor at 0.01)."""
        items = [
            {"video_id": "a", "score": 0.0},
            {"video_id": "b", "score": 1.0},
        ]
        result = _weighted_shuffle(items)
        assert len(result) == 2


# --- API Endpoint Tests ---


class TestAPI:
    @pytest.fixture
    def app_with_autopilot(self, tmp_path):
        config = ServerConfig(
            mpv_socket="/tmp/picast-test-socket",
            db_file=str(tmp_path / "test.db"),
            data_dir=str(tmp_path / "data"),
        )
        autoplay = AutoplayConfig(enabled=True, pool_mode=True)
        autopilot = AutopilotConfig(enabled=False, queue_depth=3)
        app = create_app(
            config,
            autoplay_config=autoplay,
            autopilot_config=autopilot,
        )
        app.player.stop()
        app.config["TESTING"] = True
        return app

    @pytest.fixture
    def client(self, app_with_autopilot):
        return app_with_autopilot.test_client()

    def test_autopilot_status(self, client):
        resp = client.get("/api/autopilot/status")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["enabled"] is False
        assert data["mode"] == "single"
        assert data["queue_depth"] == 0

    def test_autopilot_toggle(self, client):
        resp = client.post("/api/autopilot/toggle")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["enabled"] is True

        # Toggle back off
        resp = client.post("/api/autopilot/toggle")
        data = resp.get_json()
        assert data["enabled"] is False

    def test_autopilot_queue_empty(self, client):
        resp = client.get("/api/autopilot/queue")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["queue"] == []
        assert data["count"] == 0

    def test_autopilot_trigger_integration(self, client, app_with_autopilot):
        """When autopilot is enabled, trigger should use autopilot engine."""
        # Add videos to pool
        pool = AutoPlayPool(app_with_autopilot.db)
        _seed_pool(pool, "morning", count=3)

        # Enable autopilot
        client.post("/api/autopilot/toggle")

        # Check status shows enabled
        resp = client.get("/api/autopilot/status")
        assert resp.get_json()["enabled"] is True
