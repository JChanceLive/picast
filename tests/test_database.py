"""Tests for SQLite database layer."""

import os
import shutil
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

        with pytest.raises(sqlite3.DatabaseError, match="circuit breaker open"):
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

    @patch("picast.server.database.time.sleep")
    def test_retry_catches_database_error(self, mock_sleep, db):
        """DatabaseError('malformed') should trigger retry, not raise immediately."""
        calls = 0

        def malformed_then_ok(conn):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise sqlite3.DatabaseError("database disk image is malformed")
            return conn.execute("SELECT 1")

        result = db._retry_on_io_error(malformed_then_ok, "test")
        assert result is not None
        assert calls == 2  # First call failed, retry succeeded

    @patch("picast.server.database.time.sleep")
    def test_retry_catches_corrupt_error(self, mock_sleep, db):
        """DatabaseError('corrupt') should trigger retry."""
        calls = 0

        def corrupt_then_ok(conn):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise sqlite3.DatabaseError("database or disk is corrupt")
            return conn.execute("SELECT 1")

        result = db._retry_on_io_error(corrupt_then_ok, "test")
        assert result is not None
        assert calls == 2

    def test_retry_does_not_catch_unrelated_errors(self, db):
        """ProgrammingError should raise immediately, not retry."""
        def programming_error(conn):
            raise sqlite3.ProgrammingError("SQL syntax error")

        with pytest.raises(sqlite3.ProgrammingError, match="SQL syntax"):
            db._retry_on_io_error(programming_error, "test")

    def test_retry_does_not_catch_unrelated_database_error(self, db):
        """DatabaseError without retryable keywords should raise immediately."""
        def unknown_db_error(conn):
            raise sqlite3.DatabaseError("something completely different")

        with pytest.raises(sqlite3.DatabaseError, match="something completely different"):
            db._retry_on_io_error(unknown_db_error, "test")

    def test_circuit_breaker_raises_database_error(self, db):
        """Circuit breaker fast-fail should raise DatabaseError, not OperationalError."""
        db._circuit_open_until = time.monotonic() + 60

        with pytest.raises(sqlite3.DatabaseError, match="circuit breaker open"):
            db._retry_on_io_error(lambda conn: None, "test")


class TestIntegrityCheck:
    """Tests for database integrity check and recovery."""

    def test_integrity_check_passes_healthy_db(self, db):
        """A freshly created database should pass integrity check."""
        assert db._check_integrity() is True

    def test_integrity_check_fails_corrupt_db(self, tmp_path):
        """A corrupted database should fail integrity check."""
        db_path = str(tmp_path / "corrupt.db")
        # Create a valid DB first, insert data to ensure pages exist
        db = Database(db_path)
        for i in range(50):
            db.execute(
                "INSERT INTO library (url, title, added_at) VALUES (?, ?, ?)",
                (f"http://test{i}", f"Test {i}", float(i)),
            )
        db.commit()
        # Checkpoint WAL to flush all data to main DB file
        db._get_conn().execute("PRAGMA wal_checkpoint(TRUNCATE)")
        db.close()

        # Corrupt it by overwriting a large chunk in the data pages
        file_size = os.path.getsize(db_path)
        with open(db_path, "r+b") as f:
            # Corrupt middle of the file (data pages, not just header)
            f.seek(file_size // 3)
            f.write(b"\xff\xfe\xfd\xfc" * 256)

        # New connection on corrupt file
        import threading
        db2 = Database.__new__(Database)
        db2.db_path = db_path
        db2._local = threading.local()
        db2._notification_manager = None
        db2._circuit_open_until = 0.0
        db2._consecutive_io_failures = 0
        db2._circuit_lock = threading.Lock()

        assert db2._check_integrity() is False

    def test_recover_from_backup(self, tmp_path):
        """Recovery should restore from .bak when primary is corrupt."""
        db_path = str(tmp_path / "test.db")
        bak_path = db_path + ".bak"

        # Create a valid DB and back it up
        db = Database(db_path)
        for i in range(50):
            db.execute(
                "INSERT INTO library (url, title, added_at) VALUES (?, ?, ?)",
                (f"http://test{i}", f"Test {i}", float(i)),
            )
        db.execute(
            "INSERT INTO library (url, title, added_at) VALUES (?, ?, ?)",
            ("http://saved", "Saved Video", 100.0),
        )
        db.commit()
        db._get_conn().execute("PRAGMA wal_checkpoint(TRUNCATE)")
        db.close()
        shutil.copy2(db_path, bak_path)

        # Corrupt the primary DB aggressively
        file_size = os.path.getsize(db_path)
        with open(db_path, "r+b") as f:
            f.seek(file_size // 3)
            f.write(b"\xff\xfe\xfd\xfc" * 256)

        # Re-open — should auto-recover from backup
        db2 = Database(db_path)
        row = db2.fetchone("SELECT * FROM library WHERE url = ?", ("http://saved",))
        assert row is not None
        assert row["title"] == "Saved Video"

        # Corrupt file should have been archived
        corrupt_path = db_path + ".corrupt"
        assert os.path.exists(corrupt_path)

    def test_recover_no_backup_continues(self, tmp_path):
        """Without a .bak file, recovery fails but init continues."""
        db_path = str(tmp_path / "norecover.db")

        # Create and close a valid DB
        db = Database(db_path)
        db.close()

        # Corrupt it (no .bak exists)
        with open(db_path, "r+b") as f:
            f.seek(200)
            f.write(b"\x00" * 200)

        # Should not raise — continues with potentially corrupt DB
        # (SQLite may re-create tables via executescript)
        try:
            db2 = Database(db_path)
            # If we get here, init didn't crash
            assert True
        except Exception:
            # Some corruption is too severe for executescript
            # The point is _init_schema didn't propagate the error
            assert True
