"""Tests for fallback screensaver feature."""

import sqlite3
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from picast.config import ServerConfig, _parse_config
from picast.server.mpv_client import MPVClient
from picast.server.player import Player
from picast.server.queue_manager import QueueManager


class TestFallbackConfig:
    """Test config parsing for fallback fields."""

    def test_default_fallback_fields(self):
        config = ServerConfig()
        assert config.fallback_url == ""
        assert config.fallback_title == "Screensaver"

    def test_parse_fallback_url(self):
        data = {"server": {"fallback_url": "https://www.youtube.com/watch?v=b7TGGIqezsQ"}}
        config = _parse_config(data)
        assert config.server.fallback_url == "https://www.youtube.com/watch?v=b7TGGIqezsQ"

    def test_parse_fallback_title(self):
        data = {"server": {
            "fallback_url": "https://example.com/video",
            "fallback_title": "Live Cam",
        }}
        config = _parse_config(data)
        assert config.server.fallback_title == "Live Cam"

    def test_parse_fallback_defaults_when_absent(self):
        data = {"server": {"port": 5050}}
        config = _parse_config(data)
        assert config.server.fallback_url == ""
        assert config.server.fallback_title == "Screensaver"


class TestPlayerFallbackInit:
    """Test Player fallback initialization."""

    def test_fallback_params_stored(self):
        mpv = MagicMock(spec=MPVClient)
        queue = MagicMock(spec=QueueManager)
        player = Player(
            mpv, queue,
            fallback_url="https://example.com/video",
            fallback_title="Test Screensaver",
        )
        assert player._fallback_url == "https://example.com/video"
        assert player._fallback_title == "Test Screensaver"
        assert player._fallback_active is False

    def test_fallback_defaults(self):
        mpv = MagicMock(spec=MPVClient)
        queue = MagicMock(spec=QueueManager)
        player = Player(mpv, queue)
        assert player._fallback_url == ""
        assert player._fallback_title == "Screensaver"


class TestPlayerFallbackStatus:
    """Test get_status includes fallback_active."""

    def test_status_includes_fallback_active(self):
        mpv = MagicMock(spec=MPVClient)
        mpv.get_status.return_value = {"idle": True, "title": ""}
        queue = MagicMock(spec=QueueManager)
        player = Player(mpv, queue)
        player._running = True

        status = player.get_status()
        assert "fallback_active" in status
        assert status["fallback_active"] is False

    def test_status_fallback_active_true(self):
        mpv = MagicMock(spec=MPVClient)
        mpv.get_status.return_value = {"idle": False, "title": ""}
        queue = MagicMock(spec=QueueManager)
        player = Player(mpv, queue)
        player._running = True
        player._fallback_active = True

        status = player.get_status()
        assert status["fallback_active"] is True


