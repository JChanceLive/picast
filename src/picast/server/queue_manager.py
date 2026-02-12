"""Queue manager for PiCast.

Manages the playback queue with add, remove, reorder, and advance operations.
Persists to SQLite for reliable crash recovery.
"""

import logging
import time
from dataclasses import asdict, dataclass, field

from picast.server.database import Database

logger = logging.getLogger(__name__)


@dataclass
class QueueItem:
    """A single item in the playback queue."""

    id: int
    url: str
    title: str = ""
    source_type: str = "youtube"
    status: str = "pending"  # pending, playing, played, skipped
    added_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "QueueItem":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


def _row_to_item(row: dict) -> QueueItem:
    """Convert a database row to a QueueItem."""
    return QueueItem(
        id=row["id"],
        url=row["url"],
        title=row["title"],
        source_type=row["source_type"],
        status=row["status"],
        added_at=row["added_at"],
    )


class QueueManager:
    """Queue manager with SQLite persistence."""

    def __init__(self, db: Database):
        self._db = db

    def _detect_source(self, url: str) -> str:
        """Detect source type from URL."""
        if "youtube.com" in url or "youtu.be" in url:
            return "youtube"
        if "twitch.tv" in url:
            return "twitch"
        if url.startswith("/") or url.startswith("file://"):
            return "local"
        return "youtube"

    def add(self, url: str, title: str = "") -> QueueItem:
        """Add a URL to the end of the queue."""
        source_type = self._detect_source(url)
        now = time.time()
        cursor = self._db.execute(
            "INSERT INTO queue (url, title, source_type, status, added_at, position) "
            "VALUES (?, ?, ?, 'pending', ?, 0)",
            (url, title, source_type, now),
        )
        item_id = cursor.lastrowid
        # Set position = id so default order is insertion order
        self._db.execute("UPDATE queue SET position = ? WHERE id = ?", (item_id, item_id))
        self._db.commit()
        logger.info("Added to queue: %s", url)
        return QueueItem(id=item_id, url=url, title=title, source_type=source_type, added_at=now)

    def remove(self, item_id: int) -> bool:
        """Remove an item by ID."""
        cursor = self._db.execute("DELETE FROM queue WHERE id = ?", (item_id,))
        self._db.commit()
        return cursor.rowcount > 0

    def get_all(self) -> list[QueueItem]:
        """Get all queue items."""
        rows = self._db.fetchall("SELECT * FROM queue ORDER BY position, id")
        return [_row_to_item(r) for r in rows]

    def get_pending(self) -> list[QueueItem]:
        """Get items that haven't been played yet."""
        rows = self._db.fetchall(
            "SELECT * FROM queue WHERE status = 'pending' ORDER BY position, id"
        )
        return [_row_to_item(r) for r in rows]

    def get_next(self) -> QueueItem | None:
        """Get the next pending item without changing its status."""
        row = self._db.fetchone(
            "SELECT * FROM queue WHERE status = 'pending' ORDER BY position, id LIMIT 1"
        )
        return _row_to_item(row) if row else None

    def mark_playing(self, item_id: int) -> bool:
        """Mark an item as currently playing."""
        cursor = self._db.execute(
            "UPDATE queue SET status = 'playing' WHERE id = ?", (item_id,)
        )
        self._db.commit()
        return cursor.rowcount > 0

    def mark_played(self, item_id: int) -> bool:
        """Mark an item as played."""
        cursor = self._db.execute(
            "UPDATE queue SET status = 'played', played_at = ? WHERE id = ?",
            (time.time(), item_id),
        )
        self._db.commit()
        return cursor.rowcount > 0

    def mark_pending(self, item_id: int) -> bool:
        """Mark an item as pending (for retry after failure)."""
        cursor = self._db.execute(
            "UPDATE queue SET status = 'pending', played_at = NULL WHERE id = ?", (item_id,)
        )
        self._db.commit()
        return cursor.rowcount > 0

    def replay(self, item_id: int) -> bool:
        """Re-queue a played/skipped item at the end of the pending queue."""
        # Get the max position so we can place the replayed item at the end
        row = self._db.fetchone("SELECT MAX(position) AS max_pos FROM queue")
        max_pos = (row["max_pos"] or 0) + 1 if row else 1
        cursor = self._db.execute(
            "UPDATE queue SET status = 'pending', played_at = NULL, position = ? WHERE id = ?",
            (max_pos, item_id),
        )
        self._db.commit()
        return cursor.rowcount > 0

    def mark_skipped(self, item_id: int) -> bool:
        """Mark an item as skipped."""
        cursor = self._db.execute(
            "UPDATE queue SET status = 'skipped', played_at = ? WHERE id = ?",
            (time.time(), item_id),
        )
        self._db.commit()
        return cursor.rowcount > 0

    def get_current(self) -> QueueItem | None:
        """Get the currently playing item."""
        row = self._db.fetchone("SELECT * FROM queue WHERE status = 'playing' LIMIT 1")
        return _row_to_item(row) if row else None

    def reorder(self, item_ids: list[int]):
        """Reorder items by specifying the desired ID order.

        Only reorders pending items. Playing/played items stay in place.
        Reuses existing position slots so pending items don't jump above
        non-pending items in the display order.
        """
        # Get pending items with their current positions
        rows = self._db.fetchall(
            "SELECT id, position FROM queue WHERE status = 'pending' ORDER BY position, id"
        )
        pending_ids = {r["id"] for r in rows}
        # Sorted position slots currently held by pending items
        slots = sorted(r["position"] for r in rows)

        # Assign existing slots to the new order
        idx = 0
        seen = set()
        for item_id in item_ids:
            if item_id in pending_ids and idx < len(slots):
                self._db.execute(
                    "UPDATE queue SET position = ? WHERE id = ?", (slots[idx], item_id)
                )
                seen.add(item_id)
                idx += 1

        # Any pending items not in the reorder list get remaining slots
        for pid in pending_ids - seen:
            if idx < len(slots):
                self._db.execute(
                    "UPDATE queue SET position = ? WHERE id = ?", (slots[idx], pid)
                )
                idx += 1

        self._db.commit()

    def reset_stale_playing(self) -> int:
        """Reset any items stuck in 'playing' status back to 'pending'.

        Called on startup to recover from crashes/restarts where items
        were left in 'playing' state with no active mpv process.
        Returns number of items reset.
        """
        cursor = self._db.execute(
            "UPDATE queue SET status = 'pending' WHERE status = 'playing'"
        )
        self._db.commit()
        count = cursor.rowcount
        if count:
            logger.info("Reset %d stale 'playing' items to 'pending'", count)
        return count

    def clear_played(self):
        """Remove all played and skipped items."""
        self._db.execute("DELETE FROM queue WHERE status IN ('played', 'skipped')")
        self._db.commit()

    def clear_all(self):
        """Remove all items."""
        self._db.execute("DELETE FROM queue")
        self._db.commit()

    def import_queue_txt(self, queue_txt_path: str) -> int:
        """Import URLs from the old queue.txt format.

        Returns number of items imported.
        """
        import os

        count = 0
        try:
            with open(queue_txt_path, "r") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if line.startswith("[PLAYED] "):
                        url = line[len("[PLAYED] "):]
                        item = self.add(url)
                        self._db.execute(
                            "UPDATE queue SET status = 'played' WHERE id = ?", (item.id,)
                        )
                        self._db.commit()
                    else:
                        self.add(line)
                    count += 1
        except OSError as e:
            logger.error("Failed to import queue.txt: %s", e)
        return count
