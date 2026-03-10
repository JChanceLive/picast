"""Tests for TasteProfile loader (v2 — energy profiles)."""

import json
import os
import tempfile
from datetime import datetime, timedelta, timezone

import pytest

from picast.server.database import Database
from picast.server.taste_profile import TasteProfile, TasteProfileError


def _make_profile(
    version=2,
    generated_at="2026-03-10T06:00:00",
    genre_weights=None,
    energy_profiles=None,
    discovery_queries=None,
    creator_affinity=None,
    avoid_patterns=None,
):
    """Build a valid v2 taste profile dict."""
    profile = {
        "version": version,
        "generated_at": generated_at,
        "global_preferences": {
            "preferred_duration_range": [600, 3600],
            "genre_weights": genre_weights or {"ambient": 0.8, "documentary": 0.6},
        },
        "energy_profiles": energy_profiles
        or {
            "chill": {
                "genres": ["ambient", "nature"],
                "max_duration": 7200,
                "tempo": "slow",
                "description": "Relaxing background content",
            },
            "focus": {
                "genres": ["lo-fi", "jazz"],
                "max_duration": 5400,
                "tempo": "steady",
                "description": "Non-distracting work content",
            },
            "vibes": {
                "genres": ["documentary", "comedy"],
                "max_duration": 3600,
                "tempo": "any",
                "description": "Engaging variety content",
            },
        },
    }
    if discovery_queries is not None:
        profile["discovery_queries"] = discovery_queries
    elif discovery_queries is None:
        profile["discovery_queries"] = [
            "relaxing ambient music",
            "nature documentary 4k",
            "lo-fi focus beats",
        ]
    if creator_affinity is not None:
        profile["creator_affinity"] = creator_affinity
    if avoid_patterns is not None:
        profile["avoid_patterns"] = avoid_patterns
    return profile


@pytest.fixture
def db():
    """Create a temporary database with schema."""
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
            (json.dumps(profile), "2026-03-10T06:00:00", 2),
        )
        db.commit()

        result = tp.load(db)
        assert result is not None
        assert tp.is_loaded is True
        assert tp.version == 2

    def test_load_invalid_json(self, db, tp):
        db.execute(
            "INSERT INTO autopilot_profile (id, profile_json, generated_at, version) "
            "VALUES (1, ?, ?, ?)",
            ("not-valid-json{{{", "2026-03-10T06:00:00", 2),
        )
        db.commit()

        result = tp.load(db)
        assert result is None
        assert tp.is_loaded is False

    def test_load_missing_keys(self, db, tp):
        incomplete = {"version": 2, "generated_at": "2026-03-10T06:00:00"}
        db.execute(
            "INSERT INTO autopilot_profile (id, profile_json, generated_at, version) "
            "VALUES (1, ?, ?, ?)",
            (json.dumps(incomplete), "2026-03-10T06:00:00", 2),
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
            (json.dumps(profile), now.isoformat(), 2),
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
            (json.dumps(profile), old.isoformat(), 2),
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
            (json.dumps(profile), recent.isoformat(), 2),
        )
        db.commit()
        tp.load(db)

        assert tp.is_stale(threshold_hours=2) is True
        assert tp.is_stale(threshold_hours=6) is False


class TestTasteProfileQueries:
    def test_energy_profile_exists(self, db, tp):
        profile = _make_profile()
        tp.save(db, json.dumps(profile), "2026-03-10T06:00:00")

        energy = tp.get_energy_profile("chill")
        assert energy["tempo"] == "slow"
        assert "ambient" in energy["genres"]

    def test_energy_profile_missing(self, db, tp):
        profile = _make_profile()
        tp.save(db, json.dumps(profile), "2026-03-10T06:00:00")

        energy = tp.get_energy_profile("nonexistent")
        assert energy == {}

    def test_energy_profile_not_loaded(self, tp):
        assert tp.get_energy_profile("chill") == {}

    def test_genre_weights(self, db, tp):
        profile = _make_profile(
            genre_weights={"lofi": 0.9, "jazz": 0.5}
        )
        tp.save(db, json.dumps(profile), "2026-03-10T06:00:00")

        weights = tp.get_genre_weights()
        assert weights == {"lofi": 0.9, "jazz": 0.5}

    def test_genre_weights_not_loaded(self, tp):
        assert tp.get_genre_weights() == {}

    def test_discovery_queries_global(self, db, tp):
        profile = _make_profile()
        tp.save(db, json.dumps(profile), "2026-03-10T06:00:00")

        queries = tp.get_discovery_queries()
        assert len(queries) == 3
        assert "relaxing ambient music" in queries

    def test_discovery_queries_not_loaded(self, tp):
        assert tp.get_discovery_queries() == []

    def test_creator_affinity(self, db, tp):
        profile = _make_profile(
            creator_affinity={"Chillhop Music": 1.5, "Cafe BGM": 0.8}
        )
        tp.save(db, json.dumps(profile), "2026-03-10T06:00:00")

        affinity = tp.get_creator_affinity()
        assert affinity == {"Chillhop Music": 1.5, "Cafe BGM": 0.8}

    def test_creator_affinity_missing(self, db, tp):
        profile = _make_profile()
        tp.save(db, json.dumps(profile), "2026-03-10T06:00:00")

        assert tp.get_creator_affinity() == {}

    def test_creator_affinity_not_loaded(self, tp):
        assert tp.get_creator_affinity() == {}

    def test_avoid_patterns(self, db, tp):
        profile = _make_profile(
            avoid_patterns=["asmr", "mukbang"]
        )
        tp.save(db, json.dumps(profile), "2026-03-10T06:00:00")

        patterns = tp.get_avoid_patterns()
        assert patterns == ["asmr", "mukbang"]

    def test_avoid_patterns_missing(self, db, tp):
        profile = _make_profile()
        tp.save(db, json.dumps(profile), "2026-03-10T06:00:00")

        assert tp.get_avoid_patterns() == []

    def test_avoid_patterns_not_loaded(self, tp):
        assert tp.get_avoid_patterns() == []


class TestTasteProfileSave:
    def test_save_valid(self, db, tp):
        profile = _make_profile()
        tp.save(db, json.dumps(profile), "2026-03-10T06:00:00")

        assert tp.is_loaded is True
        assert tp.version == 2

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
            tp.save(db, "not{json", "2026-03-10T06:00:00")

    def test_save_missing_keys(self, db, tp):
        incomplete = json.dumps({"version": 2})
        with pytest.raises(TasteProfileError, match="missing required keys"):
            tp.save(db, incomplete, "2026-03-10T06:00:00")


class TestTasteProfileToDict:
    def test_to_dict_not_loaded(self, tp):
        result = tp.to_dict()
        assert result["loaded"] is False
        assert result["version"] == 0
        assert result["generated_at"] is None
        assert result["stale"] is True
        assert result["energy_profiles"] == []

    def test_to_dict_loaded(self, db, tp):
        profile = _make_profile()
        tp.save(db, json.dumps(profile), "2026-03-10T06:00:00")

        result = tp.to_dict()
        assert result["loaded"] is True
        assert result["version"] == 2
        assert set(result["energy_profiles"]) == {"chill", "focus", "vibes"}
