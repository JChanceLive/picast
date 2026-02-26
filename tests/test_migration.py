"""Tests for database schema migrations."""

import sqlite3

from picast.server.database import Database


class TestMigrationV2ToV3:
    """Test migrating from schema v2 to v3."""

    def _create_v2_db(self, db_path: str):
        """Create a database with v2 schema (no error columns, no events table)."""
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")

        # Create v2 tables (without error columns)
        conn.executescript("""
            CREATE TABLE schema_version (version INTEGER NOT NULL);
            INSERT INTO schema_version (version) VALUES (2);

            CREATE TABLE library (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT NOT NULL,
                title TEXT NOT NULL DEFAULT '',
                source_type TEXT NOT NULL DEFAULT 'youtube',
                duration REAL DEFAULT 0,
                notes TEXT NOT NULL DEFAULT '',
                play_count INTEGER NOT NULL DEFAULT 0,
                first_played_at REAL,
                last_played_at REAL,
                added_at REAL NOT NULL,
                favorite INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE playlists (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                description TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );

            CREATE TABLE playlist_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                playlist_id INTEGER NOT NULL,
                library_id INTEGER NOT NULL,
                position INTEGER NOT NULL DEFAULT 0,
                added_at REAL NOT NULL
            );

            CREATE TABLE queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT NOT NULL,
                title TEXT NOT NULL DEFAULT '',
                source_type TEXT NOT NULL DEFAULT 'youtube',
                status TEXT NOT NULL DEFAULT 'pending',
                position INTEGER NOT NULL DEFAULT 0,
                added_at REAL NOT NULL,
                played_at REAL
            );

            CREATE INDEX idx_queue_status ON queue(status);
            CREATE INDEX idx_queue_position ON queue(position);
        """)
        conn.commit()

        # Insert some test data
        conn.execute(
            "INSERT INTO queue (url, title, source_type, status, position, added_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("https://youtube.com/watch?v=test1", "Test Video 1", "youtube", "pending", 1, 1000.0),
        )
        conn.execute(
            "INSERT INTO queue (url, title, source_type, status, position, added_at, played_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("https://youtube.com/watch?v=test2", "Test Video 2",
             "youtube", "played", 2, 1001.0, 1002.0),
        )
        conn.commit()
        conn.close()

    def test_migration_adds_error_columns(self, tmp_path):
        """v2→v3 migration adds error_count, last_error, failed_at columns."""
        db_path = str(tmp_path / "migrate.db")
        self._create_v2_db(db_path)

        # Open with Database class - should trigger migration
        db = Database(db_path)

        # Verify columns exist by querying them
        row = db.fetchone("SELECT error_count, last_error, failed_at FROM queue WHERE id = 1")
        assert row is not None
        assert row["error_count"] == 0
        assert row["last_error"] == ""
        assert row["failed_at"] is None

    def test_migration_creates_events_table(self, tmp_path):
        """v2→v3 migration creates the events table."""
        db_path = str(tmp_path / "migrate.db")
        self._create_v2_db(db_path)

        db = Database(db_path)

        # Verify events table exists
        tables = db.fetchall(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='events'"
        )
        assert len(tables) == 1

        # Verify events table has correct columns
        row = db.fetchone(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='events'"
        )
        assert "event_type" in row["sql"]
        assert "queue_item_id" in row["sql"]
        assert "title" in row["sql"]
        assert "detail" in row["sql"]
        assert "created_at" in row["sql"]

    def test_migration_preserves_existing_data(self, tmp_path):
        """v2→v3 migration preserves existing queue data."""
        db_path = str(tmp_path / "migrate.db")
        self._create_v2_db(db_path)

        db = Database(db_path)

        rows = db.fetchall("SELECT * FROM queue ORDER BY id")
        assert len(rows) == 2

        assert rows[0]["url"] == "https://youtube.com/watch?v=test1"
        assert rows[0]["title"] == "Test Video 1"
        assert rows[0]["status"] == "pending"

        assert rows[1]["url"] == "https://youtube.com/watch?v=test2"
        assert rows[1]["status"] == "played"
        assert rows[1]["played_at"] == 1002.0

    def test_migration_updates_schema_version(self, tmp_path):
        """v2→v3→...→v6 migration bumps schema_version to 6."""
        db_path = str(tmp_path / "migrate.db")
        self._create_v2_db(db_path)

        db = Database(db_path)

        row = db.fetchone("SELECT version FROM schema_version")
        assert row["version"] == 8

    def test_migration_events_indices_exist(self, tmp_path):
        """v2→v3 migration creates indices on events table."""
        db_path = str(tmp_path / "migrate.db")
        self._create_v2_db(db_path)

        db = Database(db_path)

        indices = db.fetchall(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='events'"
        )
        index_names = {r["name"] for r in indices}
        assert "idx_events_created" in index_names
        assert "idx_events_type" in index_names

    def test_fresh_db_has_v3_schema(self, tmp_path):
        """A fresh database should have v3 schema with all columns."""
        db = Database(str(tmp_path / "fresh.db"))

        # Verify queue has error columns
        row = db.fetchone(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='queue'"
        )
        assert "error_count" in row["sql"]
        assert "last_error" in row["sql"]
        assert "failed_at" in row["sql"]

        # Verify events table exists
        tables = db.fetchall(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='events'"
        )
        assert len(tables) == 1


