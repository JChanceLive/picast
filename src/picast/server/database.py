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

SCHEMA_VERSION = 6

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
CREATE UNIQUE INDEX IF NOT EXISTS idx_playlist_items_unique
    ON playlist_items(playlist_id, library_id);

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

CREATE TABLE IF NOT EXISTS discover_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    genre TEXT NOT NULL DEFAULT '',
    decade TEXT NOT NULL DEFAULT '',
    rolled_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_discover_history_url ON discover_history(url);
CREATE INDEX IF NOT EXISTS idx_discover_history_rolled ON discover_history(rolled_at);

CREATE TABLE IF NOT EXISTS catalog_progress (
    series_id TEXT UNIQUE NOT NULL,
    last_episode_index INTEGER NOT NULL DEFAULT 0,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS watch_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    source_type TEXT NOT NULL DEFAULT 'unknown',
    started_at REAL NOT NULL,
    ended_at REAL NOT NULL,
    duration_watched REAL NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_watch_sessions_started ON watch_sessions(started_at);

CREATE TABLE IF NOT EXISTS sd_errors (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    error_type TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    occurred_at REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sd_errors_occurred ON sd_errors(occurred_at);

CREATE TABLE IF NOT EXISTS autoplay_videos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    block_name TEXT NOT NULL,
    tags TEXT NOT NULL DEFAULT '',
    rating INTEGER NOT NULL DEFAULT 0,
    play_count INTEGER NOT NULL DEFAULT 0,
    last_played TEXT,
    added_date TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'manual',
    active INTEGER NOT NULL DEFAULT 1,
    UNIQUE(video_id, block_name)
);

CREATE INDEX IF NOT EXISTS idx_autoplay_videos_block ON autoplay_videos(block_name);
CREATE INDEX IF NOT EXISTS idx_autoplay_videos_active ON autoplay_videos(active);

CREATE TABLE IF NOT EXISTS autoplay_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id TEXT NOT NULL,
    block_name TEXT NOT NULL,
    played_at TEXT NOT NULL,
    duration_watched INTEGER NOT NULL DEFAULT 0,
    completed INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_autoplay_history_block ON autoplay_history(block_name);
CREATE INDEX IF NOT EXISTS idx_autoplay_history_played ON autoplay_history(played_at);
"""


class Database:
    """Thread-safe SQLite database manager for PiCast."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._local = threading.local()
        self._notification_manager = None
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._init_schema()

    def set_notification_manager(self, manager):
        """Set the notification manager for SD error alerts."""
        self._notification_manager = manager

    def _get_conn(self) -> sqlite3.Connection:
        """Get a thread-local connection."""
        if not hasattr(self._local, "conn") or self._local.conn is None:
            self._local.conn = sqlite3.connect(self.db_path)
            self._local.conn.row_factory = sqlite3.Row
            self._local.conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn.execute("PRAGMA foreign_keys=ON")
            self._local.conn.execute("PRAGMA busy_timeout=5000")
        return self._local.conn

    def _init_schema(self):
        """Create tables if they don't exist and run migrations."""
        conn = self._get_conn()
        conn.executescript(SCHEMA_SQL)

        # Check/set schema version
        row = conn.execute("SELECT version FROM schema_version").fetchone()
        if row is None:
            conn.execute("INSERT INTO schema_version (version) VALUES (?)", (SCHEMA_VERSION,))
        else:
            current = row["version"]
            if current < SCHEMA_VERSION:
                self._migrate(current, SCHEMA_VERSION)
        conn.commit()  # Ensure no open transaction after init

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
        if from_version < 4:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS discover_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    url TEXT NOT NULL,
                    title TEXT NOT NULL DEFAULT '',
                    genre TEXT NOT NULL DEFAULT '',
                    decade TEXT NOT NULL DEFAULT '',
                    rolled_at REAL NOT NULL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_discover_history_url ON discover_history(url)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_discover_history_rolled ON discover_history(rolled_at)")
        if from_version < 5:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS catalog_progress (
                    series_id TEXT UNIQUE NOT NULL,
                    last_episode_index INTEGER NOT NULL DEFAULT 0,
                    updated_at REAL NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS watch_sessions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    url TEXT NOT NULL,
                    title TEXT NOT NULL DEFAULT '',
                    source_type TEXT NOT NULL DEFAULT 'unknown',
                    started_at REAL NOT NULL,
                    ended_at REAL NOT NULL,
                    duration_watched REAL NOT NULL DEFAULT 0
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_watch_sessions_started ON watch_sessions(started_at)")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sd_errors (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    error_type TEXT NOT NULL,
                    detail TEXT NOT NULL DEFAULT '',
                    occurred_at REAL NOT NULL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_sd_errors_occurred ON sd_errors(occurred_at)")
        if from_version < 6:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS autoplay_videos (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    video_id TEXT NOT NULL,
                    title TEXT NOT NULL DEFAULT '',
                    block_name TEXT NOT NULL,
                    tags TEXT NOT NULL DEFAULT '',
                    rating INTEGER NOT NULL DEFAULT 0,
                    play_count INTEGER NOT NULL DEFAULT 0,
                    last_played TEXT,
                    added_date TEXT NOT NULL,
                    source TEXT NOT NULL DEFAULT 'manual',
                    active INTEGER NOT NULL DEFAULT 1,
                    UNIQUE(video_id, block_name)
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_autoplay_videos_block ON autoplay_videos(block_name)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_autoplay_videos_active ON autoplay_videos(active)")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS autoplay_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    video_id TEXT NOT NULL,
                    block_name TEXT NOT NULL,
                    played_at TEXT NOT NULL,
                    duration_watched INTEGER NOT NULL DEFAULT 0,
                    completed INTEGER NOT NULL DEFAULT 0
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_autoplay_history_block ON autoplay_history(block_name)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_autoplay_history_played ON autoplay_history(played_at)")
        conn.execute("UPDATE schema_version SET version = ?", (to_version,))
        conn.commit()
        logger.info("Migrated database from v%d to v%d", from_version, to_version)

    # SD card controller errors (mmc1: Controller never released inhibit bit(s))
    # can persist for 10+ seconds. Exponential backoff: 0.5, 1, 2, 4, 8 = 15.5s total.
    _RETRY_DELAYS = [0.5, 1.0, 2.0, 4.0, 8.0]

    def _retry_on_io_error(self, operation, description: str = "DB operation"):
        """Run a DB operation with retry+backoff for SD card I/O errors."""
        try:
            return operation(self._get_conn())
        except sqlite3.OperationalError as e:
            err = str(e)
            if "disk I/O error" not in err and "database is locked" not in err:
                raise
            # Record SD error for notification manager
            if self._notification_manager and "disk I/O error" in err:
                try:
                    self._notification_manager.record_sd_error("disk_io", err)
                except Exception:
                    pass  # Don't let notification failures block retries
            last_exc = e
            for attempt, delay in enumerate(self._RETRY_DELAYS, start=1):
                logger.warning(
                    "SQLite %s error (attempt %d/%d): %s — retrying in %.1fs",
                    description, attempt, len(self._RETRY_DELAYS), e, delay,
                )
                self.close()
                time.sleep(delay)
                try:
                    return operation(self._get_conn())
                except sqlite3.OperationalError as retry_e:
                    last_exc = retry_e
            raise last_exc

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        """Execute a SQL statement. Retries on I/O error with backoff."""
        return self._retry_on_io_error(
            lambda conn: conn.execute(sql, params), "execute"
        )

    def executemany(self, sql: str, params_list: list[tuple]) -> sqlite3.Cursor:
        """Execute a SQL statement with many param sets."""
        return self._retry_on_io_error(
            lambda conn: conn.executemany(sql, params_list), "executemany"
        )

    def commit(self):
        """Commit the current transaction."""
        self._retry_on_io_error(lambda conn: conn.commit(), "commit")

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
