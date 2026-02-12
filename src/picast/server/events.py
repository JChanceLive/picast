"""Event bus for real-time push notifications.

Provides SSE (Server-Sent Events) push from server to connected Web UI clients.
Events are persisted to the SQLite events table and pushed to subscriber queues.
"""

import logging
import queue
import threading
import time

from picast.server.database import Database

logger = logging.getLogger(__name__)


class EventBus:
    """Thread-safe event bus with SSE subscriber management.

    Events are emitted from the player loop and pushed to all connected
    Web UI clients via SSE. Events are also persisted to the database
    for recent-events queries.
    """

    def __init__(self, db: Database):
        self._db = db
        self._subscribers: list[queue.Queue] = []
        self._lock = threading.Lock()

    def emit(
        self,
        event_type: str,
        title: str = "",
        detail: str = "",
        queue_item_id: int | None = None,
    ):
        """Emit an event to all subscribers and persist to DB.

        Args:
            event_type: Category (e.g. "error", "playback", "retry", "failed")
            title: Short human-readable summary
            detail: Longer detail text
            queue_item_id: Associated queue item ID, if any
        """
        now = time.time()

        # Persist to DB
        try:
            self._db.execute(
                "INSERT INTO events (event_type, queue_item_id, title, detail, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (event_type, queue_item_id, title, detail, now),
            )
            self._db.commit()
        except Exception as e:
            logger.warning("Failed to persist event: %s", e)

        # Build SSE payload
        event_data = {
            "type": event_type,
            "title": title,
            "detail": detail,
            "queue_item_id": queue_item_id,
            "timestamp": now,
        }

        # Push to all subscribers
        dead = []
        with self._lock:
            for q in self._subscribers:
                try:
                    q.put_nowait(event_data)
                except queue.Full:
                    dead.append(q)

            # Clean up dead subscribers
            for q in dead:
                self._subscribers.remove(q)
                logger.debug("Removed dead SSE subscriber (queue full)")

        logger.debug("Emitted event: %s - %s", event_type, title)

    def subscribe(self) -> queue.Queue:
        """Create a new SSE subscriber queue.

        Returns a Queue that will receive event dicts. Caller should
        iterate over it and format as SSE text/event-stream.
        """
        q = queue.Queue(maxsize=50)
        with self._lock:
            self._subscribers.append(q)
        logger.debug("New SSE subscriber (total: %d)", len(self._subscribers))
        return q

    def unsubscribe(self, q: queue.Queue):
        """Remove a subscriber queue."""
        with self._lock:
            try:
                self._subscribers.remove(q)
            except ValueError:
                pass
        logger.debug("SSE subscriber removed (total: %d)", len(self._subscribers))

    def recent(self, limit: int = 20) -> list[dict]:
        """Fetch recent events from the database."""
        rows = self._db.fetchall(
            "SELECT * FROM events ORDER BY created_at DESC LIMIT ?", (limit,)
        )
        return [dict(r) for r in rows]

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscribers)
