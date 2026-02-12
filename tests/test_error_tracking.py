"""Tests for the error tracking flow in the player loop."""

from unittest.mock import patch

from picast.server.events import EventBus
from picast.server.mpv_client import MPVClient
from picast.server.player import FAILURE_BACKOFF, MAX_RAPID_FAILURES, Player
from picast.server.queue_manager import QueueItem, QueueManager


class TestClassifyError:
    """Test error classification logic."""

    def _make_player(self, db):
        mpv = MPVClient("/tmp/nonexistent-test-socket")
        queue = QueueManager(db)
        event_bus = EventBus(db)
        player = Player(mpv, queue, event_bus=event_bus)
        return player

    def _make_item(self, **kwargs):
        defaults = dict(
            id=1, url="https://youtube.com/watch?v=test",
            title="Test", source_type="youtube",
        )
        defaults.update(kwargs)
        return QueueItem(**defaults)

    def test_classify_known_exit_code_2(self, db):
        player = self._make_player(db)
        result = player._classify_error(2, self._make_item())
        assert "unavailable" in result

    def test_classify_known_exit_code_3(self, db):
        player = self._make_player(db)
        result = player._classify_error(3, self._make_item())
        assert "network" in result

    def test_classify_known_exit_code_4(self, db):
        player = self._make_player(db)
        result = player._classify_error(4, self._make_item())
        assert "codec" in result or "format" in result

    def test_classify_exit_0_rapid(self, db):
        player = self._make_player(db)
        result = player._classify_error(0, self._make_item())
        assert "too quickly" in result or "yt-dlp" in result

    def test_classify_unknown_exit_code(self, db):
        player = self._make_player(db)
        result = player._classify_error(42, self._make_item())
        assert "42" in result

    def test_classify_403_from_log(self, db, tmp_path):
        player = self._make_player(db)
        # Create a fake mpv log with 403 error
        with open("/tmp/mpv-debug.log", "w") as f:
            f.write("[error] HTTP error 403 Forbidden\n")
        result = player._classify_error(1, self._make_item())
        assert "403" in result

    def test_classify_unable_to_extract(self, db):
        player = self._make_player(db)
        with open("/tmp/mpv-debug.log", "w") as f:
            f.write("[error] Unable to extract video data\n")
        result = player._classify_error(1, self._make_item())
        assert "unable to extract" in result

    def test_classify_timeout(self, db):
        player = self._make_player(db)
        with open("/tmp/mpv-debug.log", "w") as f:
            f.write("[error] Connection timed out\n")
        result = player._classify_error(1, self._make_item())
        assert "timeout" in result.lower()

    def test_classify_missing_log_file(self, db):
        player = self._make_player(db)
        # Remove the log file if it exists
        import os
        try:
            os.remove("/tmp/mpv-debug.log")
        except OSError:
            pass
        result = player._classify_error(99, self._make_item())
        assert "99" in result