class TestPlayerFallbackLoop:
    """Test _loop() fallback activation logic."""

    @patch.object(Player, "_play_fallback")
    def test_loop_calls_fallback_when_queue_empty_and_url_set(self, mock_fallback):
        """When queue is empty and fallback_url is set, _play_fallback is called."""
        mpv = MagicMock(spec=MPVClient)
        queue = MagicMock(spec=QueueManager)
        queue.get_next.return_value = None
        queue.has_loopable.return_value = False

        player = Player(mpv, queue, fallback_url="https://example.com/vid")
        player._running = True

        # _play_fallback will stop the loop
        call_count = 0
        def stop_after_call():
            nonlocal call_count
            call_count += 1
            player._running = False

        mock_fallback.side_effect = stop_after_call
        player._loop()

        assert call_count == 1
        mock_fallback.assert_called_once()

    @patch("time.sleep")
    def test_loop_sleeps_when_no_fallback_url(self, mock_sleep):
        """When queue is empty and no fallback_url, just sleep."""
        mpv = MagicMock(spec=MPVClient)
        queue = MagicMock(spec=QueueManager)
        queue.get_next.return_value = None
        queue.has_loopable.return_value = False

        player = Player(mpv, queue, fallback_url="")
        player._running = True

        call_count = 0
        original_sleep = time.sleep
        def stop_after_sleep(secs):
            nonlocal call_count
            call_count += 1
            if call_count >= 1:
                player._running = False

        mock_sleep.side_effect = stop_after_sleep
        player._loop()

        # Should have called time.sleep, not _play_fallback
        assert mock_sleep.called

    @patch.object(Player, "_play_fallback")
    def test_loop_no_fallback_when_stop_requested(self, mock_fallback):
        """When stop is requested, don't start fallback."""
        mpv = MagicMock(spec=MPVClient)
        queue = MagicMock(spec=QueueManager)
        queue.get_next.return_value = None

        player = Player(mpv, queue, fallback_url="https://example.com/vid")
        player._running = True
        player._stop_requested = True

        # The stop_requested check happens before queue check in _loop
        # Run one iteration
        call_count = 0
        original_sleep = time.sleep
        @patch("time.sleep")
        def run_test(mock_sleep):
            def stop_loop(secs):
                nonlocal call_count
                call_count += 1
                player._running = False
            mock_sleep.side_effect = stop_loop
            player._loop()

        run_test()
        mock_fallback.assert_not_called()


class TestPlayerPlayNowInterruptsFallback:
    """Test play_now() interrupts fallback."""

    def test_play_now_sets_skip_when_fallback_active(self):
        mpv = MagicMock(spec=MPVClient)
        queue = MagicMock(spec=QueueManager)
        mock_item = MagicMock()
        mock_item.id = 1
        queue.add.return_value = mock_item
        queue.get_pending.return_value = []

        player = Player(mpv, queue, fallback_url="https://example.com/vid")
        player._running = True
        player._fallback_active = True
        player._current_item = None  # No regular item playing

        player.play_now("https://example.com/new-video", "Test")

        assert player._skip_requested is True

    def test_play_now_skips_regular_item_not_fallback(self):
        """When a regular item is playing, skip() is called instead."""
        mpv = MagicMock(spec=MPVClient)
        mpv.connected = True
        queue = MagicMock(spec=QueueManager)
        mock_item = MagicMock()
        mock_item.id = 1
        queue.add.return_value = mock_item
        queue.get_pending.return_value = []

        player = Player(mpv, queue)
        player._running = True
        player._current_item = MagicMock()  # Regular item playing
        player._fallback_active = False

        player.play_now("https://example.com/new-video", "Test")

        # skip() should have been called (via _current_item path)
        # This sets _skip_requested = True and calls mpv quit
        assert player._skip_requested is True


class TestFallbackApiStatus:
    """Test fallback_active appears in API status response."""

    def test_status_endpoint_includes_fallback(self, client):
        resp = client.get("/api/status")
        data = resp.get_json()
        assert "fallback_active" in data
        assert data["fallback_active"] is False

    @pytest.fixture
    def client(self, tmp_path):
        config = ServerConfig(
            mpv_socket="/tmp/picast-test-fb-socket",
            db_file=str(tmp_path / "test.db"),
            data_dir=str(tmp_path / "data"),
            fallback_url="https://example.com/screensaver",
        )
        app = __import__("picast.server.app", fromlist=["create_app"]).create_app(config)
        app.player.stop()
        app.config["TESTING"] = True
        return app.test_client()


