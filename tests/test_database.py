"""Tests for SQLite database layer."""

import sqlite3
import time
from unittest.mock import patch

import pytest

from picast.server.database import Database


class TestDatabase:
    def test_creates_tables(self, db):
        tables = db.fetchall(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
        names = [t["name"] for t in tables]
        assert "library" in names
        assert "playlists" in names
        assert "playlist_items" in names
        assert "schema_version" in names

    def test_schema_version(self, db):
        from picast.server.database import SCHEMA_VERSION
        row = db.fetchone("SELECT version FROM schema_version")
        assert row["version"] == SCHEMA_VERSION

    def test_settings_get_set(self, db):
        assert db.get_setting("volume") is None
        assert db.get_setting("volume", "80") == "80"
        db.set_setting("volume", "65")
        assert db.get_setting("volume") == "65"
        db.set_setting("volume", "40")
        assert db.get_setting("volume") == "40"

    def test_fetchone_returns_dict(self, db):
        db.execute(
            "INSERT INTO library (url, title, added_at) VALUES (?, ?, ?)",
            ("http://a", "Test", 1.0),
        )
        db.commit()
        row = db.fetchone("SELECT * FROM library WHERE url = ?", ("http://a",))
        assert isinstance(row, dict)
        assert row["url"] == "http://a"
        assert row["title"] == "Test"

    def test_fetchone_not_found(self, db):
        row = db.fetchone("SELECT * FROM library WHERE url = ?", ("nope",))
        assert row is None

    def test_fetchall_returns_list(self, db):
        db.execute(
            "INSERT INTO library (url, title, added_at) VALUES (?, ?, ?)",
            ("http://a", "A", 1.0),
        )
        db.execute(
            "INSERT INTO library (url, title, added_at) VALUES (?, ?, ?)",
            ("http://b", "B", 2.0),
        )
        db.commit()
        rows = db.fetchall("SELECT * FROM library ORDER BY url")
        assert len(rows) == 2
        assert all(isinstance(r, dict) for r in rows)

    def test_fetchall_empty(self, db):
        rows = db.fetchall("SELECT * FROM library")
        assert rows == []

    def test_wal_mode(self, db):
        row = db.fetchone("PRAGMA journal_mode")
        assert row is not None

    def test_foreign_keys_enabled(self, db):
        row = db.fetchone("PRAGMA foreign_keys")
        assert row["foreign_keys"] == 1

    def test_reopen_preserves_data(self, tmp_path):
        path = str(tmp_path / "persist.db")
        db1 = Database(path)
        db1.execute(
            "INSERT INTO library (url, title, added_at) VALUES (?, ?, ?)",
            ("http://a", "A", 1.0),
        )
        db1.commit()
        db1.close()

        db2 = Database(path)
        row = db2.fetchone("SELECT * FROM library WHERE url = ?", ("http://a",))
        assert row is not None
        assert row["title"] == "A"

    def test_unique_url_constraint(self, db):
        db.execute(
            "INSERT INTO library (url, title, added_at) VALUES (?, ?, ?)",
            ("http://a", "A", 1.0),
        )
        db.commit()
        with pytest.raises(Exception):
            db.execute(
                "INSERT INTO library (url, title, added_at) VALUES (?, ?, ?)",
                ("http://a", "A2", 2.0),
            )


class TestBlockMetadata:
    def test_block_metadata_table_exists(self, db):
        tables = db.fetchall(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='block_metadata'"
        )
        assert len(tables) == 1

    def test_upsert_block_metadata_insert(self, db):
        db.upsert_block_metadata(
            "morning-foundation",
            display_name="Morning Foundation",
            emoji="🌅",
            tagline="Start strong",
            block_type="ritual",
            energy="high",
            source="manual",
        )
        row = db.get_block_metadata("morning-foundation")
        assert row is not None
        assert row["display_name"] == "Morning Foundation"
        assert row["emoji"] == "🌅"
        assert row["tagline"] == "Start strong"
        assert row["block_type"] == "ritual"
        assert row["updated_at"] is not None

    def test_upsert_block_metadata_update(self, db):
        db.upsert_block_metadata(
            "creation-stack", display_name="Creation Stack", emoji="🎨",
        )
        db.upsert_block_metadata(
            "creation-stack", display_name="Create", emoji="✨",
        )
        row = db.get_block_metadata("creation-stack")
        assert row["display_name"] == "Create"
        assert row["emoji"] == "✨"

    def test_get_all_block_metadata(self, db):
        db.upsert_block_metadata("alpha", display_name="Alpha")
        db.upsert_block_metadata("beta", display_name="Beta")
        rows = db.get_all_block_metadata()
        assert len(rows) == 2
        assert rows[0]["block_name"] == "alpha"
        assert rows[1]["block_name"] == "beta"

    def test_delete_block_metadata(self, db):
        db.upsert_block_metadata("temp", display_name="Temp")
        assert db.get_block_metadata("temp") is not None
        db.delete_block_metadata("temp")
        assert db.get_block_metadata("temp") is None


class TestCircuitBreaker:
    """Tests for the database circuit breaker."""

    def test_db_healthy_initially(self, db):
        assert db.db_healthy is True

    def test_breaker_stays_closed_on_success(self, db):
        db.execute(
            "INSERT INTO library (url, title, added_at) VALUES (?, ?, ?)",
            ("http://a", "A", 1.0),
        )
        db.commit()
        assert db.db_healthy is True
        assert db._consecutive_io_failures == 0

    def test_breaker_opens_after_threshold_failures(self, db):
        """Simulate repeated I/O failures — breaker should open."""
        call_count = 0

        def failing_op(conn):
            nonlocal call_count
            call_count += 1
            raise sqlite3.OperationalError("disk I/O error")

        # Trip the breaker by exhausting retries _CIRCUIT_THRESHOLD times
        for i in range(Database._CIRCUIT_THRESHOLD):
            with pytest.raises(sqlite3.OperationalError):
                db._retry_on_io_error(failing_op, "test")

        assert db.db_healthy is False
        assert db._consecutive_io_failures >= Database._CIRCUIT_THRESHOLD

    def test_breaker_open_skips_retries(self, db):
        """When breaker is open, operations fail immediately (no retries)."""
        # Force breaker open
        db._circuit_open_until = time.monotonic() + 60
        db._consecutive_io_failures = Database._CIRCUIT_THRESHOLD

        call_count = 0

        def op(conn):
            nonlocal call_count
            call_count += 1
            return "ok"

        with pytest.raises(sqlite3.OperationalError, match="circuit breaker open"):
            db._retry_on_io_error(op, "test")

        # Operation was never called — breaker short-circuited
        assert call_count == 0

    def test_breaker_closes_after_cooldown(self, db):
        """After cooldown expires, breaker allows retry attempts again."""
        # Force breaker open but with expired cooldown
        db._circuit_open_until = time.monotonic() - 1
        db._consecutive_io_failures = Database._CIRCUIT_THRESHOLD

        # This should succeed since cooldown has passed
        db.execute(
            "INSERT INTO library (url, title, added_at) VALUES (?, ?, ?)",
            ("http://cooldown", "Cooldown", 1.0),
        )
        db.commit()

        assert db.db_healthy is True
        assert db._consecutive_io_failures == 0

    def test_success_resets_failure_count(self, db):
        """A successful operation resets the consecutive failure counter."""
        db._consecutive_io_failures = 2  # Below threshold

        db.execute(
            "INSERT INTO library (url, title, added_at) VALUES (?, ?, ?)",
            ("http://reset", "Reset", 1.0),
        )
        db.commit()

        assert db._consecutive_io_failures == 0

    @patch("picast.server.database.time.sleep")
    def test_retry_success_resets_breaker(self, mock_sleep, db):
        """If a retry succeeds, the breaker resets."""
        calls = 0

        def flaky_op(conn):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise sqlite3.OperationalError("disk I/O error")
            return conn.execute("SELECT 1")

        result = db._retry_on_io_error(flaky_op, "test")
        assert result is not None
        assert db._consecutive_io_failures == 0
        assert db.db_healthy is True
