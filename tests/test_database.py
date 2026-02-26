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
        assert row["version"] == 10

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