class TestPlayerLoopCrashProtection:
    """Test that the player loop survives exceptions."""

    @patch("picast.server.player.time.sleep")
    def test_player_loop_survives_db_error(self, mock_sleep):
        """Player loop should catch DB errors and keep running."""
        mpv = MagicMock(spec=MPVClient)
        queue = MagicMock(spec=QueueManager)

        # get_next raises DatabaseError on first call, returns None on second
        call_count = 0
        def get_next_side_effect():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise sqlite3.DatabaseError("database disk image is malformed")
            # Stop the loop on second call
            player._running = False
            return None

        queue.get_next.side_effect = get_next_side_effect
        queue.has_loopable.return_value = False

        player = Player(mpv, queue, fallback_url="")
        player._running = True
        player._loop()

        # Loop should have survived the first error and made a second call
        assert call_count == 2
        # sleep should have been called for backoff after the error
        assert mock_sleep.called

    @patch("picast.server.player.time.sleep")
    def test_player_loop_exponential_backoff(self, mock_sleep):
        """Consecutive errors should increase backoff time."""
        mpv = MagicMock(spec=MPVClient)
        queue = MagicMock(spec=QueueManager)

        call_count = 0
        def get_next_errors():
            nonlocal call_count
            call_count += 1
            if call_count <= 3:
                raise RuntimeError("test error")
            player._running = False
            return None

        queue.get_next.side_effect = get_next_errors
        queue.has_loopable.return_value = False

        player = Player(mpv, queue, fallback_url="")
        player.event_bus = MagicMock()
        player._running = True
        player._loop()

        # Error backoff delays: 2^1=2, 2^2=4, 2^3=8 (then final sleep(2) from normal path)
        # Filter to only error-backoff sleeps (>= 2 and strictly increasing)
        all_sleep_vals = [call.args[0] for call in mock_sleep.call_args_list if call.args]
        # First 3 should be backoff: 2, 4, 8
        backoff_sleeps = all_sleep_vals[:3]
        assert backoff_sleeps == [2, 4, 8]


class TestFallbackBackoff:
    """Test fallback screensaver failure backoff."""

    def test_fallback_consecutive_failures_init(self):
        """Player should initialize fallback failure counter."""
        mpv = MagicMock(spec=MPVClient)
        queue = MagicMock(spec=QueueManager)
        player = Player(mpv, queue)
        assert player._fallback_consecutive_failures == 0

    @patch("picast.server.player.detect_hdmi_audio", return_value=None)
    @patch("picast.server.player.detect_wayland", return_value=None)
    @patch("picast.server.player.time.sleep")
    @patch("picast.server.player.subprocess.Popen")
    def test_fallback_backoff_on_rapid_exit(self, mock_popen, mock_sleep,
                                            mock_wayland, mock_hdmi):
        """If fallback exits quickly, should back off before retrying."""
        mpv = MagicMock(spec=MPVClient)
        mpv.socket_path = "/tmp/test-socket"
        mpv.connect.return_value = False
        queue = MagicMock(spec=QueueManager)
        queue._db = MagicMock()

        player = Player(
            mpv, queue,
            fallback_url="https://example.com/vid",
            fallback_title="Test",
        )
        player._running = True
        player._config = None

        # Mock mpv process that exits immediately (simulates stream failure)
        mock_proc = MagicMock()
        mock_proc.poll.return_value = 1  # Already exited
        mock_popen.return_value = mock_proc

        # Mock the wakeup event to track wait() calls
        player._wakeup = MagicMock()
        player._wakeup.wait.return_value = False  # Simulate timeout (not woken)

        # Run fallback — it should detect rapid exit and backoff
        player._play_fallback()

        assert player._fallback_consecutive_failures == 1
        # The wakeup.wait should have been called with backoff timeout (5s for first failure)
        player._wakeup.wait.assert_called_once()
        wait_timeout = player._wakeup.wait.call_args.kwargs.get(
            "timeout", player._wakeup.wait.call_args.args[0] if player._wakeup.wait.call_args.args else None
        )
        assert wait_timeout == 5

    @patch("picast.server.player.detect_hdmi_audio", return_value=None)
    @patch("picast.server.player.detect_wayland", return_value=None)
    @patch("picast.server.player.time.sleep")
    @patch("picast.server.player.subprocess.Popen")
    def test_fallback_resets_on_successful_play(self, mock_popen, mock_sleep,
                                                mock_wayland, mock_hdmi):
        """A successful long play should reset the failure counter."""
        mpv = MagicMock(spec=MPVClient)
        mpv.socket_path = "/tmp/test-socket"
        mpv.connect.return_value = False
        queue = MagicMock(spec=QueueManager)
        queue._db = MagicMock()

        player = Player(
            mpv, queue,
            fallback_url="https://example.com/vid",
            fallback_title="Test",
        )
        player._running = True
        player._config = None
        player._fallback_consecutive_failures = 3  # Pre-set failures

        # Mock mpv process that runs for a while then gets skip-interrupted
        mock_proc = MagicMock()
        # poll returns None first (running), then exits
        poll_count = 0
        def poll_side_effect():
            nonlocal poll_count
            poll_count += 1
            if poll_count <= 1:
                return None
            return 0

        mock_proc.poll.side_effect = poll_side_effect
        mock_popen.return_value = mock_proc

        # Simulate skip after "long" play by mocking time.monotonic
        # to make play_duration > 15s
        real_monotonic = time.monotonic
        call_idx = 0
        start = real_monotonic()
        def fake_monotonic():
            nonlocal call_idx
            call_idx += 1
            # First call (start) returns real time
            # Subsequent calls return start + 20s to simulate long play
            if call_idx <= 1:
                return start
            return start + 20

        with patch("picast.server.player.time.monotonic", side_effect=fake_monotonic):
            player._play_fallback()

        # Failures should be reset after successful play (>15s)
        assert player._fallback_consecutive_failures == 0
        assert player._fallback_disabled_until is None


