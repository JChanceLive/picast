"""Library and playlist operations for PiCast.

High-level CRUD on top of the SQLite database. Handles:
- Auto-saving played videos to library
- Search and browse
- User notes on videos
- Named playlists with ordering
"""

import logging
import time

from picast.server.database import Database

logger = logging.getLogger(__name__)


class Library:
    """Video library manager."""

    def __init__(self, db: Database):
        self.db = db

    # --- Library CRUD ---

    def add(
        self,
        url: str,
        title: str = "",
        source_type: str = "youtube",
        duration: float = 0,
    ) -> dict:
        """Add a video to the library or update if it already exists.

        Returns the library entry dict.
        """
        now = time.time()
        existing = self.get_by_url(url)

        if existing:
            # Update title/duration if we have better data
            updates = []
            params = []
            if title and not existing["title"]:
                updates.append("title = ?")
                params.append(title)
            if duration and not existing["duration"]:
                updates.append("duration = ?")
                params.append(duration)
            if updates:
                params.append(existing["id"])
                self.db.execute(
                    f"UPDATE library SET {', '.join(updates)} WHERE id = ?",
                    tuple(params),
                )
                self.db.commit()
            return self.get(existing["id"])

        self.db.execute(
            """INSERT INTO library (url, title, source_type, duration, added_at)
               VALUES (?, ?, ?, ?, ?)""",
            (url, title, source_type, duration, now),
        )
        self.db.commit()
        return self.get_by_url(url)

    def record_play(
        self, url: str, title: str = "",
        source_type: str = "youtube", duration: float = 0,
    ) -> dict:
        """Record that a video was played. Creates entry if needed, bumps play count."""
        entry = self.add(url, title, source_type, duration)
        now = time.time()
        self.db.execute(
            """UPDATE library SET
                play_count = play_count + 1,
                last_played_at = ?,
                first_played_at = COALESCE(first_played_at, ?)
               WHERE id = ?""",
            (now, now, entry["id"]),
        )
        self.db.commit()
        return self.get(entry["id"])

    def get(self, library_id: int) -> dict | None:
        """Get a library entry by ID."""
        return self.db.fetchone("SELECT * FROM library WHERE id = ?", (library_id,))

    def get_by_url(self, url: str) -> dict | None:
        """Get a library entry by URL."""
        return self.db.fetchone("SELECT * FROM library WHERE url = ?", (url,))

    def update_notes(self, library_id: int, notes: str) -> bool:
        """Update notes for a library entry."""
        self.db.execute("UPDATE library SET notes = ? WHERE id = ?", (notes, library_id))
        self.db.commit()
        return True

    def toggle_favorite(self, library_id: int) -> bool:
        """Toggle favorite status. Returns new favorite value."""
        entry = self.get(library_id)
        if not entry:
            return False
        new_val = 0 if entry["favorite"] else 1
        self.db.execute("UPDATE library SET favorite = ? WHERE id = ?", (new_val, library_id))
        self.db.commit()
        return bool(new_val)

    def delete(self, library_id: int) -> bool:
        """Delete a library entry."""
        self.db.execute("DELETE FROM library WHERE id = ?", (library_id,))
        self.db.commit()
        return True

    def search(self, query: str, limit: int = 50) -> list[dict]:
        """Search library by title or URL."""
        like = f"%{query}%"
        return self.db.fetchall(
            """SELECT * FROM library
               WHERE title LIKE ? OR url LIKE ?
               ORDER BY last_played_at DESC NULLS LAST
               LIMIT ?""",
            (like, like, limit),
        )

    def browse(
        self,
        source_type: str | None = None,
        favorites_only: bool = False,
        sort: str = "recent",
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict]:
        """Browse library with filters."""
        where_clauses = []
        params = []

        if source_type:
            where_clauses.append("source_type = ?")
            params.append(source_type)
        if favorites_only:
            where_clauses.append("favorite = 1")

        where = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

        order = {
            "recent": "last_played_at DESC NULLS LAST",
            "added": "added_at DESC",
            "title": "title ASC",
            "plays": "play_count DESC",
        }.get(sort, "last_played_at DESC NULLS LAST")

        params.extend([limit, offset])
        return self.db.fetchall(
            f"SELECT * FROM library {where} ORDER BY {order} LIMIT ? OFFSET ?",
            tuple(params),
        )

    def count(self, source_type: str | None = None) -> int:
        """Count library entries."""
        if source_type:
            row = self.db.fetchone(
                "SELECT COUNT(*) as cnt FROM library WHERE source_type = ?", (source_type,)
            )
        else:
            row = self.db.fetchone("SELECT COUNT(*) as cnt FROM library")
        return row["cnt"] if row else 0

    def recent(self, limit: int = 20) -> list[dict]:
        """Get recently played videos."""
        return self.db.fetchall(
            """SELECT * FROM library
               WHERE last_played_at IS NOT NULL
               ORDER BY last_played_at DESC LIMIT ?""",
            (limit,),
        )

    # --- Playlist Operations ---

    def create_playlist(self, name: str, description: str = "") -> dict:
        """Create a new playlist."""
        now = time.time()
        self.db.execute(
            "INSERT INTO playlists (name, description, created_at, updated_at) VALUES (?, ?, ?, ?)",
            (name, description, now, now),
        )
        self.db.commit()
        return self.db.fetchone("SELECT * FROM playlists WHERE name = ?", (name,))

    def get_playlist(self, playlist_id: int) -> dict | None:
        """Get playlist metadata."""
        return self.db.fetchone("SELECT * FROM playlists WHERE id = ?", (playlist_id,))

    def list_playlists(self) -> list[dict]:
        """List all playlists with item counts."""
        return self.db.fetchall(
            """SELECT p.*, COUNT(pi.id) as item_count
               FROM playlists p
               LEFT JOIN playlist_items pi ON p.id = pi.playlist_id
               GROUP BY p.id
               ORDER BY p.updated_at DESC"""
        )

    def update_playlist(
        self, playlist_id: int,
        name: str | None = None, description: str | None = None,
    ) -> bool:
        """Update playlist metadata."""
        updates = ["updated_at = ?"]
        params = [time.time()]
        if name is not None:
            updates.append("name = ?")
            params.append(name)
        if description is not None:
            updates.append("description = ?")
            params.append(description)
        params.append(playlist_id)
        self.db.execute(f"UPDATE playlists SET {', '.join(updates)} WHERE id = ?", tuple(params))
        self.db.commit()
        return True

    def delete_playlist(self, playlist_id: int) -> bool:
        """Delete a playlist and its items."""
        self.db.execute("DELETE FROM playlists WHERE id = ?", (playlist_id,))
        self.db.commit()
        return True

    def get_playlist_items(self, playlist_id: int) -> list[dict]:
        """Get all items in a playlist with library data."""
        return self.db.fetchall(
            """SELECT pi.id as playlist_item_id, pi.position, pi.added_at as pi_added_at,
                      l.*
               FROM playlist_items pi
               JOIN library l ON pi.library_id = l.id
               WHERE pi.playlist_id = ?
               ORDER BY pi.position ASC""",
            (playlist_id,),
        )

    def add_to_playlist(self, playlist_id: int, library_id: int) -> dict | None:
        """Add a library item to a playlist."""
        # Get next position
        row = self.db.fetchone(
            "SELECT COALESCE(MAX(position), -1) + 1 as next_pos "
            "FROM playlist_items WHERE playlist_id = ?",
            (playlist_id,),
        )
        pos = row["next_pos"] if row else 0

        try:
            self.db.execute(
                "INSERT INTO playlist_items "
                "(playlist_id, library_id, position, added_at) "
                "VALUES (?, ?, ?, ?)",
                (playlist_id, library_id, pos, time.time()),
            )
            self.db.execute(
                "UPDATE playlists SET updated_at = ? WHERE id = ?",
                (time.time(), playlist_id),
            )
            self.db.commit()
        except Exception:
            return None

        return self.db.fetchone(
            "SELECT * FROM playlist_items WHERE playlist_id = ? AND library_id = ?",
            (playlist_id, library_id),
        )

    def remove_from_playlist(self, playlist_id: int, library_id: int) -> bool:
        """Remove a library item from a playlist."""
        self.db.execute(
            "DELETE FROM playlist_items WHERE playlist_id = ? AND library_id = ?",
            (playlist_id, library_id),
        )
        self.db.execute(
            "UPDATE playlists SET updated_at = ? WHERE id = ?",
            (time.time(), playlist_id),
        )
        self.db.commit()
        return True

    def queue_playlist(self, playlist_id: int) -> list[dict]:
        """Get playlist items in order, ready for queueing."""
        return self.get_playlist_items(playlist_id)

    # --- Stats ---

    def stats(self) -> dict:
        """Get library statistics."""
        total = self.db.fetchone("SELECT COUNT(*) as cnt FROM library")
        total_count = total["cnt"] if total else 0

        plays = self.db.fetchone("SELECT COALESCE(SUM(play_count), 0) as total FROM library")
        total_plays = plays["total"] if plays else 0

        favs = self.db.fetchone("SELECT COUNT(*) as cnt FROM library WHERE favorite = 1")
        fav_count = favs["cnt"] if favs else 0

        sources = self.db.fetchall(
            "SELECT source_type, COUNT(*) as cnt "
            "FROM library GROUP BY source_type ORDER BY cnt DESC"
        )
        source_breakdown = {row["source_type"]: row["cnt"] for row in sources}

        top_played = self.db.fetchall(
            """SELECT title, url, play_count, source_type FROM library
               WHERE play_count > 0
               ORDER BY play_count DESC LIMIT 5"""
        )

        return {
            "total_videos": total_count,
            "total_plays": total_plays,
            "favorites": fav_count,
            "sources": source_breakdown,
            "top_played": top_played,
        }
