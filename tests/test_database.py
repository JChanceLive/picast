"""Tests for SQLite database layer."""

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
        row = db.fetchone("SELECT version FROM schema_version")
        assert row["version"] == 3

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
