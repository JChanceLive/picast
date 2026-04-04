"""SQLite database for PiCast library and playlists.

Stores video history, metadata, user notes, and playlists.
Auto-migrates schema on startup.
"""

import logging
import os
import shutil
import sqlite3
import threading
import time

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 11

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
    skip_count INTEGER NOT NULL DEFAULT 0,
    completion_count INTEGER NOT NULL DEFAULT 0,
    duration INTEGER NOT NULL DEFAULT 0,
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
    completed INTEGER NOT NULL DEFAULT 0,
    stop_reason TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_autoplay_history_block ON autoplay_history(block_name);
CREATE INDEX IF NOT EXISTS idx_autoplay_history_played ON autoplay_history(played_at);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS autoplay_seasonal_tags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id TEXT NOT NULL,
    season TEXT NOT NULL,
    UNIQUE(video_id, season)
);

CREATE TABLE IF NOT EXISTS autoplay_cross_block_prefs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id TEXT NOT NULL,
    source_block TEXT NOT NULL,
    signal_type TEXT NOT NULL,
    signal_strength REAL NOT NULL DEFAULT 1.0,
    created_at TEXT NOT NULL,
    UNIQUE(video_id, source_block, signal_type)
);

CREATE TABLE IF NOT EXISTS block_metadata (
    block_name TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    block_start TEXT,
    block_end TEXT,
    emoji TEXT,
    tagline TEXT,
    block_type TEXT,
    energy TEXT,
    source TEXT DEFAULT 'manual',
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS autopilot_profile (
    id INTEGER PRIMARY KEY DEFAULT 1,
    profile_json TEXT NOT NULL,
    generated_at TEXT NOT NULL,
    loaded_at TEXT DEFAULT (datetime('now')),
    version INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS autopilot_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT DEFAULT (datetime('now')),
    action TEXT NOT NULL,
    video_id TEXT,
    block_name TEXT,
    source TEXT,
    score REAL,
    reason TEXT
);

CREATE INDEX IF NOT EXISTS idx_autopilot_log_action ON autopilot_log(action);
CREATE INDEX IF NOT EXISTS idx_autopilot_log_timestamp ON autopilot_log(timestamp);

CREATE TABLE IF NOT EXISTS autopilot_devices (
    device_id TEXT PRIMARY KEY,
    room_name TEXT,
    device_type TEXT DEFAULT 'single',
    last_seen TEXT,
    is_manual_override BOOLEAN DEFAULT 0
);
"""


class Database:
    """Thread-safe SQLite database manager for PiCast."""

    # Circuit breaker: skip retries after repeated I/O failures
    _CIRCUIT_THRESHOLD = 3    # open after N consecutive failures
    _CIRCUIT_COOLDOWN = 30.0  # seconds before re-testing

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._local = threading.local()
        self._notification_manager = None
        self._circuit_open_until: float = 0.0
        self._consecutive_io_failures: int = 0
        self._circuit_lock = threading.Lock()
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        self._init_schema()

    def set_notification_manager(self, manager):
        """Set the notification manager for SD error alerts."""
        self._notification_manager = manager

    @property
    def db_healthy(self) -> bool:
        """True when the circuit breaker is closed (DB presumed healthy)."""
        return time.monotonic() >= self._circuit_open_until

    def _get_conn(self) -> sqlite3.Connection:
        """Get a thread-local connection."""
        if not hasattr(self._local, "conn") or self._local.conn is None:
            self._local.conn = sqlite3.connect(self.db_path)
            self._local.conn.row_factory = sqlite3.Row
            self._local.conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn.execute("PRAGMA foreign_keys=ON")
            self._local.conn.execute("PRAGMA busy_timeout=5000")
        return self._local.conn

    def _check_integrity(self) -> bool:
        """Run PRAGMA integrity_check on the database.

        Returns True if the database passes integrity checks.
        """
        try:
            conn = self._get_conn()
            result = conn.execute("PRAGMA integrity_check").fetchone()
            if result and result[0] == "ok":
                logger.info("Database integrity check passed")
                return True
            logger.error("Database integrity check FAILED: %s", result[0] if result else "no result")
            return False
        except sqlite3.DatabaseError as e:
            logger.error("Database integrity check error: %s", e)
            return False

    def _recover_from_backup(self) -> bool:
        """Attempt to recover from .bak file when primary DB is corrupt.

        Archives the corrupt DB as .corrupt, removes WAL/SHM files,
        and copies .bak to the primary path. Returns True on success.
        """
        bak_path = self.db_path + ".bak"
        if not os.path.exists(bak_path):
            logger.warning("No backup file found at %s — cannot recover", bak_path)
            return False

        # Close current connection to release file handles
        self.close()

        corrupt_path = self.db_path + ".corrupt"
        try:
            # Archive corrupt DB
            shutil.move(self.db_path, corrupt_path)
            logger.info("Archived corrupt DB to %s", corrupt_path)

            # Remove WAL/SHM files (stale after corruption)
            for suffix in (".db-wal", ".db-shm"):
                wal_path = self.db_path + suffix.replace(".db", "")
                if os.path.exists(wal_path):
                    os.remove(wal_path)
                    logger.info("Removed stale %s", wal_path)

            # Copy backup to primary
            shutil.copy2(bak_path, self.db_path)
            logger.info("Restored database from backup: %s", bak_path)
            return True
        except OSError as e:
            logger.error("Recovery from backup failed: %s", e)
            # Try to restore the corrupt file if the copy failed
            if not os.path.exists(self.db_path) and os.path.exists(corrupt_path):
                try:
                    shutil.move(corrupt_path, self.db_path)
                except OSError:
                    pass
            return False

    def _init_schema(self):
        """Create tables if they don't exist and run migrations."""
        # Check integrity before schema init — recover from backup if corrupt
        if os.path.exists(self.db_path):
            if not self._check_integrity():
                if self._recover_from_backup():
                    logger.info("Recovery successful, proceeding with restored database")
                else:
                    logger.warning("Recovery failed — proceeding with potentially corrupt database")

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
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_discover_history_url ON discover_history(url)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_discover_history_rolled "
                "ON discover_history(rolled_at)"
            )
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
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_watch_sessions_started "
                "ON watch_sessions(started_at)"
            )
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sd_errors (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    error_type TEXT NOT NULL,
                    detail TEXT NOT NULL DEFAULT '',
                    occurred_at REAL NOT NULL
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_sd_errors_occurred ON sd_errors(occurred_at)"
            )
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
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_autoplay_videos_block "
                "ON autoplay_videos(block_name)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_autoplay_videos_active ON autoplay_videos(active)"
            )
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
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_autoplay_history_block "
                "ON autoplay_history(block_name)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_autoplay_history_played "
                "ON autoplay_history(played_at)"
            )
        if from_version < 7:
            # Self-learning columns for autoplay
            for col in [
                "ALTER TABLE autoplay_videos "
                "ADD COLUMN skip_count INTEGER NOT NULL DEFAULT 0",
                "ALTER TABLE autoplay_videos "
                "ADD COLUMN completion_count INTEGER NOT NULL DEFAULT 0",
                "ALTER TABLE autoplay_videos "
                "ADD COLUMN duration INTEGER NOT NULL DEFAULT 0",
                "ALTER TABLE autoplay_history "
                "ADD COLUMN stop_reason TEXT NOT NULL DEFAULT ''",
            ]:
                try:
                    conn.execute(col)
                except sqlite3.OperationalError:
                    pass  # Column already exists
        if from_version < 8:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
            """)
        if from_version < 9:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS autoplay_seasonal_tags (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    video_id TEXT NOT NULL,
                    season TEXT NOT NULL,
                    UNIQUE(video_id, season)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS autoplay_cross_block_prefs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    video_id TEXT NOT NULL,
                    source_block TEXT NOT NULL,
                    signal_type TEXT NOT NULL,
                    signal_strength REAL NOT NULL DEFAULT 1.0,
                    created_at TEXT NOT NULL,
                    UNIQUE(video_id, source_block, signal_type)
                )
            """)
        if from_version < 10:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS block_metadata (
                    block_name TEXT PRIMARY KEY,
                    display_name TEXT NOT NULL,
                    block_start TEXT,
                    block_end TEXT,
                    emoji TEXT,
                    tagline TEXT,
                    block_type TEXT,
                    energy TEXT,
                    source TEXT DEFAULT 'manual',
                    updated_at TEXT
                )
            """)
        if from_version < 11:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS autopilot_profile (
                    id INTEGER PRIMARY KEY DEFAULT 1,
                    profile_json TEXT NOT NULL,
                    generated_at TEXT NOT NULL,
                    loaded_at TEXT DEFAULT (datetime('now')),
                    version INTEGER NOT NULL DEFAULT 1
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS autopilot_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT DEFAULT (datetime('now')),
                    action TEXT NOT NULL,
                    video_id TEXT,
                    block_name TEXT,
                    source TEXT,
                    score REAL,
                    reason TEXT
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_autopilot_log_action "
                "ON autopilot_log(action)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_autopilot_log_timestamp "
                "ON autopilot_log(timestamp)"
            )
            conn.execute("""
                CREATE TABLE IF NOT EXISTS autopilot_devices (
                    device_id TEXT PRIMARY KEY,
                    room_name TEXT,
                    device_type TEXT DEFAULT 'single',
                    last_seen TEXT,
                    is_manual_override BOOLEAN DEFAULT 0
                )
            """)
        conn.execute("UPDATE schema_version SET version = ?", (to_version,))
        conn.commit()
        logger.info("Migrated database from v%d to v%d", from_version, to_version)

    # SD card controller errors (mmc1: Controller never released inhibit bit(s))
    # can persist for 10+ seconds. Exponential backoff: 0.5, 1, 2, 4, 8 = 15.5s total.
    _RETRY_DELAYS = [0.5, 1.0, 2.0, 4.0, 8.0]

    def _retry_on_io_error(self, operation, description: str = "DB operation"):
        """Run a DB operation with retry+backoff for SD card I/O errors.

        Uses a circuit breaker: after _CIRCUIT_THRESHOLD consecutive I/O
        failures, skip the 15.5s retry loop and fail immediately for
        _CIRCUIT_COOLDOWN seconds.  Any success resets the breaker.
        """
        # Circuit breaker: fail fast when DB is known-bad
        if time.monotonic() < self._circuit_open_until:
            raise sqlite3.DatabaseError(
                f"disk I/O error (circuit breaker open, retries skipped for {description})"
            )

        try:
            result = operation(self._get_conn())
            # Success — reset breaker
            self._reset_circuit()
            return result
        except sqlite3.DatabaseError as e:
            err = str(e)
            if not any(
                s in err
                for s in ("disk I/O error", "database is locked", "malformed", "corrupt")
            ):
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
                    description,
                    attempt,
                    len(self._RETRY_DELAYS),
                    e,
                    delay,
                )
                self.close()
                time.sleep(delay)
                try:
                    result = operation(self._get_conn())
                    self._reset_circuit()
                    return result
                except sqlite3.DatabaseError as retry_e:
                    last_exc = retry_e
            # All retries exhausted — trip the breaker
            self._trip_circuit()
            raise last_exc

    def _reset_circuit(self):
        """Reset circuit breaker on successful DB operation."""
        with self._circuit_lock:
            self._consecutive_io_failures = 0
            self._circuit_open_until = 0.0

    def _trip_circuit(self):
        """Increment failure count; open breaker if threshold reached."""
        with self._circuit_lock:
            self._consecutive_io_failures += 1
            if self._consecutive_io_failures >= self._CIRCUIT_THRESHOLD:
                self._circuit_open_until = time.monotonic() + self._CIRCUIT_COOLDOWN
                logger.error(
                    "Circuit breaker OPEN: %d consecutive I/O failures, "
                    "skipping retries for %.0fs",
                    self._consecutive_io_failures,
                    self._CIRCUIT_COOLDOWN,
                )

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        """Execute a SQL statement. Retries on I/O error with backoff."""
        return self._retry_on_io_error(lambda conn: conn.execute(sql, params), "execute")

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

    def get_setting(self, key: str, default: str | None = None) -> str | None:
        """Get a setting value by key."""
        row = self.fetchone("SELECT value FROM settings WHERE key = ?", (key,))
        return row["value"] if row else default

    def set_setting(self, key: str, value: str):
        """Set a setting value (upsert)."""
        self.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        self.commit()

    # --- Block Metadata CRUD ---

    def get_all_block_metadata(self) -> list[dict]:
        """Get all block metadata rows."""
        return self.fetchall("SELECT * FROM block_metadata ORDER BY block_name")

    def get_block_metadata(self, block_name: str) -> dict | None:
        """Get metadata for a single block."""
        return self.fetchone("SELECT * FROM block_metadata WHERE block_name = ?", (block_name,))

    def upsert_block_metadata(self, block_name: str, **fields):
        """Insert or update block metadata."""
        existing = self.get_block_metadata(block_name)
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        if existing:
            sets = ", ".join(f"{k} = ?" for k in fields)
            vals = list(fields.values()) + [now, block_name]
            self.execute(
                f"UPDATE block_metadata SET {sets}, updated_at = ? WHERE block_name = ?",
                tuple(vals),
            )
        else:
            fields["block_name"] = block_name
            fields["updated_at"] = now
            cols = ", ".join(fields.keys())
            placeholders = ", ".join("?" for _ in fields)
            self.execute(
                f"INSERT INTO block_metadata ({cols}) VALUES ({placeholders})",
                tuple(fields.values()),
            )
        self.commit()

    def delete_block_metadata(self, block_name: str):
        """Delete metadata for a block."""
        self.execute("DELETE FROM block_metadata WHERE block_name = ?", (block_name,))
        self.commit()

    def close(self):
        """Close the thread-local connection."""
        if hasattr(self._local, "conn") and self._local.conn:
            self._local.conn.close()
            self._local.conn = None
