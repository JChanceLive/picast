"""Tests for AutopilotEngine — AI-enhanced video selection and queue management."""

import json
from unittest.mock import MagicMock, patch

import pytest

from picast.config import AutopilotConfig, AutoplayConfig, FleetDeviceConfig, ServerConfig
from picast.server.app import create_app
from picast.server.autoplay_pool import AutoPlayPool
from picast.server.autopilot_engine import AutopilotEngine, _weighted_shuffle
from picast.server.autopilot_fleet import FleetManager
from picast.server.database import Database
from picast.server.taste_profile import TasteProfile
from picast.server.youtube_discovery import DiscoveryAgent, DiscoveryResult


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

    # --- Mode Switch Tests ---

    def test_mode_switch_to_fleet(self, client):
        resp = client.post(
            "/api/autopilot/mode",
            json={"mode": "fleet"},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["mode"] == "fleet"

        # Verify status reflects new mode
        resp = client.get("/api/autopilot/status")
        assert resp.get_json()["mode"] == "fleet"

    def test_mode_switch_to_single(self, client):
        # First switch to fleet
        client.post("/api/autopilot/mode", json={"mode": "fleet"})
        # Then back to single
        resp = client.post("/api/autopilot/mode", json={"mode": "single"})
        assert resp.status_code == 200
        assert resp.get_json()["mode"] == "single"

    def test_mode_switch_invalid(self, client):
        resp = client.post(
            "/api/autopilot/mode",
            json={"mode": "invalid"},
        )
        assert resp.status_code == 400
        assert "error" in resp.get_json()

    def test_mode_switch_missing(self, client):
        resp = client.post("/api/autopilot/mode", json={})
        assert resp.status_code == 400

    # --- Profile Endpoint Tests ---

    def test_profile_get_no_profile(self, client):
        resp = client.get("/api/autopilot/profile")
        assert resp.status_code == 404
        assert "no profile" in resp.get_json()["error"]

    def test_profile_upload_and_get(self, client):
        profile = _make_profile()
        resp = client.post(
            "/api/autopilot/profile",
            json={
                "profile": profile,
                "generated_at": "2026-03-09T06:00:00",
            },
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["profile"]["loaded"] is True

        # Now GET should return the profile
        resp = client.get("/api/autopilot/profile")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["version"] == 1
        assert "global_preferences" in data
        assert "block_strategies" in data

    def test_profile_upload_as_json_string(self, client):
        profile = _make_profile()
        resp = client.post(
            "/api/autopilot/profile",
            json={
                "profile": json.dumps(profile),
                "generated_at": "2026-03-09T06:00:00",
            },
        )
        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True

    def test_profile_upload_missing_profile(self, client):
        resp = client.post(
            "/api/autopilot/profile",
            json={"generated_at": "2026-03-09T06:00:00"},
        )
        assert resp.status_code == 400
        assert "profile required" in resp.get_json()["error"]

    def test_profile_upload_missing_generated_at(self, client):
        resp = client.post(
            "/api/autopilot/profile",
            json={"profile": _make_profile()},
        )
        assert resp.status_code == 400
        assert "generated_at required" in resp.get_json()["error"]

    def test_profile_upload_invalid_json(self, client):
        resp = client.post(
            "/api/autopilot/profile",
            json={
                "profile": "not valid json {{{",
                "generated_at": "2026-03-09T06:00:00",
            },
        )
        assert resp.status_code == 400

    def test_profile_upload_missing_keys(self, client):
        resp = client.post(
            "/api/autopilot/profile",
            json={
                "profile": {"incomplete": True},
                "generated_at": "2026-03-09T06:00:00",
            },
        )
        assert resp.status_code == 400

    # --- Sources Endpoint Tests ---

    def test_sources_set_pool_only(self, client):
        resp = client.post(
            "/api/autopilot/sources",
            json={"pool_only": True},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["pool_only"] is True

        # Verify it sticks in status
        resp = client.get("/api/autopilot/status")
        assert resp.get_json()["pool_only"] is True

    def test_sources_set_discovery_ratio(self, client):
        resp = client.post(
            "/api/autopilot/sources",
            json={"discovery_ratio": 0.5},
        )
        assert resp.status_code == 200
        assert resp.get_json()["discovery_ratio"] == 0.5

    def test_sources_invalid_discovery_ratio(self, client):
        resp = client.post(
            "/api/autopilot/sources",
            json={"discovery_ratio": 1.5},
        )
        assert resp.status_code == 400

    def test_sources_set_both(self, client):
        resp = client.post(
            "/api/autopilot/sources",
            json={"pool_only": False, "discovery_ratio": 0.2},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["pool_only"] is False
        assert data["discovery_ratio"] == 0.2

    # --- Feedback Endpoint Tests ---

    def test_feedback_more(self, client, app_with_autopilot):
        # Seed a pool and set current autoplay
        pool = AutoPlayPool(app_with_autopilot.db)
        _seed_pool(pool, "morning", count=1)

        resp = client.post(
            "/api/autopilot/feedback",
            json={"signal": "more", "video_id": "vid000aaaaa", "block_name": "morning"},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["signal"] == "more"
        assert data["video_id"] == "vid000aaaaa"

    def test_feedback_less(self, client):
        resp = client.post(
            "/api/autopilot/feedback",
            json={"signal": "less", "video_id": "vid000aaaaa", "block_name": "morning"},
        )
        assert resp.status_code == 200
        assert resp.get_json()["signal"] == "less"

    def test_feedback_invalid_signal(self, client):
        resp = client.post(
            "/api/autopilot/feedback",
            json={"signal": "invalid", "video_id": "vid000aaaaa"},
        )
        assert resp.status_code == 400

    def test_feedback_no_video(self, client):
        """Should fail when no video_id and no current video playing."""
        resp = client.post(
            "/api/autopilot/feedback",
            json={"signal": "more"},
        )
        assert resp.status_code == 400
        assert "no video_id" in resp.get_json()["error"]

    # --- Status Stale Detection Tests ---

    def test_status_shows_stale_no_profile(self, client):
        resp = client.get("/api/autopilot/status")
        data = resp.get_json()
        assert data["stale"] is True
        assert data["stale_reason"] == "no profile loaded"

    def test_status_shows_stale_threshold(self, client):
        # Upload a profile with old generated_at
        profile = _make_profile()
        client.post(
            "/api/autopilot/profile",
            json={
                "profile": profile,
                "generated_at": "2020-01-01T00:00:00",
            },
        )
        resp = client.get("/api/autopilot/status")
        data = resp.get_json()
        assert data["stale"] is True
        assert "older than" in data["stale_reason"]

    def test_status_shows_not_stale(self, client):
        # Upload a fresh profile
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc).isoformat()
        profile = _make_profile(generated_at=now)
        client.post(
            "/api/autopilot/profile",
            json={"profile": profile, "generated_at": now},
        )
        resp = client.get("/api/autopilot/status")
        data = resp.get_json()
        assert data["stale"] is False
        assert data["stale_reason"] is None
        assert "stale_threshold_hours" in data


# --- Engine Method Tests (new in S1.3) ---


class TestEngineNewMethods:
    @pytest.fixture
    def db(self, tmp_path):
        return Database(str(tmp_path / "test.db"))

    @pytest.fixture
    def pool(self, db):
        return AutoPlayPool(db, avoid_recent=2)

    @pytest.fixture
    def profile(self):
        return TasteProfile()

    @pytest.fixture
    def config(self):
        return AutopilotConfig(enabled=True, queue_depth=4)

    @pytest.fixture
    def engine(self, pool, profile, config, db):
        return AutopilotEngine(pool=pool, profile=profile, config=config, db=db)

    def test_set_mode_single(self, engine):
        result = engine.set_mode("single")
        assert result == "single"
        assert engine.get_status()["mode"] == "single"

    def test_set_mode_fleet(self, engine):
        result = engine.set_mode("fleet")
        assert result == "fleet"
        assert engine.get_status()["mode"] == "fleet"

    def test_set_mode_invalid(self, engine):
        with pytest.raises(ValueError):
            engine.set_mode("invalid")

    def test_reload_profile_no_profile(self, engine):
        result = engine.reload_profile()
        assert result is None

    def test_reload_profile_with_data(self, engine, db):
        profile_dict = _make_profile()
        _save_profile(db, profile_dict)
        result = engine.reload_profile()
        assert result is not None
        assert result["version"] == 1

    def test_reload_profile_refreshes_queue(self, engine, pool, db):
        """reload_profile should clear and refill queue when running."""
        _seed_pool(pool, "morning", count=5)
        engine.start()
        engine.on_block_change("morning")
        assert engine.get_status()["queue_depth"] > 0

        # Reload should refill
        profile_dict = _make_profile()
        _save_profile(db, profile_dict)
        engine.reload_profile()
        assert engine.get_status()["queue_depth"] > 0

    def test_record_feedback(self, engine, db):
        engine.record_feedback("vid000aaaaa", "more", "morning")
        # Verify log entry
        row = db.fetchone(
            "SELECT action, video_id, reason FROM autopilot_log "
            "WHERE action = 'feedback'"
        )
        assert row is not None
        assert row["video_id"] == "vid000aaaaa"
        assert row["reason"] == "more"

    def test_get_profile_data_none(self, engine):
        assert engine.get_profile_data() is None

    def test_get_profile_data_loaded(self, engine, db):
        profile_dict = _make_profile()
        _save_profile(db, profile_dict)
        engine._profile.load(db)
        data = engine.get_profile_data()
        assert data is not None
        assert data["version"] == 1

    def test_status_includes_stale_fields(self, engine):
        status = engine.get_status()
        assert "stale" in status
        assert "stale_reason" in status
        assert "stale_threshold_hours" in status


# --- Discovery Integration Tests ---


def _make_profile_with_discovery(block_name="morning"):
    """Build a profile with discovery queries."""
    return {
        "version": 1,
        "generated_at": "2026-03-10T06:00:00",
        "global_preferences": {"genre_weights": {"ambient": 0.9}},
        "block_strategies": {
            block_name: {"energy": "low", "max_duration": 1800},
        },
        "discovery_queries": {
            block_name: ["chill ambient music"],
        },
    }


class TestDiscoveryIntegration:
    """Tests for discovery wired into AutopilotEngine._fill_queue."""

    @pytest.fixture
    def mock_discovery(self):
        """Create a mock DiscoveryAgent that returns controlled results."""
        agent = MagicMock(spec=DiscoveryAgent)
        agent.discover_from_profile.return_value = [
            DiscoveryResult("disc_vid_001", "Discovery Track 1", 600,
                            "https://www.youtube.com/watch?v=disc_vid_001"),
            DiscoveryResult("disc_vid_002", "Discovery Track 2", 900,
                            "https://www.youtube.com/watch?v=disc_vid_002"),
        ]
        return agent

    @pytest.fixture
    def discovery_config(self):
        return AutopilotConfig(
            enabled=True, queue_depth=4,
            pool_only=False, discovery_ratio=0.5,
        )

    @pytest.fixture
    def discovery_engine(self, pool, profile, discovery_config, db, mock_discovery):
        return AutopilotEngine(
            pool=pool, profile=profile, config=discovery_config,
            db=db, discovery=mock_discovery,
        )

    def test_discovery_fills_slots_when_enabled(self, discovery_engine, db, pool, mock_discovery):
        _seed_pool(pool, "morning", count=5)
        _save_profile(db, _make_profile_with_discovery())
        discovery_engine._profile.load(db)
        discovery_engine.start()
        result = discovery_engine.select_next("morning")
        assert result is not None
        # Discovery was called
        mock_discovery.discover_from_profile.assert_called()
        # Queue should contain a mix
        queue = discovery_engine.get_queue_preview()
        sources = {v.get("source") for v in queue}
        video_ids = {v["video_id"] for v in queue}
        # At least one discovery item should be present (or was selected)
        has_discovery = "disc_vid_001" in video_ids or "disc_vid_002" in video_ids or (
            result["video_id"] in ("disc_vid_001", "disc_vid_002")
        )
        assert has_discovery or "discovery" in sources

    def test_pool_only_disables_discovery(self, pool, profile, db, mock_discovery):
        config = AutopilotConfig(
            enabled=True, queue_depth=4,
            pool_only=True, discovery_ratio=0.5,
        )
        engine = AutopilotEngine(
            pool=pool, profile=profile, config=config,
            db=db, discovery=mock_discovery,
        )
        _seed_pool(pool, "morning", count=5)
        _save_profile(db, _make_profile_with_discovery())
        engine._profile.load(db)
        engine.start()
        engine.select_next("morning")
        mock_discovery.discover_from_profile.assert_not_called()

    def test_zero_ratio_disables_discovery(self, pool, profile, db, mock_discovery):
        config = AutopilotConfig(
            enabled=True, queue_depth=4,
            pool_only=False, discovery_ratio=0.0,
        )
        engine = AutopilotEngine(
            pool=pool, profile=profile, config=config,
            db=db, discovery=mock_discovery,
        )
        _seed_pool(pool, "morning", count=5)
        _save_profile(db, _make_profile_with_discovery())
        engine._profile.load(db)
        engine.start()
        engine.select_next("morning")
        mock_discovery.discover_from_profile.assert_not_called()

    def test_no_discovery_agent_falls_back_to_pool(self, pool, profile, db):
        config = AutopilotConfig(
            enabled=True, queue_depth=4,
            pool_only=False, discovery_ratio=0.5,
        )
        engine = AutopilotEngine(
            pool=pool, profile=profile, config=config,
            db=db, discovery=None,
        )
        _seed_pool(pool, "morning", count=5)
        _save_profile(db, _make_profile_with_discovery())
        engine._profile.load(db)
        engine.start()
        result = engine.select_next("morning")
        assert result is not None  # Falls back to pool

    def test_discovery_failure_falls_back_to_pool(self, pool, profile, db):
        mock_disc = MagicMock(spec=DiscoveryAgent)
        mock_disc.discover_from_profile.side_effect = RuntimeError("network error")
        config = AutopilotConfig(
            enabled=True, queue_depth=4,
            pool_only=False, discovery_ratio=0.5,
        )
        engine = AutopilotEngine(
            pool=pool, profile=profile, config=config,
            db=db, discovery=mock_disc,
        )
        _seed_pool(pool, "morning", count=5)
        _save_profile(db, _make_profile_with_discovery())
        engine._profile.load(db)
        engine.start()
        result = engine.select_next("morning")
        assert result is not None  # Pool videos still work


# --- Queue Skip Endpoint Test ---


class TestQueueSkipEndpoint:

    @pytest.fixture
    def app(self, tmp_path):
        config = ServerConfig(
            mpv_socket="/tmp/picast-test-socket",
            db_file=str(tmp_path / "test.db"),
            data_dir=str(tmp_path / "data"),
        )
        autoplay = AutoplayConfig(enabled=True, pool_mode=True)
        app = create_app(config, autoplay_config=autoplay)
        app.player.stop()
        app.config["TESTING"] = True
        return app

    @pytest.fixture
    def client(self, app):
        return app.test_client()

    def test_skip_requires_video_id(self, client):
        resp = client.post("/api/autopilot/queue/skip", json={})
        assert resp.status_code == 400

    def test_skip_accepts_video_id(self, client):
        resp = client.post(
            "/api/autopilot/queue/skip",
            json={"video_id": "test1234567"},
        )
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["video_id"] == "test1234567"


# --- Fleet Integration with Engine ---


class TestEngineFleet:
    @pytest.fixture
    def fleet_config(self):
        return AutopilotConfig(
            enabled=True,
            mode="fleet",
            queue_depth=4,
            fleet_devices={
                "living-room": FleetDeviceConfig(
                    host="10.0.0.10", port=5050, room="living room", mood="chill",
                ),
            },
        )

    @pytest.fixture
    def fleet_engine(self, pool, profile, fleet_config, db):
        fleet = FleetManager(fleet_config)
        return AutopilotEngine(
            pool=pool, profile=profile, config=fleet_config,
            db=db, fleet=fleet,
        )

    def test_engine_has_fleet(self, fleet_engine):
        assert fleet_engine.fleet is not None

    def test_engine_no_fleet(self, engine):
        assert engine.fleet is None

    def test_status_includes_fleet_devices(self, fleet_engine):
        status = fleet_engine.get_status()
        assert "fleet_devices" in status
        assert status["fleet_devices"] == 1

    def test_status_no_fleet_key_without_fleet(self, engine):
        status = engine.get_status()
        assert "fleet_devices" not in status

    def test_get_fleet_status_with_fleet(self, fleet_engine):
        result = fleet_engine.get_fleet_status()
        assert result is not None
        assert len(result) == 1
        assert result[0]["device_id"] == "living-room"

    def test_get_fleet_status_without_fleet(self, engine):
        assert engine.get_fleet_status() is None

    def test_select_next_fleet_not_running(self, fleet_engine):
        """Fleet selection should return empty when engine not running."""
        results = fleet_engine.select_next_fleet()
        assert results == []

    def test_select_next_fleet_single_mode(self, fleet_engine):
        """Fleet selection should return empty in single mode."""
        fleet_engine._config.mode = "single"
        fleet_engine.start()
        results = fleet_engine.select_next_fleet()
        assert results == []

    @patch("picast.server.autopilot_fleet.urllib.request.urlopen")
    def test_select_next_fleet_logs_push(self, mock_urlopen, fleet_engine, pool, db):
        """Fleet push actions should be logged."""
        import json as _json
        # Make device online + idle
        mock_resp = MagicMock()
        mock_resp.read.return_value = _json.dumps({"idle": True}).encode()
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        _seed_pool(pool, "evening-transition", count=5)
        fleet_engine.start()
        fleet_engine.on_block_change("evening-transition")

        # Change response for push
        mock_resp_push = MagicMock()
        mock_resp_push.read.return_value = _json.dumps({"ok": True}).encode()
        mock_resp_push.__enter__ = MagicMock(return_value=mock_resp_push)
        mock_resp_push.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp_push

        results = fleet_engine.select_next_fleet()
        # Check fleet_push was logged
        rows = db.fetchall(
            "SELECT * FROM autopilot_log WHERE action = 'fleet_push'"
        )
        assert len(rows) >= 1
