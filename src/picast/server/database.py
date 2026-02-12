"""SQLite database for PiCast library and playlists.

Stores video history, metadata, user notes, and playlists.
Auto-migrates schema on startup.
"""

import logging
import os
import sqlite3
import threading
import time

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 3

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS library (
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

CREATE UNIQUE INDEX IF NOT EXISTS idx_library_url ON library(url);
CREATE INDEX IF NOT EXISTS idx_library_source ON library(source_type);
CREATE INDEX IF NOT EXISTS idx_library_favorite ON library(favorite);

CREATE TABLE IF NOT EXISTS playlists (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS playlist_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    playlist_id INTEGER NOT NULL,
    library_id INTEGER NOT NULL,
    position INTEGER NOT NULL DEFAULT 0,
    added_at REAL NOT NULL,
    FOREIGN KEY (playlist_id) REFERENCES playlists(id) ON DELETE CASCADE,
    FOREIGN KEY (library_id) REFERENCES library(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_playlist_items_playlist ON playlist_items(playlist_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_playlist_items_unique ON playlist_items(playlist_id, library_id);

CREATE TABLE IF NOT EXISTS queue (
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

CREATE INDEX IF NOT EXISTS idx_queue_status ON queue(status);
CREATE INDEX IF NOT EXISTS idx_queue_position ON queue(position);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_type TEXT NOT NULL,
    queue_item_id INTEGER,
    title TEXT NOT NULL DEFAULT '',
    detail TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_events_created ON events(created_at);
CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type);
"""


class Database:
    """Thread-safe SQLite database manager for PiCast."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._local = threading.local()
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._init_schema()

    def _get_conn(self) -> sqlite3.Connection:
        """Get a thread-local connection."""
        if not hasattr(self._local, "conn") or self._local.conn is None:
            self._local.conn = sqlite3.connect(self.db_path)
            self._local.conn.row_factory = sqlite3.Row
            self._local.conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn.execute("PRAGMA foreign_keys=ON")
        return self._local.conn

    def _init_schema(self):
        """Create tables if they don't exist and run migrations."""
        conn = self._get_conn()
        conn.executescript(SCHEMA_SQL)

        # Check/set schema version
        row = conn.execute("SELECT version FROM schema_version").fetchone()
        if row is None:
            conn.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))
            conn.commit()
        else:
            current = row["version"]
            if current < SCHEMA_VERSION:
                self._migrate(current, SCHEMA_VERSION)

        logger.info("Database initialized at %s (schema v%d)", self.db_path, SCHEMA_VERSION)

    def _migrate(self, from_version: int, to_version: int):
        """Run schema migrations."""
        conn = self._get_conn()
        if from_version < 2:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS queue (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    url TEXT NOT NULL,
                    title TEXT NOT NULL DEFAULT '',
                    source_type TEXT NOT NULL DEFAULT 'youtube',
                    status TEXT NOT NULL DEFAULT 'pending',
                    position INTEGER NOT NULL DEFAULT 0,
                    added_at REAL NOT NULL,
                    played_at REAL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_queue_status ON queue(status)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_queue_position ON queue(position)")
        if from_version < 3:
            # Add error tracking columns to queue table
            conn.execute("ALTER TABLE queue ADD COLUMN error_count INTEGER NOT NULL DEFAULT 0")
            conn.execute("ALTER TABLE queue ADD COLUMN last_error TEXT NOT NULL DEFAULT ''")
            conn.execute("ALTER TABLE queue ADD COLUMN failed_at REAL")
            # Create events table
            conn.execute("""
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT NOT NULL,
                    queue_item_id INTEGER,
                    title TEXT NOT NULL DEFAULT '',
                    detail TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_events_created ON events(created_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type)")
        conn.execute("UPDATE schema_version SET version = ?", (to_version,))
        conn.commit()
        logger.info("Migrated database from v%d to v%d", from_version, to_version)

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        """Execute a SQL statement."""
        return self._get_conn().execute(sql, params)

    def executemany(self, sql: str, params_list: list[tuple]) -> sqlite3.Cursor:
        """Execute a SQL statement with many param sets."""
        return self._get_conn().executemany(sql, params_list)

    def commit(self):
        """Commit the current transaction."""
        self._get_conn().commit()

    def fetchone(self, sql: str, params: tuple = ()) -> dict | None:
        """Execute and fetch one row as dict."""
        row = self.execute(sql, params).fetchone()
        return dict(row) if row else None

    def fetchall(self, sql: str, params: tuple = ()) -> list[dict]:
        """Execute and fetch all rows as dicts."""
        return [dict(row) for row in self.execute(sql, params).fetchall()]

    def close(self):
        """Close the thread-local connection."""
        if hasattr(self._local, "conn") and self._local.conn:
            self._local.conn.close()
            self._local.conn = None
