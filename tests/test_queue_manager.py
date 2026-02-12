"""Tests for QueueManager."""


from picast.server.database import Database
from picast.server.queue_manager import QueueManager


class TestQueueManager:
    def test_add_item(self, queue):
        item = queue.add("https://www.youtube.com/watch?v=abc123")
        assert item.id == 1
        assert item.url == "https://www.youtube.com/watch?v=abc123"
        assert item.status == "pending"
        assert item.source_type == "youtube"

    def test_add_multiple(self, queue):
        item1 = queue.add("https://www.youtube.com/watch?v=abc")
        item2 = queue.add("https://www.youtube.com/watch?v=def")
        assert item1.id == 1
        assert item2.id == 2

    def test_get_all(self, queue):
        queue.add("https://www.youtube.com/watch?v=a")
        queue.add("https://www.youtube.com/watch?v=b")
        items = queue.get_all()
        assert len(items) == 2

    def test_get_next(self, queue):
        queue.add("https://www.youtube.com/watch?v=a")
        queue.add("https://www.youtube.com/watch?v=b")
        nxt = queue.get_next()
        assert nxt.url == "https://www.youtube.com/watch?v=a"

    def test_get_next_skips_playing(self, queue):
        item1 = queue.add("https://www.youtube.com/watch?v=a")
        queue.add("https://www.youtube.com/watch?v=b")
        queue.mark_playing(item1.id)
        nxt = queue.get_next()
        assert nxt.url == "https://www.youtube.com/watch?v=b"

    def test_get_next_empty(self, queue):
        assert queue.get_next() is None

    def test_mark_playing(self, queue):
        item = queue.add("https://www.youtube.com/watch?v=a")
        queue.mark_playing(item.id)
        current = queue.get_current()
        assert current.id == item.id

    def test_mark_played(self, queue):
        item = queue.add("https://www.youtube.com/watch?v=a")
        queue.mark_played(item.id)
        assert len(queue.get_pending()) == 0

    def test_remove(self, queue):
        item = queue.add("https://www.youtube.com/watch?v=a")
        assert queue.remove(item.id) is True
        assert len(queue.get_all()) == 0

    def test_remove_nonexistent(self, queue):
        assert queue.remove(999) is False

    def test_clear_played(self, queue):
        item1 = queue.add("https://www.youtube.com/watch?v=a")
        queue.add("https://www.youtube.com/watch?v=b")
        queue.mark_played(item1.id)
        queue.clear_played()
        items = queue.get_all()
        assert len(items) == 1
        assert items[0].url == "https://www.youtube.com/watch?v=b"

    def test_clear_all(self, queue):
        queue.add("https://www.youtube.com/watch?v=a")
        queue.add("https://www.youtube.com/watch?v=b")
        queue.clear_all()
        assert len(queue.get_all()) == 0

    def test_persistence(self, tmp_path):
        db_path = str(tmp_path / "persist.db")
        db1 = Database(db_path)
        q1 = QueueManager(db1)
        q1.add("https://www.youtube.com/watch?v=persist")
        db1.close()
        # Create a new Database+QueueManager pointing to the same file
        db2 = Database(db_path)
        q2 = QueueManager(db2)
        items = q2.get_all()
        assert len(items) == 1
        assert items[0].url == "https://www.youtube.com/watch?v=persist"

    def test_source_detection_youtube(self, queue):
        item = queue.add("https://www.youtube.com/watch?v=abc")
        assert item.source_type == "youtube"

    def test_source_detection_youtu_be(self, queue):
        item = queue.add("https://youtu.be/abc")
        assert item.source_type == "youtube"

    def test_source_detection_twitch(self, queue):
        item = queue.add("https://www.twitch.tv/somechannel")
        assert item.source_type == "twitch"

    def test_source_detection_local(self, queue):
        item = queue.add("/mnt/usb/movie.mp4")
        assert item.source_type == "local"

    def test_reorder(self, queue):
        item1 = queue.add("https://www.youtube.com/watch?v=a")
        item2 = queue.add("https://www.youtube.com/watch?v=b")
        item3 = queue.add("https://www.youtube.com/watch?v=c")
        queue.reorder([item3.id, item1.id, item2.id])
        pending = queue.get_pending()
        assert pending[0].id == item3.id
        assert pending[1].id == item1.id
        assert pending[2].id == item2.id

    def test_import_queue_txt(self, queue, tmp_path):
        queue_txt = tmp_path / "queue.txt"
        queue_txt.write_text(
            "# This is a comment\n"
            "https://www.youtube.com/watch?v=abc\n"
            "[PLAYED] https://www.youtube.com/watch?v=def\n"
            "\n"
            "https://www.youtube.com/watch?v=ghi\n"
        )
        count = queue.import_queue_txt(str(queue_txt))
        assert count == 3
        items = queue.get_all()
        assert len(items) == 3
        # The [PLAYED] one should be marked as played
        played = [i for i in items if i.status == "played"]
        assert len(played) == 1
        assert "def" in played[0].url

    def test_reset_stale_playing(self, queue):
        item = queue.add("https://www.youtube.com/watch?v=a")
        queue.mark_playing(item.id)
        count = queue.reset_stale_playing()
        assert count == 1
        assert queue.get_current() is None
        assert queue.get_next().id == item.id

    def test_mark_skipped(self, queue):
        item = queue.add("https://www.youtube.com/watch?v=a")
        queue.mark_skipped(item.id)
        items = queue.get_all()
        assert items[0].status == "skipped"
        assert len(queue.get_pending()) == 0

    def test_replay_marks_pending(self, queue):
        item = queue.add("https://www.youtube.com/watch?v=a")
        queue.mark_played(item.id)
        assert queue.replay(item.id) is True
        items = queue.get_all()
        assert items[0].status == "pending"

    def test_replay_moves_to_end(self, queue):
        """Replayed items go to the end of the pending queue."""
        item1 = queue.add("https://www.youtube.com/watch?v=a")
        item2 = queue.add("https://www.youtube.com/watch?v=b")
        item3 = queue.add("https://www.youtube.com/watch?v=c")
        queue.mark_played(item1.id)
        queue.replay(item1.id)
        pending = queue.get_pending()
        # item1 should now be LAST, after item2 and item3
        assert pending[0].id == item2.id
        assert pending[1].id == item3.id
        assert pending[2].id == item1.id

    def test_replay_nonexistent(self, queue):
        assert queue.replay(999) is False


