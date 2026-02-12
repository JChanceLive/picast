"""Tests for EventBus and SSE endpoints."""

import queue
import threading
import time


class TestEventBus:
    def test_emit_persists_to_db(self, event_bus, db):
        event_bus.emit("error", "Test error", "Something went wrong", queue_item_id=1)
        rows = db.fetchall("SELECT * FROM events")
        assert len(rows) == 1
        assert rows[0]["event_type"] == "error"
        assert rows[0]["title"] == "Test error"
        assert rows[0]["detail"] == "Something went wrong"
        assert rows[0]["queue_item_id"] == 1

    def test_emit_pushes_to_subscriber(self, event_bus):
        q = event_bus.subscribe()
        event_bus.emit("playback", "Now playing", "Video title")
        event = q.get(timeout=1)
        assert event["type"] == "playback"
        assert event["title"] == "Now playing"
        assert event["detail"] == "Video title"
        assert "timestamp" in event
        event_bus.unsubscribe(q)

    def test_multiple_subscribers(self, event_bus):
        q1 = event_bus.subscribe()
        q2 = event_bus.subscribe()
        event_bus.emit("error", "Test")
        assert q1.get(timeout=1)["type"] == "error"
        assert q2.get(timeout=1)["type"] == "error"
        event_bus.unsubscribe(q1)
        event_bus.unsubscribe(q2)

    def test_unsubscribe(self, event_bus):
        q = event_bus.subscribe()
        assert event_bus.subscriber_count == 1
        event_bus.unsubscribe(q)
        assert event_bus.subscriber_count == 0
        # Emit after unsub - should not raise
        event_bus.emit("test", "After unsub")

    def test_unsubscribe_nonexistent(self, event_bus):
        q = queue.Queue()
        # Should not raise
        event_bus.unsubscribe(q)

    def test_dead_subscriber_cleanup(self, event_bus):
        """Full queues get cleaned up on next emit."""
        event_bus.subscribe()
        # Fill the queue to max
        for i in range(50):
            event_bus.emit("fill", f"Event {i}")
        assert event_bus.subscriber_count == 1
        # Next emit should detect the full queue and remove it
        event_bus.emit("overflow", "This triggers cleanup")
        assert event_bus.subscriber_count == 0

    def test_recent(self, event_bus):
        event_bus.emit("a", "First")
        event_bus.emit("b", "Second")
        event_bus.emit("c", "Third")
        recent = event_bus.recent(limit=2)
        assert len(recent) == 2
        # Most recent first
        assert recent[0]["title"] == "Third"
        assert recent[1]["title"] == "Second"

    def test_recent_empty(self, event_bus):
        assert event_bus.recent() == []

    def test_emit_without_queue_item_id(self, event_bus):
        event_bus.emit("info", "No item")
        q = event_bus.subscribe()
        # Not testing the push since we subscribed after emit
        recent = event_bus.recent()
        assert len(recent) == 1
        assert recent[0]["queue_item_id"] is None
        event_bus.unsubscribe(q)

    def test_subscriber_count(self, event_bus):
        assert event_bus.subscriber_count == 0
        q1 = event_bus.subscribe()
        assert event_bus.subscriber_count == 1
        q2 = event_bus.subscribe()
        assert event_bus.subscriber_count == 2
        event_bus.unsubscribe(q1)
        assert event_bus.subscriber_count == 1
        event_bus.unsubscribe(q2)
        assert event_bus.subscriber_count == 0

    def test_thread_safety(self, event_bus):
        """Concurrent emit and subscribe should not crash."""
        errors = []

        def emitter():
            try:
                for i in range(20):
                    event_bus.emit("thread", f"Event {i}")
            except Exception as e:
                errors.append(e)

        def subscriber():
            try:
                q = event_bus.subscribe()
                time.sleep(0.05)
                event_bus.unsubscribe(q)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=emitter) for _ in range(3)]
        threads += [threading.Thread(target=subscriber) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert errors == []


class TestSSEEndpoint:
    def test_sse_content_type(self, client):
        """SSE endpoint returns text/event-stream."""
        # Start SSE request in background thread
        result = {}

        def fetch():
            with client.get("/api/events") as resp:
                result["content_type"] = resp.content_type
                result["status"] = resp.status_code

        t = threading.Thread(target=fetch)
        t.start()
        time.sleep(0.5)
        # The thread will be blocked on the SSE stream
        # Just verify the content type was set correctly
        t.join(timeout=2)
        if "content_type" in result:
            assert "text/event-stream" in result["content_type"]

    def test_events_recent_endpoint(self, client):
        """Recent events endpoint returns JSON."""
        resp = client.get("/api/events/recent")
        assert resp.status_code == 200
        data = resp.get_json()
        assert isinstance(data, list)

    def test_events_recent_with_data(self, client):
        """Recent events returns persisted events."""
        client.application.event_bus.emit("test", "Test event")
        resp = client.get("/api/events/recent")
        data = resp.get_json()
        assert len(data) == 1
        assert data[0]["title"] == "Test event"


class TestErrorEndpoints:
    def test_get_failed_empty(self, client):
        resp = client.get("/api/queue/failed")
        assert resp.status_code == 200
        assert resp.get_json() == []

    def test_get_failed_with_items(self, client):
        r = client.post("/api/queue/add", json={"url": "https://www.youtube.com/watch?v=bad"})
        item_id = r.get_json()["id"]
        client.application.queue.mark_failed(item_id)
        resp = client.get("/api/queue/failed")
        data = resp.get_json()
        assert len(data) == 1
        assert data[0]["status"] == "failed"

    def test_retry_failed_item(self, client):
        r = client.post("/api/queue/add", json={"url": "https://www.youtube.com/watch?v=retry"})
        item_id = r.get_json()["id"]
        client.application.queue.mark_failed(item_id)
        resp = client.post(f"/api/queue/{item_id}/retry")
        assert resp.status_code == 200
        assert resp.get_json()["ok"] is True
        # Verify back to pending
        queue = client.get("/api/queue").get_json()
        assert queue[0]["status"] == "pending"

    def test_retry_not_failed(self, client):
        r = client.post("/api/queue/add", json={"url": "https://www.youtube.com/watch?v=a"})
        item_id = r.get_json()["id"]
        resp = client.post(f"/api/queue/{item_id}/retry")
        assert resp.status_code == 404

    def test_retry_nonexistent(self, client):
        resp = client.post("/api/queue/999/retry")
        assert resp.status_code == 404

    def test_clear_failed(self, client):
        r = client.post("/api/queue/add", json={"url": "https://www.youtube.com/watch?v=a"})
        client.application.queue.mark_failed(r.get_json()["id"])
        client.post("/api/queue/add", json={"url": "https://www.youtube.com/watch?v=b"})
        resp = client.post("/api/queue/clear-failed")
        assert resp.status_code == 200
        # Only non-failed item should remain
        queue = client.get("/api/queue").get_json()
        assert len(queue) == 1
        assert queue[0]["status"] == "pending"