class TestCircuitBreaker:
    """Test fallback circuit breaker behavior."""

    def test_circuit_breaker_trips_after_max_failures(self):
        """After FALLBACK_MAX_FAILURES rapid exits, circuit breaker disables fallback."""
        from picast.server.player import FALLBACK_MAX_FAILURES

        mpv = MagicMock(spec=MPVClient)
        mpv.socket_path = "/tmp/test-socket"
        mpv.connect.return_value = False
        queue = MagicMock(spec=QueueManager)
        queue._db = MagicMock()

        player = Player(
            mpv, queue,
            fallback_url="https://example.com/vid",
            fallback_title="Test",
        )
        player._running = True
        player._config = None
        # Set failures to one below threshold
        player._fallback_consecutive_failures = FALLBACK_MAX_FAILURES - 1

        # Mock mpv process that exits immediately
        mock_proc = MagicMock()
        mock_proc.poll.return_value = 1
        with patch("picast.server.player.subprocess.Popen", return_value=mock_proc), \
             patch("picast.server.player.detect_hdmi_audio", return_value=None), \
             patch("picast.server.player.detect_wayland", return_value=None), \
             patch("picast.server.player.time.sleep"):
            player._play_fallback()

        assert player._fallback_consecutive_failures == FALLBACK_MAX_FAILURES
        assert player._fallback_disabled_until is not None

    def test_circuit_breaker_resets_on_successful_play(self):
        """Successful play (>15s) clears circuit breaker."""
        mpv = MagicMock(spec=MPVClient)
        mpv.socket_path = "/tmp/test-socket"
        mpv.connect.return_value = False
        queue = MagicMock(spec=QueueManager)
        queue._db = MagicMock()

        player = Player(
            mpv, queue,
            fallback_url="https://example.com/vid",
            fallback_title="Test",
        )
        player._running = True
        player._config = None
        player._fallback_consecutive_failures = 5
        player._fallback_disabled_until = time.monotonic() + 100  # Pre-set

        # Mock mpv that runs "long enough"
        mock_proc = MagicMock()
        poll_count = 0
        def poll_side():
            nonlocal poll_count
            poll_count += 1
            return None if poll_count <= 1 else 0
        mock_proc.poll.side_effect = poll_side

        real_mono = time.monotonic
        call_idx = 0
        start = real_mono()
        def fake_mono():
            nonlocal call_idx
            call_idx += 1
            return start if call_idx <= 1 else start + 20

        with patch("picast.server.player.subprocess.Popen", return_value=mock_proc), \
             patch("picast.server.player.detect_hdmi_audio", return_value=None), \
             patch("picast.server.player.detect_wayland", return_value=None), \
             patch("picast.server.player.time.sleep"), \
             patch("picast.server.player.time.monotonic", side_effect=fake_mono):
            player._play_fallback()

        assert player._fallback_consecutive_failures == 0
        assert player._fallback_disabled_until is None

    def test_status_includes_fallback_disabled(self):
        """get_status exposes fallback_disabled field."""
        mpv = MagicMock(spec=MPVClient)
        mpv.get_status.return_value = {"idle": True, "title": ""}
        queue = MagicMock(spec=QueueManager)
        player = Player(mpv, queue)
        player._running = True

        # Not disabled
        status = player.get_status()
        assert status["fallback_disabled"] is False

        # Disabled
        player._fallback_disabled_until = time.monotonic() + 100
        status = player.get_status()
        assert status["fallback_disabled"] is True

    @patch.object(Player, "_play_fallback")
    @patch("time.sleep")
    def test_loop_skips_fallback_during_cooldown(self, mock_sleep, mock_fallback):
        """When circuit breaker is active, _loop should skip fallback."""
        mpv = MagicMock(spec=MPVClient)
        queue = MagicMock(spec=QueueManager)
        queue.get_next.return_value = None
        queue.has_loopable.return_value = False

        player = Player(mpv, queue, fallback_url="https://example.com/vid")
        player._running = True
        # Set circuit breaker to future time
        player._fallback_disabled_until = time.monotonic() + 9999

        call_count = 0
        def stop_after_sleep(secs):
            nonlocal call_count
            call_count += 1
            if call_count >= 1:
                player._running = False

        mock_sleep.side_effect = stop_after_sleep
        player._loop()

        # _play_fallback should NOT have been called
        mock_fallback.assert_not_called()
        # sleep(2) should have been called (cooldown skip path)
        assert mock_sleep.called