class TestErrorTracking:
    """Tests for error tracking methods."""

    def test_increment_error(self, queue):
        item = queue.add("https://www.youtube.com/watch?v=a")
        count = queue.increment_error(item.id, "mpv exited with code 2")
        assert count == 1
        # Check error stored
        items = queue.get_all()
        assert items[0].error_count == 1
        assert items[0].last_error == "mpv exited with code 2"

    def test_increment_error_multiple(self, queue):
        item = queue.add("https://www.youtube.com/watch?v=a")
        queue.increment_error(item.id, "error 1")
        queue.increment_error(item.id, "error 2")
        count = queue.increment_error(item.id, "error 3")
        assert count == 3
        items = queue.get_all()
        assert items[0].last_error == "error 3"

    def test_mark_failed(self, queue):
        item = queue.add("https://www.youtube.com/watch?v=a")
        assert queue.mark_failed(item.id) is True
        items = queue.get_all()
        assert items[0].status == "failed"
        assert items[0].failed_at is not None

    def test_mark_failed_nonexistent(self, queue):
        assert queue.mark_failed(999) is False

    def test_get_failed(self, queue):
        item1 = queue.add("https://www.youtube.com/watch?v=a")
        item2 = queue.add("https://www.youtube.com/watch?v=b")
        queue.add("https://www.youtube.com/watch?v=c")
        queue.mark_failed(item1.id)
        queue.mark_failed(item2.id)
        failed = queue.get_failed()
        assert len(failed) == 2
        assert all(f.status == "failed" for f in failed)

    def test_get_failed_empty(self, queue):
        queue.add("https://www.youtube.com/watch?v=a")
        assert queue.get_failed() == []

    def test_retry_failed(self, queue):
        item = queue.add("https://www.youtube.com/watch?v=a")
        queue.increment_error(item.id, "some error")
        queue.increment_error(item.id, "another error")
        queue.mark_failed(item.id)
        assert queue.retry_failed(item.id) is True
        # Verify reset
        items = queue.get_all()
        assert items[0].status == "pending"
        assert items[0].error_count == 0
        assert items[0].last_error == ""
        assert items[0].failed_at is None

    def test_retry_failed_only_works_on_failed(self, queue):
        """retry_failed should only affect items with status='failed'."""
        item = queue.add("https://www.youtube.com/watch?v=a")
        # Item is 'pending', not 'failed'
        assert queue.retry_failed(item.id) is False

    def test_retry_failed_nonexistent(self, queue):
        assert queue.retry_failed(999) is False

    def test_clear_failed(self, queue):
        item1 = queue.add("https://www.youtube.com/watch?v=a")
        item2 = queue.add("https://www.youtube.com/watch?v=b")
        item3 = queue.add("https://www.youtube.com/watch?v=c")
        queue.mark_failed(item1.id)
        queue.mark_failed(item2.id)
        queue.clear_failed()
        items = queue.get_all()
        assert len(items) == 1
        assert items[0].id == item3.id

    def test_clear_failed_empty(self, queue):
        """clear_failed on no failed items is a no-op."""
        queue.add("https://www.youtube.com/watch?v=a")
        queue.clear_failed()
        assert len(queue.get_all()) == 1

    def test_failed_items_not_in_pending(self, queue):
        """Failed items should not appear in get_pending()."""
        item = queue.add("https://www.youtube.com/watch?v=a")
        queue.mark_failed(item.id)
        assert queue.get_pending() == []
        assert queue.get_next() is None

    def test_error_fields_in_to_dict(self, queue):
        """QueueItem.to_dict() includes error tracking fields."""
        item = queue.add("https://www.youtube.com/watch?v=a")
        queue.increment_error(item.id, "test error")
        items = queue.get_all()
        d = items[0].to_dict()
        assert "error_count" in d
        assert "last_error" in d
        assert "failed_at" in d
        assert d["error_count"] == 1
        assert d["last_error"] == "test error"