class TestCascadeWithErrorTracking:
    """Test _check_cascade with error tracking integration."""

    def _make_player(self, db):
        mpv = MPVClient("/tmp/nonexistent-test-socket")
        queue = QueueManager(db)
        event_bus = EventBus(db)
        player = Player(mpv, queue, event_bus=event_bus)
        return player, queue, event_bus

    def _make_item(self, queue):
        return queue.add("https://youtube.com/watch?v=test")

    @patch("picast.server.player.time.sleep")
    def test_first_failure_increments_error(self, mock_sleep, db):
        player, queue, bus = self._make_player(db)
        item = self._make_item(queue)
        queue.mark_playing(item.id)

        result = player._check_cascade(1, 2.0, item)
        assert result == "retry"

        # Verify error was tracked
        items = queue.get_all()
        assert items[0].error_count == 1
        assert items[0].last_error != ""

    @patch("picast.server.player.time.sleep")
    def test_max_failures_marks_failed(self, mock_sleep, db):
        player, queue, bus = self._make_player(db)
        item = self._make_item(queue)
        queue.mark_playing(item.id)

        # Simulate 3 rapid failures
        for i in range(MAX_RAPID_FAILURES):
            result = player._check_cascade(1, 2.0, item)

        assert result == "failed"

        # Verify item is marked failed
        failed = queue.get_failed()
        assert len(failed) == 1
        assert failed[0].id == item.id
        assert failed[0].error_count == MAX_RAPID_FAILURES

    @patch("picast.server.player.time.sleep")
    def test_failure_emits_error_event(self, mock_sleep, db):
        player, queue, bus = self._make_player(db)
        item = self._make_item(queue)
        queue.mark_playing(item.id)

        sub_q = bus.subscribe()
        player._check_cascade(1, 2.0, item)

        event = sub_q.get(timeout=1)
        assert event["type"] == "error"
        assert "Retrying" in event["title"]
        bus.unsubscribe(sub_q)

    @patch("picast.server.player.time.sleep")
    def test_max_failure_emits_failed_event(self, mock_sleep, db):
        player, queue, bus = self._make_player(db)
        item = self._make_item(queue)
        queue.mark_playing(item.id)

        sub_q = bus.subscribe()

        for _ in range(MAX_RAPID_FAILURES):
            player._check_cascade(1, 2.0, item)

        # Collect all events
        events = []
        while not sub_q.empty():
            events.append(sub_q.get_nowait())

        # Last event should be "failed"
        assert events[-1]["type"] == "failed"
        assert "Failed" in events[-1]["title"]
        bus.unsubscribe(sub_q)

    @patch("picast.server.player.time.sleep")
    def test_exponential_backoff(self, mock_sleep, db):
        """Verify backoff delays increase with each retry."""
        player, queue, bus = self._make_player(db)
        item = self._make_item(queue)
        queue.mark_playing(item.id)

        # First failure
        player._check_cascade(1, 2.0, item)
        mock_sleep.assert_called_with(FAILURE_BACKOFF[0])

        # Second failure
        player._check_cascade(1, 2.0, item)
        mock_sleep.assert_called_with(FAILURE_BACKOFF[1])

    @patch("picast.server.player.time.sleep")
    def test_rapid_exit_tracks_errors(self, mock_sleep, db):
        """exit=0 with <5s also tracks errors."""
        player, queue, bus = self._make_player(db)
        item = self._make_item(queue)
        queue.mark_playing(item.id)

        result = player._check_cascade(0, 2.0, item)
        assert result == "retry"

        items = queue.get_all()
        assert items[0].error_count == 1

    @patch("picast.server.player.time.sleep")
    def test_rapid_exit_max_marks_failed(self, mock_sleep, db):
        """3 rapid exits (exit=0, <5s) marks as failed."""
        player, queue, bus = self._make_player(db)
        item = self._make_item(queue)
        queue.mark_playing(item.id)

        for _ in range(MAX_RAPID_FAILURES):
            result = player._check_cascade(0, 2.0, item)

        assert result == "failed"
        assert len(queue.get_failed()) == 1

    def test_normal_play_resets_counters(self, db):
        player, queue, bus = self._make_player(db)
        item = self._make_item(queue)
        queue.mark_playing(item.id)

        result = player._check_cascade(0, 60.0, item)
        assert result == "ok"

    def test_user_stop_resets_counters(self, db):
        player, queue, bus = self._make_player(db)
        item = self._make_item(queue)
        queue.mark_playing(item.id)
        player._stop_requested = True

        result = player._check_cascade(1, 2.0, item)
        assert result == "ok"

    def test_user_skip_resets_counters(self, db):
        player, queue, bus = self._make_player(db)
        item = self._make_item(queue)
        queue.mark_playing(item.id)
        player._skip_requested = True

        result = player._check_cascade(1, 2.0, item)
        assert result == "ok"


class TestPlayerEmitAndOSD:
    """Test _emit and _show_osd helper methods."""

    def test_emit_without_event_bus(self, db):
        mpv = MPVClient("/tmp/nonexistent-test-socket")
        queue = QueueManager(db)
        player = Player(mpv, queue)  # No event_bus
        # Should not raise
        player._emit("test", "No bus")

    def test_emit_with_event_bus(self, db):
        mpv = MPVClient("/tmp/nonexistent-test-socket")
        queue = QueueManager(db)
        bus = EventBus(db)
        player = Player(mpv, queue, event_bus=bus)

        sub = bus.subscribe()
        player._emit("test", "With bus")
        event = sub.get(timeout=1)
        assert event["type"] == "test"
        bus.unsubscribe(sub)

    def test_show_osd_when_disconnected(self, db):
        mpv = MPVClient("/tmp/nonexistent-test-socket")
        queue = QueueManager(db)
        player = Player(mpv, queue)
        # Should not raise
        player._show_osd("Test text")