class TestWakeupEvent:
    """Test _wakeup threading.Event for interruptible backoff."""

    def test_play_now_sets_wakeup_event(self):
        """play_now() should set _wakeup to interrupt any backoff sleep."""
        mpv = MagicMock(spec=MPVClient)
        queue = MagicMock(spec=QueueManager)
        mock_item = MagicMock()
        mock_item.id = 1
        queue.add.return_value = mock_item
        queue.get_pending.return_value = []

        player = Player(mpv, queue, fallback_url="https://example.com/vid")
        player._running = True

        player.play_now("https://example.com/new-video", "Test")
        assert player._wakeup.is_set()

    def test_wakeup_interrupts_backoff_sleep(self):
        """_wakeup.set() should cause backoff wait to return early."""
        mpv = MagicMock(spec=MPVClient)
        mpv.socket_path = "/tmp/test-socket"
        mpv.connect.return_value = False
        queue = MagicMock(spec=QueueManager)
        queue._db = MagicMock()

        player = Player(
            mpv, queue,
            fallback_url="https://example.com/vid",
            fallback_title="Test",
        )
        player._running = True
        player._config = None

        # Mock mpv process that exits immediately (triggers backoff)
        mock_proc = MagicMock()
        mock_proc.poll.return_value = 1

        finished = threading.Event()

        def run_fallback():
            player._play_fallback()
            finished.set()

        with patch("picast.server.player.subprocess.Popen", return_value=mock_proc), \
             patch("picast.server.player.detect_hdmi_audio", return_value=None), \
             patch("picast.server.player.detect_wayland", return_value=None), \
             patch("picast.server.player.time.sleep"):  # Speed up mpv connect loop
            t = threading.Thread(target=run_fallback)
            t.start()
            # Give it a moment to enter the backoff wait
            time.sleep(0.3)
            # Wake it up — should interrupt the _wakeup.wait(timeout=5)
            player._wakeup.set()
            # Should finish quickly (not wait full 5s backoff)
            assert finished.wait(timeout=2), "_play_fallback did not return after wakeup"
            t.join(timeout=2)