class TestMigrationV4ToV5:
    """Test migrating from schema v4 to v5."""

    def _create_v4_db(self, db_path: str):
        """Create a database with v4 schema."""
        conn = sqlite3.connect(db_path)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript("""
            CREATE TABLE schema_version (version INTEGER NOT NULL);
            INSERT INTO schema_version (version) VALUES (4);

            CREATE TABLE library (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT NOT NULL,
                title TEXT NOT NULL DEFAULT '',
                source_type TEXT NOT NULL DEFAULT 'youtube',
                duration REAL DEFAULT 0,
                notes TEXT NOT NULL DEFAULT '',
                play_count INTEGER NOT NULL DEFAULT 0,
                first_played_at REAL,
                last_played_at REAL,
                added_at REAL NOT NULL,
                favorite INTEGER NOT NULL DEFAULT 0
            );

            CREATE TABLE playlists (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                description TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );

            CREATE TABLE playlist_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                playlist_id INTEGER NOT NULL,
                library_id INTEGER NOT NULL,
                position INTEGER NOT NULL DEFAULT 0,
                added_at REAL NOT NULL
            );

            CREATE TABLE queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT NOT NULL,
                title TEXT NOT NULL DEFAULT '',
                source_type TEXT NOT NULL DEFAULT 'youtube',
                status TEXT NOT NULL DEFAULT 'pending',
                position INTEGER NOT NULL DEFAULT 0,
                added_at REAL NOT NULL,
                played_at REAL,
                error_count INTEGER NOT NULL DEFAULT 0,
                last_error TEXT NOT NULL DEFAULT '',
                failed_at REAL
            );

            CREATE TABLE events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type TEXT NOT NULL,
                queue_item_id INTEGER,
                title TEXT NOT NULL DEFAULT '',
                detail TEXT NOT NULL DEFAULT '',
                created_at REAL NOT NULL
            );

            CREATE TABLE discover_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                url TEXT NOT NULL,
                title TEXT NOT NULL DEFAULT '',
                genre TEXT NOT NULL DEFAULT '',
                decade TEXT NOT NULL DEFAULT '',
                rolled_at REAL NOT NULL
            );
        """)
        conn.commit()

        # Insert test data
        conn.execute(
            "INSERT INTO library (url, title, added_at) VALUES (?, ?, ?)",
            ("https://youtube.com/watch?v=test", "Test Video", 1000.0),
        )
        conn.commit()
        conn.close()

    def test_v4_to_v5_creates_catalog_progress(self, tmp_path):
        """v4→v5 migration creates catalog_progress table."""
        db_path = str(tmp_path / "migrate.db")
        self._create_v4_db(db_path)
        db = Database(db_path)

        tables = db.fetchall(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='catalog_progress'"
        )
        assert len(tables) == 1

    def test_v4_to_v5_creates_watch_sessions(self, tmp_path):
        """v4→v5 migration creates watch_sessions table."""
        db_path = str(tmp_path / "migrate.db")
        self._create_v4_db(db_path)
        db = Database(db_path)

        tables = db.fetchall(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='watch_sessions'"
        )
        assert len(tables) == 1

    def test_v4_to_v5_creates_sd_errors(self, tmp_path):
        """v4→v5 migration creates sd_errors table."""
        db_path = str(tmp_path / "migrate.db")
        self._create_v4_db(db_path)
        db = Database(db_path)

        tables = db.fetchall(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='sd_errors'"
        )
        assert len(tables) == 1

    def test_v4_to_v5_preserves_data(self, tmp_path):
        """v4→v5 migration preserves existing library data."""
        db_path = str(tmp_path / "migrate.db")
        self._create_v4_db(db_path)
        db = Database(db_path)

        rows = db.fetchall("SELECT * FROM library")
        assert len(rows) == 1
        assert rows[0]["title"] == "Test Video"

    def test_v4_to_v5_updates_version(self, tmp_path):
        """v4→v5 migration bumps schema_version to 5."""
        db_path = str(tmp_path / "migrate.db")
        self._create_v4_db(db_path)
        db = Database(db_path)

        row = db.fetchone("SELECT version FROM schema_version")
        assert row["version"] == 8

    def test_fresh_db_has_v5_tables(self, tmp_path):
        """A fresh database should have all v5 tables."""
        db = Database(str(tmp_path / "fresh.db"))

        for table_name in ("catalog_progress", "watch_sessions", "sd_errors"):
            tables = db.fetchall(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (table_name,),
            )
            assert len(tables) == 1, f"Missing table: {table_name}"

    def test_fresh_db_schema_version_is_5(self, tmp_path):
        """A fresh database should have schema version 5."""
        db = Database(str(tmp_path / "fresh.db"))
        row = db.fetchone("SELECT version FROM schema_version")
        assert row["version"] == 8
