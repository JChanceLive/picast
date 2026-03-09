"""Tests for TasteProfile loader."""

import json
import os
import tempfile
from datetime import datetime, timedelta, timezone

import pytest

from picast.server.database import Database
from picast.server.taste_profile import TasteProfile, TasteProfileError


def _make_profile(
    version=1,
    generated_at="2026-03-09T06:00:00",
    genre_weights=None,
    block_strategies=None,
    discovery_queries=None,
):
    """Build a valid taste profile dict."""
    return {
        "version": version,
        "generated_at": generated_at,
        "global_preferences": {
            "preferred_duration_range": [600, 3600],
            "genre_weights": genre_weights or {"ambient": 0.8, "documentary": 0.6},
        },
        "block_strategies": block_strategies
        or {
            "morning": {
                "energy": "low",
                "genres": ["ambient", "nature"],
                "max_duration": 1800,
                "discovery_ratio": 0.2,
            },
            "evening": {
                "energy": "medium",
                "genres": ["documentary", "comedy"],
                "max_duration": 7200,
                "discovery_ratio": 0.3,
            },
        },
        "discovery_queries": discovery_queries
        or {
            "morning": ["relaxing morning ambient", "nature documentary 4k"],
            "evening": ["best short documentaries"],
        },
    }


@pytest.fixture
def db():
    """Create a temporary database with v11 schema."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    database = Database(path)
    yield database
    database.close()
    os.unlink(path)


@pytest.fixture
def tp():
    return TasteProfile()


class TestTasteProfileLoad:
    def test_load_empty_db(self, db, tp):
        result = tp.load(db)
        assert result is None
        assert tp.is_loaded is False

    def test_load_valid_profile(self, db, tp):
        profile = _make_profile()
        db.execute(
            "INSERT INTO autopilot_profile (id, profile_json, generated_at, version) "
            "VALUES (1, ?, ?, ?)",
            (json.dumps(profile), "2026-03-09T06:00:00", 1),
        )
        db.commit()

        result = tp.load(db)
        assert result is not None
        assert tp.is_loaded is True
        assert tp.version == 1

    def test_load_invalid_json(self, db, tp):
        db.execute(
            "INSERT INTO autopilot_profile (id, profile_json, generated_at, version) "
            "VALUES (1, ?, ?, ?)",
            ("not-valid-json{{{", "2026-03-09T06:00:00", 1),
        )
        db.commit()

        result = tp.load(db)
        assert result is None
        assert tp.is_loaded is False

    def test_load_missing_keys(self, db, tp):
        incomplete = {"version": 1, "generated_at": "2026-03-09T06:00:00"}
        db.execute(
            "INSERT INTO autopilot_profile (id, profile_json, generated_at, version) "
            "VALUES (1, ?, ?, ?)",
            (json.dumps(incomplete), "2026-03-09T06:00:00", 1),
        )
        db.commit()

        result = tp.load(db)
        assert result is None


class TestTasteProfileStale:
    def test_stale_when_not_loaded(self, tp):
        assert tp.is_stale() is True

    def test_not_stale_recent(self, db, tp):
        now = datetime.now(timezone.utc)
        profile = _make_profile(generated_at=now.isoformat())
        db.execute(
            "INSERT INTO autopilot_profile (id, profile_json, generated_at, version) "
            "VALUES (1, ?, ?, ?)",
            (json.dumps(profile), now.isoformat(), 1),
        )
        db.commit()
        tp.load(db)

        assert tp.is_stale(threshold_hours=48) is False

    def test_stale_old_profile(self, db, tp):
        old = datetime.now(timezone.utc) - timedelta(hours=72)
        profile = _make_profile(generated_at=old.isoformat())
        db.execute(
            "INSERT INTO autopilot_profile (id, profile_json, generated_at, version) "
            "VALUES (1, ?, ?, ?)",
            (json.dumps(profile), old.isoformat(), 1),
        )
        db.commit()
        tp.load(db)

        assert tp.is_stale(threshold_hours=48) is True

    def test_custom_threshold(self, db, tp):
        recent = datetime.now(timezone.utc) - timedelta(hours=3)
        profile = _make_profile(generated_at=recent.isoformat())
        db.execute(
            "INSERT INTO autopilot_profile (id, profile_json, generated_at, version) "
            "VALUES (1, ?, ?, ?)",
            (json.dumps(profile), recent.isoformat(), 1),
        )
        db.commit()
        tp.load(db)

        assert tp.is_stale(threshold_hours=2) is True
        assert tp.is_stale(threshold_hours=6) is False


class TestTasteProfileQueries:
    def test_block_strategy_exists(self, db, tp):
        profile = _make_profile()
        tp.save(db, json.dumps(profile), "2026-03-09T06:00:00")

        strategy = tp.get_block_strategy("morning")
        assert strategy["energy"] == "low"
        assert "ambient" in strategy["genres"]

    def test_block_strategy_missing(self, db, tp):
        profile = _make_profile()
        tp.save(db, json.dumps(profile), "2026-03-09T06:00:00")

        strategy = tp.get_block_strategy("nonexistent")
        assert strategy == {}

    def test_block_strategy_not_loaded(self, tp):
        assert tp.get_block_strategy("morning") == {}

    def test_genre_weights(self, db, tp):
        profile = _make_profile(
            genre_weights={"lofi": 0.9, "jazz": 0.5}
        )
        tp.save(db, json.dumps(profile), "2026-03-09T06:00:00")

        weights = tp.get_genre_weights()
        assert weights == {"lofi": 0.9, "jazz": 0.5}

    def test_genre_weights_not_loaded(self, tp):
        assert tp.get_genre_weights() == {}

    def test_discovery_queries(self, db, tp):
        profile = _make_profile()
        tp.save(db, json.dumps(profile), "2026-03-09T06:00:00")

        queries = tp.get_discovery_queries("morning")
        assert len(queries) == 2
        assert "relaxing morning ambient" in queries

    def test_discovery_queries_missing_block(self, db, tp):
        profile = _make_profile()
        tp.save(db, json.dumps(profile), "2026-03-09T06:00:00")

        queries = tp.get_discovery_queries("nonexistent")
        assert queries == []

    def test_discovery_queries_not_loaded(self, tp):
        assert tp.get_discovery_queries("morning") == []


class TestTasteProfileSave:
    def test_save_valid(self, db, tp):
        profile = _make_profile()
        tp.save(db, json.dumps(profile), "2026-03-09T06:00:00")

        assert tp.is_loaded is True
        assert tp.version == 1

    def test_save_upsert(self, db, tp):
        """Second save replaces the first."""
        profile_v1 = _make_profile(version=1)
        tp.save(db, json.dumps(profile_v1), "2026-03-09T06:00:00")
        assert tp.version == 1

        profile_v2 = _make_profile(version=2)
        tp.save(db, json.dumps(profile_v2), "2026-03-10T06:00:00")
        assert tp.version == 2

        row = db.fetchone("SELECT COUNT(*) as cnt FROM autopilot_profile")
        assert row["cnt"] == 1

    def test_save_invalid_json(self, db, tp):
        with pytest.raises(TasteProfileError, match="Invalid profile JSON"):
            tp.save(db, "not{json", "2026-03-09T06:00:00")

    def test_save_missing_keys(self, db, tp):
        incomplete = json.dumps({"version": 1})
        with pytest.raises(TasteProfileError, match="missing required keys"):
            tp.save(db, incomplete, "2026-03-09T06:00:00")


class TestTasteProfileToDict:
    def test_to_dict_not_loaded(self, tp):
        result = tp.to_dict()
        assert result["loaded"] is False
        assert result["version"] == 0
        assert result["generated_at"] is None
        assert result["stale"] is True
        assert result["block_count"] == 0

    def test_to_dict_loaded(self, db, tp):
        profile = _make_profile()
        tp.save(db, json.dumps(profile), "2026-03-09T06:00:00")

        result = tp.to_dict()
        assert result["loaded"] is True
        assert result["version"] == 1
        assert result["block_count"] == 2
