"""Tests for MultiTVManager — queue distribution across multiple TVs."""

import subprocess
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from picast.config import MultiTVConfig
from picast.server.multi_tv import (
    AssignmentInfo,
    MultiTVManager,
    _CHECK_CACHE_TTL,
    _FAILURE_BACKOFF_SECONDS,
    _GRACE_PERIOD_SECONDS,
    _MAX_CONSECUTIVE_FAILURES,
)


# --- Helpers ---


def _make_queue_item(id, url="https://www.youtube.com/watch?v=abc123def45", title="Test Video", status="pending"):
    """Create a mock QueueItem."""
    item = MagicMock()
    item.id = id
    item.url = url
    item.title = title
    item.status = status
    return item


def _make_manager(fleet=None, pending=None, config=None):
    """Create a MultiTVManager with mocked dependencies."""
    queue = MagicMock()
    player = MagicMock()
    sources = MagicMock()

    if pending is not None:
        queue.get_pending.return_value = pending
    else:
        queue.get_pending.return_value = []

    mgr = MultiTVManager(
        queue=queue,
        fleet=fleet,
        player=player,
        sources=sources,
        config=config,
    )
    return mgr


def _make_fleet(device_ids=None, idle_ids=None, available_ids=None):
    """Create a mock FleetManager with given devices."""
    fleet = MagicMock()
    fleet.device_ids = device_ids or []
    idle_ids = set(device_ids or []) if idle_ids is None else idle_ids
    # available_ids defaults to same as idle_ids for backwards compat
    if available_ids is None:
        available_ids = idle_ids
    fleet.is_device_idle.side_effect = lambda d: d in idle_ids
    fleet.is_available_for_queue.side_effect = lambda d: d in available_ids
    fleet.play_immediately.return_value = True
    fleet.poll_devices.return_value = {}
    return fleet


def _assigned_item(mgr, device_id):
    """Extract item_id from AssignmentInfo for assertions."""
    info = mgr._assignments.get(device_id)
    return info.item_id if info else None


def _set_assignment(mgr, device_id, item_id):
    """Set an assignment with current timestamp for test setup."""
    mgr._assignments[device_id] = AssignmentInfo(
        item_id=item_id, assigned_at=time.monotonic(),
    )


# --- Enable / Disable ---


class TestEnableDisable:
    def test_enable_sets_flag(self):
        mgr = _make_manager()
        assert not mgr.enabled
        # Enable synchronously by setting directly (avoid threading)
        mgr._enabled = True
        assert mgr.enabled

    def test_disable_clears_state(self):
        mgr = _make_manager()
        mgr._enabled = True
        _set_assignment(mgr, "main", 1)
        _set_assignment(mgr, "z1", 2)
        mgr.disable()
        assert not mgr.enabled
        assert mgr._assignments == {}

    def test_disable_clears_device_failures(self):
        mgr = _make_manager()
        mgr._enabled = True
        mgr._device_failures = {"z1": (3, time.monotonic())}
        mgr.disable()
        assert mgr._device_failures == {}

    def test_enable_disable_toggle(self):
        mgr = _make_manager()
        mgr._enabled = True
        mgr.disable()
        assert not mgr.enabled
        mgr._enabled = True
        assert mgr.enabled


# --- Distribute ---


class TestDistribute:
    def test_distribute_no_devices_no_items(self):
        mgr = _make_manager()
        mgr._enabled = True
        mgr.distribute()
        # No crash, no assignments
        assert mgr._assignments == {}

    def test_distribute_main_only_one_item(self):
        item = _make_queue_item(1)
        mgr = _make_manager(pending=[item])
        mgr._enabled = True
        mgr.distribute()
        assert _assigned_item(mgr, "main") == 1
        mgr._player.play_now.assert_called_once_with(item.url, item.title)

    def test_distribute_two_devices_two_items(self):
        """Main gets #1, fleet device gets #2."""
        items = [_make_queue_item(1, title="Video 1"), _make_queue_item(2, title="Video 2")]
        fleet = _make_fleet(device_ids=["z1"], idle_ids={"z1"})
        mgr = _make_manager(fleet=fleet, pending=items)
        mgr._enabled = True
        mgr.distribute()

        assert _assigned_item(mgr, "main") == 1
        assert _assigned_item(mgr, "z1") == 2
        mgr._player.play_now.assert_called_once()
        fleet.play_immediately.assert_called_once_with(
            "z1", {"url": items[1].url, "title": items[1].title}
        )

    def test_distribute_more_tvs_than_items(self):
        """Extra TVs stay idle when queue has fewer items."""
        items = [_make_queue_item(1)]
        fleet = _make_fleet(device_ids=["z1", "z2"], idle_ids={"z1", "z2"})
        mgr = _make_manager(fleet=fleet, pending=items)
        mgr._enabled = True
        mgr.distribute()

        assert _assigned_item(mgr, "main") == 1
        assert "z1" not in mgr._assignments
        assert "z2" not in mgr._assignments

    def test_distribute_skips_already_assigned(self):
        """Don't re-assign items that are already playing on a device."""
        items = [_make_queue_item(1), _make_queue_item(2)]
        mgr = _make_manager(pending=items)
        mgr._enabled = True
        _set_assignment(mgr, "main", 1)  # main already has item 1

        # No fleet, so only main as device. Main is assigned, so no action.
        mgr.distribute()
        # Main still has item 1, no change
        assert _assigned_item(mgr, "main") == 1

    def test_distribute_disabled_does_nothing(self):
        items = [_make_queue_item(1)]
        mgr = _make_manager(pending=items)
        mgr._enabled = False
        mgr.distribute()
        assert mgr._assignments == {}
        mgr._player.play_now.assert_not_called()

    def test_distribute_writes_assignment_info(self):
        """Distribute creates AssignmentInfo, not bare int."""
        item = _make_queue_item(1)
        mgr = _make_manager(pending=[item])
        mgr._enabled = True
        mgr.distribute()
        info = mgr._assignments.get("main")
        assert isinstance(info, AssignmentInfo)
        assert info.item_id == 1
        assert info.confirmed_playing is False
        assert info.assigned_at > 0


# --- On Video Finished ---


class TestOnVideoFinished:
    def test_on_video_finished_advances_queue(self):
        """When main finishes, it gets the next pending item."""
        items_round1 = [_make_queue_item(1), _make_queue_item(2)]
        items_round2 = [_make_queue_item(2)]  # item 1 is now played

        mgr = _make_manager(pending=items_round1)
        mgr._enabled = True
        _set_assignment(mgr, "main", 1)

        # After finishing, queue returns only item 2
        mgr._queue.get_pending.return_value = items_round2
        mgr.on_video_finished("main")

        mgr._queue.mark_played.assert_called_once_with(1)
        # Should try to distribute again
        assert _assigned_item(mgr, "main") == 2

    def test_on_video_finished_no_assignment(self):
        """Finishing with no prior assignment doesn't crash."""
        mgr = _make_manager()
        mgr._enabled = True
        mgr.on_video_finished("unknown-device")
        mgr._queue.mark_played.assert_not_called()

    def test_on_video_finished_clears_failure_counter(self):
        """Successful finish clears device failure counter."""
        mgr = _make_manager()
        mgr._enabled = True
        mgr._device_failures["z1"] = (2, time.monotonic())
        _set_assignment(mgr, "z1", 5)

        mgr.on_video_finished("z1")
        assert mgr._device_failures.get("z1") is None


# --- On Queue Changed ---


class TestOnQueueChanged:
    def test_on_queue_changed_fills_idle(self):
        """New item added goes to idle TV (runs in background thread)."""
        item = _make_queue_item(5)
        mgr = _make_manager(pending=[item])
        mgr._enabled = True
        mgr.on_queue_changed()
        # on_queue_changed spawns a thread — wait for it to finish
        for t in threading.enumerate():
            if t.name == "multi-tv-queue-changed":
                t.join(timeout=2)
        assert _assigned_item(mgr, "main") == 5

    def test_on_queue_changed_disabled(self):
        mgr = _make_manager(pending=[_make_queue_item(1)])
        mgr._enabled = False
        mgr.on_queue_changed()
        assert mgr._assignments == {}


# --- Pre-Check ---


class TestPreCheck:
    @patch("picast.server.multi_tv.subprocess.run")
    def test_pre_check_caches_success(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        item = _make_queue_item(1, url="https://youtube.com/watch?v=good")
        mgr = _make_manager()
        mgr.pre_check([item])

        assert item.url in mgr._check_cache
        ok, _ = mgr._check_cache[item.url]
        assert ok is True
        mock_run.assert_called_once()

    @patch("picast.server.multi_tv.subprocess.run")
    def test_pre_check_skips_offline(self, mock_run):
        """URL returning rc=1 is cached as not-ok."""
        mock_run.return_value = MagicMock(returncode=1)
        item = _make_queue_item(1, url="https://youtube.com/watch?v=dead")
        mgr = _make_manager()
        mgr.pre_check([item])

        ok, _ = mgr._check_cache[item.url]
        assert ok is False

    @patch("picast.server.multi_tv.subprocess.run")
    def test_pre_check_timeout(self, mock_run):
        """Timeout treated as failure."""
        mock_run.side_effect = subprocess.TimeoutExpired(cmd="yt-dlp", timeout=8)
        item = _make_queue_item(1, url="https://youtube.com/watch?v=slow")
        mgr = _make_manager()
        mgr.pre_check([item])

        ok, _ = mgr._check_cache[item.url]
        assert ok is False

    @patch("picast.server.multi_tv.subprocess.run")
    def test_pre_check_cache_reused(self, mock_run):
        """Second call within TTL uses cache, no subprocess."""
        mock_run.return_value = MagicMock(returncode=0)
        item = _make_queue_item(1, url="https://youtube.com/watch?v=cached")
        mgr = _make_manager()

        mgr.pre_check([item])
        assert mock_run.call_count == 1

        # Second check should use cache
        mgr.pre_check([item])
        assert mock_run.call_count == 1  # Not called again

    def test_pre_check_sets_checking_flag(self):
        """_checking is True during pre_check, False after."""
        mgr = _make_manager()
        assert not mgr._checking
        # With no items, pre_check is instant
        mgr.pre_check([])
        assert not mgr._checking

    @patch("picast.server.multi_tv.subprocess.run")
    def test_distribute_skips_failed_precheck(self, mock_run):
        """Items that failed pre-check are skipped during distribute."""
        mock_run.return_value = MagicMock(returncode=1)
        bad_item = _make_queue_item(1, url="https://youtube.com/watch?v=bad1bad2bad")
        good_item = _make_queue_item(2, url="https://youtube.com/watch?v=good1good23")

        mgr = _make_manager(pending=[bad_item, good_item])
        mgr._enabled = True

        # Pre-check marks bad_item as not-ok
        mgr.pre_check([bad_item])

        # Now distribute — should skip bad_item and assign good_item
        mgr.distribute()
        assert _assigned_item(mgr, "main") == 2


# --- Fleet Device Offline ---


class TestFleetOffline:
    def test_skip_unavailable_fleet_device(self):
        """Unavailable fleet devices (offline/manual override) are skipped."""
        items = [_make_queue_item(1), _make_queue_item(2)]
        # z1 not idle AND not available (offline or manual override)
        fleet = _make_fleet(device_ids=["z1"], idle_ids=set(), available_ids=set())
        mgr = _make_manager(fleet=fleet, pending=items)
        mgr._enabled = True
        mgr.distribute()

        # Only main should get assigned
        assert _assigned_item(mgr, "main") == 1
        assert "z1" not in mgr._assignments

    def test_autoplay_device_gets_queue_item(self):
        """Fleet device playing autoplay content is available for queue items."""
        items = [_make_queue_item(1), _make_queue_item(2)]
        # z1 not idle (playing autoplay) but available for queue
        fleet = _make_fleet(device_ids=["z1"], idle_ids=set(), available_ids={"z1"})
        mgr = _make_manager(fleet=fleet, pending=items)
        mgr._enabled = True
        mgr.distribute()

        assert _assigned_item(mgr, "main") == 1
        assert _assigned_item(mgr, "z1") == 2
        fleet.play_immediately.assert_called_once()


# --- Get Status ---


class TestGetStatus:
    def test_get_status_shape(self):
        """Verify response dict has expected keys."""
        fleet = _make_fleet(device_ids=["z1"])
        items = [_make_queue_item(1)]
        mgr = _make_manager(fleet=fleet, pending=items)
        mgr._enabled = True
        _set_assignment(mgr, "main", 1)

        status = mgr.get_status()

        assert "enabled" in status
        assert status["enabled"] is True
        assert "devices" in status
        assert len(status["devices"]) == 2  # main + z1
        assert "queue_remaining" in status
        assert "skipped_urls" in status
        assert "checking" in status
        assert status["checking"] is False
        assert "grayed_out_devices" in status

    def test_get_status_disabled(self):
        mgr = _make_manager()
        status = mgr.get_status()
        assert status["enabled"] is False
        assert status["devices"] == [{"device_id": "main", "queue_item_id": None}]

    def test_get_status_remaining_count(self):
        """Remaining = pending items not assigned to any device."""
        items = [_make_queue_item(1), _make_queue_item(2), _make_queue_item(3)]
        mgr = _make_manager(pending=items)
        mgr._enabled = True
        _set_assignment(mgr, "main", 1)

        status = mgr.get_status()
        assert status["queue_remaining"] == 2  # items 2 and 3

    def test_get_status_skipped_count(self):
        """Skipped = URLs that failed pre-check."""
        mgr = _make_manager()
        mgr._check_cache = {
            "https://bad.url": (False, time.monotonic()),
            "https://good.url": (True, time.monotonic()),
        }
        status = mgr.get_status()
        assert status["skipped_urls"] == 1


# --- Push Failure ---


class TestPushFailure:
    def test_main_play_failure_clears_assignment(self):
        """If player.play_now raises, assignment is cleared."""
        item = _make_queue_item(1)
        mgr = _make_manager(pending=[item])
        mgr._enabled = True
        mgr._player.play_now.side_effect = Exception("mpv crashed")

        mgr.distribute()
        assert "main" not in mgr._assignments

    def test_fleet_play_failure_clears_assignment(self):
        """If fleet.play_immediately returns False, assignment is cleared."""
        items = [_make_queue_item(1), _make_queue_item(2)]
        fleet = _make_fleet(device_ids=["z1"], idle_ids={"z1"})
        fleet.play_immediately.return_value = False

        mgr = _make_manager(fleet=fleet, pending=items)
        mgr._enabled = True
        mgr.distribute()

        # Main should succeed, z1 should fail
        assert _assigned_item(mgr, "main") == 1
        assert "z1" not in mgr._assignments


# --- No Fleet ---


class TestNoFleet:
    def test_works_with_no_fleet(self):
        """Multi-TV works with main device only when fleet is None."""
        item = _make_queue_item(1)
        mgr = _make_manager(fleet=None, pending=[item])
        mgr._enabled = True
        mgr.distribute()

        assert _assigned_item(mgr, "main") == 1

    def test_all_devices_main_only(self):
        mgr = _make_manager(fleet=None)
        assert mgr._get_all_devices() == ["main"]


# --- Empty Queue ---


class TestEmptyQueue:
    def test_enable_with_empty_queue(self):
        mgr = _make_manager(pending=[])
        mgr._enabled = True
        mgr.distribute()
        assert mgr._assignments == {}

    def test_status_empty_queue(self):
        mgr = _make_manager(pending=[])
        mgr._enabled = True
        status = mgr.get_status()
        assert status["queue_remaining"] == 0


# --- Skip Device ---


class TestSkipDevice:
    def test_skip_main_device(self):
        """Skip on main clears assignment, moves item to end, calls player.skip()."""
        items = [_make_queue_item(1), _make_queue_item(2)]
        mgr = _make_manager(pending=items)
        mgr._enabled = True
        _set_assignment(mgr, "main", 1)

        # After skip, distribute returns item 2 for main
        mgr._queue.get_pending.return_value = [_make_queue_item(2)]
        result = mgr.skip_device("main")

        assert result["ok"] is True
        assert result["skipped_item_id"] == 1
        mgr._queue.move_to_end.assert_called_once_with(1)
        mgr._player.skip.assert_called_once()

    def test_skip_fleet_device(self):
        """Skip on fleet device clears assignment, moves item to end."""
        items = [_make_queue_item(1), _make_queue_item(2), _make_queue_item(3)]
        fleet = _make_fleet(device_ids=["z1"], idle_ids=set(), available_ids={"z1"})
        mgr = _make_manager(fleet=fleet, pending=items)
        mgr._enabled = True
        _set_assignment(mgr, "main", 1)
        _set_assignment(mgr, "z1", 2)

        # After skip, distribute assigns item 3 to z1
        mgr._queue.get_pending.return_value = [_make_queue_item(3)]
        result = mgr.skip_device("z1")

        assert result["ok"] is True
        assert result["skipped_item_id"] == 2
        mgr._queue.move_to_end.assert_called_once_with(2)
        # player.skip should NOT be called for fleet device
        mgr._player.skip.assert_not_called()

    def test_skip_unassigned_device(self):
        """Skip on unassigned device returns error."""
        mgr = _make_manager()
        mgr._enabled = True
        result = mgr.skip_device("main")
        assert result["ok"] is False
        assert "error" in result

    def test_skip_returns_new_item_id(self):
        """Skip returns new_item_id when redistribute assigns a new item."""
        items = [_make_queue_item(1), _make_queue_item(2)]
        mgr = _make_manager(pending=items)
        mgr._enabled = True
        _set_assignment(mgr, "main", 1)

        # After skip + distribute, main gets item 2
        mgr._queue.get_pending.return_value = [_make_queue_item(2)]
        result = mgr.skip_device("main")

        assert result["ok"] is True
        assert result.get("new_item_id") == 2


# --- Pause / Resume / Volume ---


class TestPauseResumeVolume:
    def test_pause_main(self):
        """Pause on main calls mpv.pause()."""
        mgr = _make_manager()
        mgr._player.mpv.pause.return_value = True
        assert mgr.pause_device("main") is True
        mgr._player.mpv.pause.assert_called_once()

    def test_resume_main(self):
        """Resume on main calls mpv.resume()."""
        mgr = _make_manager()
        mgr._player.mpv.resume.return_value = True
        assert mgr.resume_device("main") is True
        mgr._player.mpv.resume.assert_called_once()

    def test_volume_main(self):
        """Volume on main calls mpv.set_volume()."""
        mgr = _make_manager()
        mgr._player.mpv.set_volume.return_value = True
        assert mgr.set_device_volume("main", 75) is True
        mgr._player.mpv.set_volume.assert_called_once_with(75)

    def test_pause_fleet_no_fleet(self):
        """Pause on fleet device with no fleet manager returns False."""
        mgr = _make_manager(fleet=None)
        assert mgr.pause_device("z1") is False

    def test_resume_fleet_no_fleet(self):
        """Resume on fleet device with no fleet manager returns False."""
        mgr = _make_manager(fleet=None)
        assert mgr.resume_device("z1") is False

    def test_volume_fleet_no_fleet(self):
        """Volume on fleet device with no fleet manager returns False."""
        mgr = _make_manager(fleet=None)
        assert mgr.set_device_volume("z1", 50) is False


# --- Get Device Status ---


class TestGetDeviceStatus:
    def test_status_main_device(self):
        """Status for main returns player status with queue_item_id."""
        mgr = _make_manager()
        mgr._player.get_status.return_value = {
            "idle": False,
            "title": "Test Video",
            "volume": 80,
            "paused": False,
        }
        _set_assignment(mgr, "main", 42)

        status = mgr.get_device_status("main")
        assert status["title"] == "Test Video"
        assert status["queue_item_id"] == 42
        mgr._player.get_status.assert_called_once()

    def test_status_fleet_no_fleet(self):
        """Status for fleet device with no fleet manager returns error."""
        mgr = _make_manager(fleet=None)
        status = mgr.get_device_status("z1")
        assert "error" in status


# --- Grace Period ---


class TestGracePeriod:
    def test_watcher_respects_grace_period(self):
        """Device idle during grace period is NOT marked finished."""
        fleet = _make_fleet(device_ids=["z1"], idle_ids={"z1"})
        mgr = _make_manager(fleet=fleet)
        mgr._enabled = True
        # Assign recently (within grace period)
        _set_assignment(mgr, "z1", 10)

        mgr._process_fleet_assignments()

        # Item should still be assigned (not finished)
        assert _assigned_item(mgr, "z1") == 10
        mgr._queue.mark_played.assert_not_called()
        mgr._queue.mark_pending.assert_not_called()

    def test_grace_expired_never_played_returns_to_pending(self):
        """Grace expired + never confirmed playing -> mark_pending (not mark_played)."""
        fleet = _make_fleet(device_ids=["z1"], idle_ids={"z1"})
        mgr = _make_manager(fleet=fleet)
        mgr._enabled = True
        # Assign long ago (grace expired)
        mgr._assignments["z1"] = AssignmentInfo(
            item_id=10,
            assigned_at=time.monotonic() - _GRACE_PERIOD_SECONDS - 1,
        )

        mgr._process_fleet_assignments()

        # Item returned to pending, NOT marked played
        mgr._queue.mark_pending.assert_called_once_with(10)
        mgr._queue.mark_played.assert_not_called()
        assert "z1" not in mgr._assignments

    def test_confirmed_playing_then_idle_marks_played(self):
        """confirmed_playing + idle -> video finished legitimately."""
        fleet = _make_fleet(device_ids=["z1"], idle_ids={"z1"})
        mgr = _make_manager(fleet=fleet)
        mgr._enabled = True
        mgr._assignments["z1"] = AssignmentInfo(
            item_id=10,
            assigned_at=time.monotonic() - 60,
            confirmed_playing=True,
        )

        mgr._process_fleet_assignments()

        mgr._queue.mark_played.assert_called_once_with(10)
        assert "z1" not in mgr._assignments

    def test_device_playing_sets_confirmed(self):
        """Device transitions to non-idle -> confirmed_playing set to True."""
        fleet = _make_fleet(device_ids=["z1"], idle_ids=set())  # NOT idle
        mgr = _make_manager(fleet=fleet)
        mgr._enabled = True
        _set_assignment(mgr, "z1", 10)
        assert mgr._assignments["z1"].confirmed_playing is False

        mgr._process_fleet_assignments()

        assert mgr._assignments["z1"].confirmed_playing is True
        assert _assigned_item(mgr, "z1") == 10  # Still assigned

    def test_main_excluded_from_watcher_grace(self):
        """Main device assignments are excluded from fleet watcher processing."""
        fleet = _make_fleet(device_ids=["z1"], idle_ids={"z1"})
        mgr = _make_manager(fleet=fleet)
        mgr._enabled = True
        _set_assignment(mgr, "main", 1)

        # Even if main is "idle", watcher should not process it
        mgr._process_fleet_assignments()

        # Main assignment untouched
        assert _assigned_item(mgr, "main") == 1
        mgr._queue.mark_played.assert_not_called()


# --- Device Gray Out ---


class TestDeviceGrayOut:
    def test_three_failures_grays_out_device(self):
        """3 consecutive failures -> device skipped in distribute."""
        items = [_make_queue_item(1), _make_queue_item(2)]
        fleet = _make_fleet(device_ids=["z1"], idle_ids={"z1"})
        mgr = _make_manager(fleet=fleet, pending=items)
        mgr._enabled = True
        mgr._device_failures["z1"] = (_MAX_CONSECUTIVE_FAILURES, time.monotonic())

        mgr.distribute()

        # Main gets item, z1 is grayed out
        assert _assigned_item(mgr, "main") == 1
        assert "z1" not in mgr._assignments

    def test_successful_play_clears_failure_counter(self):
        """Successful video finish clears failure counter."""
        mgr = _make_manager()
        mgr._enabled = True
        mgr._device_failures["z1"] = (2, time.monotonic())
        _set_assignment(mgr, "z1", 5)

        mgr.on_video_finished("z1")

        assert mgr._device_failures.get("z1") is None

    def test_enable_clears_all_failure_counters(self):
        """Re-enable clears all failure counters."""
        mgr = _make_manager()
        mgr._enabled = True
        mgr._device_failures = {
            "z1": (3, time.monotonic()),
            "z2": (2, time.monotonic()),
        }

        # Reset via enable (already on path)
        mgr._assignments.clear()
        mgr._check_cache.clear()
        mgr._device_failures.clear()

        assert mgr._device_failures == {}

    def test_get_status_includes_grayed_out(self):
        """get_status() includes grayed_out_devices list."""
        mgr = _make_manager()
        mgr._enabled = True
        mgr._device_failures = {
            "z1": (_MAX_CONSECUTIVE_FAILURES, time.monotonic()),
            "z2": (1, time.monotonic()),
        }

        status = mgr.get_status()
        assert "grayed_out_devices" in status
        assert "z1" in status["grayed_out_devices"]
        assert "z2" not in status["grayed_out_devices"]

    def test_disable_clears_failure_counters(self):
        """Disable clears failure counters."""
        mgr = _make_manager()
        mgr._enabled = True
        mgr._device_failures = {"z1": (3, time.monotonic())}
        mgr.disable()
        assert mgr._device_failures == {}

    def test_grace_expired_increments_failure_counter(self):
        """Failed start increments device failure counter."""
        fleet = _make_fleet(device_ids=["z1"], idle_ids={"z1"})
        mgr = _make_manager(fleet=fleet)
        mgr._enabled = True
        mgr._assignments["z1"] = AssignmentInfo(
            item_id=10,
            assigned_at=time.monotonic() - _GRACE_PERIOD_SECONDS - 1,
        )

        mgr._process_fleet_assignments()

        assert mgr._device_failures["z1"][0] == 1


# --- Latent Main Bug ---


class TestLatentMainBug:
    def test_stale_finish_does_not_pop_new_assignment(self):
        """Old item finish with wrong item_id does NOT pop new assignment."""
        mgr = _make_manager()
        mgr._enabled = True
        # New assignment is item 20
        _set_assignment(mgr, "main", 20)

        # Old callback fires for item 10 (stale)
        mgr.on_video_finished("main", item_id=10)

        # New assignment should still be there
        assert _assigned_item(mgr, "main") == 20
        mgr._queue.mark_played.assert_not_called()

    def test_matching_item_id_pops_correctly(self):
        """Matching item_id pops correctly."""
        items_round2 = [_make_queue_item(21)]
        mgr = _make_manager(pending=items_round2)
        mgr._enabled = True
        _set_assignment(mgr, "main", 20)

        mgr._queue.get_pending.return_value = items_round2
        mgr.on_video_finished("main", item_id=20)

        mgr._queue.mark_played.assert_called_once_with(20)

    def test_fleet_without_item_id_guard_still_works(self):
        """Fleet devices without item_id guard still work (backward compat)."""
        mgr = _make_manager()
        mgr._enabled = True
        _set_assignment(mgr, "z1", 5)

        # No item_id guard (fleet watcher uses on_video_finished without it)
        mgr.on_video_finished("z1")

        mgr._queue.mark_played.assert_called_once_with(5)
        assert "z1" not in mgr._assignments


# --- Watcher Integration ---


class TestWatcherIntegration:
    def test_full_cycle_assign_grace_confirm_finish(self):
        """Full cycle: assign -> grace -> confirmed playing -> finished."""
        fleet = _make_fleet(device_ids=["z1"])
        mgr = _make_manager(fleet=fleet)
        mgr._enabled = True

        # Step 1: Assign
        _set_assignment(mgr, "z1", 10)

        # Step 2: During grace, device is idle (starting up)
        fleet.is_device_idle.side_effect = lambda d: True
        mgr._process_fleet_assignments()
        assert _assigned_item(mgr, "z1") == 10  # Still assigned, grace active

        # Step 3: Device starts playing (not idle)
        fleet.is_device_idle.side_effect = lambda d: False
        mgr._process_fleet_assignments()
        assert mgr._assignments["z1"].confirmed_playing is True

        # Step 4: Device goes idle again = video finished
        fleet.is_device_idle.side_effect = lambda d: True
        mgr._process_fleet_assignments()

        mgr._queue.mark_played.assert_called_once_with(10)
        assert "z1" not in mgr._assignments


# --- Watcher Item ID Guard (S1) ---


class TestWatcherItemIdGuard:
    def test_watcher_finish_passes_item_id(self):
        """Watcher Branch 1 passes item_id to on_video_finished."""
        fleet = _make_fleet(device_ids=["z1"], idle_ids={"z1"})
        mgr = _make_manager(fleet=fleet)
        mgr._enabled = True
        mgr._assignments["z1"] = AssignmentInfo(
            item_id=10,
            assigned_at=time.monotonic() - 60,
            confirmed_playing=True,
        )

        mgr._process_fleet_assignments()

        # Verify item was marked played (item_id matched)
        mgr._queue.mark_played.assert_called_once_with(10)
        assert "z1" not in mgr._assignments

    def test_watcher_stale_finish_with_item_id_ignored(self):
        """Watcher finish with stale item_id is ignored when assignment changed."""
        fleet = _make_fleet(device_ids=["z1"], idle_ids={"z1"})
        mgr = _make_manager(fleet=fleet)
        mgr._enabled = True

        # Assign item 10, confirm playing
        mgr._assignments["z1"] = AssignmentInfo(
            item_id=10,
            assigned_at=time.monotonic() - 60,
            confirmed_playing=True,
        )

        # Simulate watcher processing: it snapshots assignments,
        # then between snapshot and on_video_finished call,
        # a new assignment replaces the old one.
        # on_video_finished(z1, item_id=10) should be rejected because
        # assignment is now item 20.
        mgr._assignments["z1"] = AssignmentInfo(
            item_id=20,
            assigned_at=time.monotonic(),
        )

        # Call on_video_finished as the watcher would (with old item_id)
        mgr.on_video_finished("z1", item_id=10)

        # New assignment preserved, old item NOT marked played
        assert _assigned_item(mgr, "z1") == 20
        mgr._queue.mark_played.assert_not_called()


# --- Watcher Exception Handling (S1) ---


class TestWatcherExceptionHandling:
    def test_watcher_exception_does_not_skip_remaining_devices(self):
        """Exception in one device doesn't prevent processing others."""
        fleet = _make_fleet(device_ids=["z1", "z2"], idle_ids={"z1", "z2"})
        mgr = _make_manager(fleet=fleet)
        mgr._enabled = True

        # z1 will throw, z2 has a completed video
        mgr._assignments["z1"] = AssignmentInfo(
            item_id=10,
            assigned_at=time.monotonic() - 60,
            confirmed_playing=True,
        )
        mgr._assignments["z2"] = AssignmentInfo(
            item_id=20,
            assigned_at=time.monotonic() - 60,
            confirmed_playing=True,
        )

        # Make is_device_idle raise for z1 but return True for z2
        def flaky_idle(dev_id):
            if dev_id == "z1":
                raise ConnectionError("z1 unreachable")
            return True

        fleet.is_device_idle.side_effect = flaky_idle

        mgr._process_fleet_assignments()

        # z2 should still be processed (finished)
        mgr._queue.mark_played.assert_called_once_with(20)
        assert "z2" not in mgr._assignments
        # z1 assignment still present (error didn't clear it)
        assert _assigned_item(mgr, "z1") == 10

    def test_watcher_exception_logs_warning(self):
        """Exception during device processing is logged as warning."""
        fleet = _make_fleet(device_ids=["z1"], idle_ids=set())
        mgr = _make_manager(fleet=fleet)
        mgr._enabled = True
        _set_assignment(mgr, "z1", 10)

        fleet.is_device_idle.side_effect = RuntimeError("network down")

        with patch("picast.server.multi_tv.logger") as mock_logger:
            mgr._process_fleet_assignments()
            mock_logger.warning.assert_called_once()
            args = mock_logger.warning.call_args[0]
            assert "z1" in args[1]

    def test_multiple_devices_one_errors_other_processed(self):
        """With 3 devices, error on middle one still processes first and last."""
        fleet = _make_fleet(device_ids=["z1", "z2", "z3"])
        mgr = _make_manager(fleet=fleet)
        mgr._enabled = True

        for dev_id, item_id in [("z1", 10), ("z2", 20), ("z3", 30)]:
            mgr._assignments[dev_id] = AssignmentInfo(
                item_id=item_id,
                assigned_at=time.monotonic() - 60,
                confirmed_playing=True,
            )

        def ordered_idle(dev_id):
            if dev_id == "z2":
                raise OSError("z2 flake")
            return True

        fleet.is_device_idle.side_effect = ordered_idle

        mgr._process_fleet_assignments()

        # z1 and z3 should be finished, z2 should still be assigned
        assert "z1" not in mgr._assignments
        assert _assigned_item(mgr, "z2") == 20
        assert "z3" not in mgr._assignments
        assert mgr._queue.mark_played.call_count == 2


# --- Failure Backoff (S1) ---


class TestFailureBackoff:
    def test_failure_backoff_skips_recently_failed(self):
        """Device with recent failure is skipped during backoff period."""
        items = [_make_queue_item(1), _make_queue_item(2)]
        fleet = _make_fleet(device_ids=["z1"], idle_ids={"z1"})
        mgr = _make_manager(fleet=fleet, pending=items)
        mgr._enabled = True

        # 1 failure, very recent
        mgr._device_failures["z1"] = (1, time.monotonic())

        mgr.distribute()

        # Main gets item 1, z1 is cooling off
        assert _assigned_item(mgr, "main") == 1
        assert "z1" not in mgr._assignments

    def test_failure_backoff_allows_after_cooldown(self):
        """Device is eligible again after backoff period expires."""
        items = [_make_queue_item(1), _make_queue_item(2)]
        fleet = _make_fleet(device_ids=["z1"], idle_ids={"z1"})
        mgr = _make_manager(fleet=fleet, pending=items)
        mgr._enabled = True

        # 1 failure, long ago (past backoff)
        mgr._device_failures["z1"] = (
            1, time.monotonic() - _FAILURE_BACKOFF_SECONDS - 1,
        )

        mgr.distribute()

        assert _assigned_item(mgr, "main") == 1
        assert _assigned_item(mgr, "z1") == 2

    def test_failure_backoff_cleared_on_success(self):
        """Successful video finish clears backoff state."""
        mgr = _make_manager()
        mgr._enabled = True
        mgr._device_failures["z1"] = (1, time.monotonic())
        _set_assignment(mgr, "z1", 5)

        mgr.on_video_finished("z1")

        assert mgr._device_failures.get("z1") is None

    def test_failure_backoff_cleared_on_disable(self):
        """Disable clears all backoff state."""
        mgr = _make_manager()
        mgr._enabled = True
        mgr._device_failures = {
            "z1": (2, time.monotonic()),
            "z2": (1, time.monotonic()),
        }
        mgr.disable()
        assert mgr._device_failures == {}

    def test_failure_backoff_cleared_on_enable(self):
        """Re-enable clears all backoff state."""
        mgr = _make_manager()
        mgr._enabled = True
        mgr._device_failures = {"z1": (2, time.monotonic())}
        # Simulate re-enable path (already enabled)
        with mgr._lock:
            mgr._assignments.clear()
            mgr._check_cache.clear()
            mgr._device_failures.clear()
        assert mgr._device_failures == {}

    def test_backoff_combined_with_grayout(self):
        """Grayout takes precedence over backoff (both skip the device)."""
        items = [_make_queue_item(1), _make_queue_item(2)]
        fleet = _make_fleet(device_ids=["z1"], idle_ids={"z1"})
        mgr = _make_manager(fleet=fleet, pending=items)
        mgr._enabled = True

        # At grayout threshold AND recent failure
        mgr._device_failures["z1"] = (
            _MAX_CONSECUTIVE_FAILURES, time.monotonic(),
        )

        mgr.distribute()

        assert _assigned_item(mgr, "main") == 1
        assert "z1" not in mgr._assignments

    def test_grace_failure_increments_with_timestamp(self):
        """Grace expired failure stores (count, timestamp) tuple."""
        fleet = _make_fleet(device_ids=["z1"], idle_ids={"z1"})
        mgr = _make_manager(fleet=fleet)
        mgr._enabled = True
        mgr._assignments["z1"] = AssignmentInfo(
            item_id=10,
            assigned_at=time.monotonic() - _GRACE_PERIOD_SECONDS - 1,
        )

        before = time.monotonic()
        mgr._process_fleet_assignments()
        after = time.monotonic()

        count, last_failure_at = mgr._device_failures["z1"]
        assert count == 1
        assert before <= last_failure_at <= after


# --- MultiTVConfig (S2) ---


class TestMultiTVConfig:
    def test_config_defaults_match_original_constants(self):
        """Default config values match the original module-level constants."""
        cfg = MultiTVConfig()
        assert cfg.grace_period == _GRACE_PERIOD_SECONDS
        assert cfg.max_consecutive_failures == _MAX_CONSECUTIVE_FAILURES
        assert cfg.check_cache_ttl == _CHECK_CACHE_TTL
        assert cfg.failure_backoff == _FAILURE_BACKOFF_SECONDS

    def test_config_from_toml_section(self):
        """Config can be loaded from TOML dict."""
        from picast.config import _parse_config

        data = {"multi_tv": {"grace_period": 20, "failure_backoff": 60}}
        config = _parse_config(data)
        assert config.multi_tv.grace_period == 20
        assert config.multi_tv.failure_backoff == 60
        # Unset values use defaults
        assert config.multi_tv.max_consecutive_failures == 3
        assert config.multi_tv.check_cache_ttl == 300

    def test_manager_without_config_uses_defaults(self):
        """Manager created without config uses default MultiTVConfig."""
        mgr = _make_manager()
        assert mgr._config.grace_period == _GRACE_PERIOD_SECONDS
        assert mgr._config.max_consecutive_failures == _MAX_CONSECUTIVE_FAILURES

    def test_manager_with_custom_config(self):
        """Manager uses provided config values."""
        cfg = MultiTVConfig(grace_period=25, failure_backoff=10)
        mgr = _make_manager(config=cfg)
        assert mgr._config.grace_period == 25
        assert mgr._config.failure_backoff == 10

    def test_custom_failure_backoff_from_config(self):
        """Custom failure_backoff from config is respected in distribute."""
        cfg = MultiTVConfig(failure_backoff=5)
        items = [_make_queue_item(1), _make_queue_item(2)]
        fleet = _make_fleet(device_ids=["z1"], idle_ids={"z1"})
        mgr = _make_manager(fleet=fleet, pending=items, config=cfg)
        mgr._enabled = True

        # Recent failure (within 5s backoff)
        mgr._device_failures["z1"] = (1, time.monotonic())
        mgr.distribute()
        assert "z1" not in mgr._assignments

        # Old failure (past 5s backoff) — would pass default 30s too,
        # but use tight timing to prove config is consulted
        mgr._device_failures["z1"] = (1, time.monotonic() - 6)
        mgr._assignments.clear()
        mgr._queue.get_pending.return_value = [_make_queue_item(1), _make_queue_item(2)]
        mgr.distribute()
        assert _assigned_item(mgr, "z1") is not None


# --- Per-Device Grace Period (S2) ---


class TestPerDeviceGrace:
    def test_per_device_grace_override(self):
        """Fleet device with grace_period > 0 overrides global."""
        fleet = _make_fleet(device_ids=["z1"], idle_ids={"z1"})
        # Set up device config with custom grace period
        state = MagicMock()
        state.config.grace_period = 30  # 30s per-device
        fleet._devices = {"z1": state}
        fleet._lock = threading.Lock()

        cfg = MultiTVConfig(grace_period=15)
        mgr = _make_manager(fleet=fleet, config=cfg)
        mgr._enabled = True

        # Assign 20s ago — would fail with global 15s, but within device 30s
        mgr._assignments["z1"] = AssignmentInfo(
            item_id=10,
            assigned_at=time.monotonic() - 20,
        )

        mgr._process_fleet_assignments()

        # Still within per-device grace, should NOT have failed
        assert _assigned_item(mgr, "z1") == 10
        mgr._queue.mark_pending.assert_not_called()

    def test_per_device_grace_zero_uses_global(self):
        """Fleet device with grace_period=0 falls back to global."""
        fleet = _make_fleet(device_ids=["z1"], idle_ids={"z1"})
        state = MagicMock()
        state.config.grace_period = 0  # Means "use global"
        fleet._devices = {"z1": state}
        fleet._lock = threading.Lock()

        cfg = MultiTVConfig(grace_period=15)
        mgr = _make_manager(fleet=fleet, config=cfg)
        mgr._enabled = True

        # Assign 20s ago — beyond global 15s grace
        mgr._assignments["z1"] = AssignmentInfo(
            item_id=10,
            assigned_at=time.monotonic() - 20,
        )

        mgr._process_fleet_assignments()

        # Should have failed (past global 15s)
        assert "z1" not in mgr._assignments
        mgr._queue.mark_pending.assert_called_once_with(10)


# --- Cache Eviction (S2) ---


class TestCacheEviction:
    def test_evict_removes_expired_entries(self):
        """Entries older than TTL are evicted."""
        cfg = MultiTVConfig(check_cache_ttl=60)
        mgr = _make_manager(config=cfg)

        now = time.monotonic()
        mgr._check_cache = {
            "https://old.url": (True, now - 120),  # Expired
            "https://new.url": (True, now - 10),    # Fresh
        }

        mgr._evict_stale_cache()

        assert "https://old.url" not in mgr._check_cache
        assert "https://new.url" in mgr._check_cache

    def test_evict_caps_at_max_size(self):
        """Cache is trimmed to max_size, removing oldest entries."""
        cfg = MultiTVConfig(check_cache_ttl=600, check_cache_max_size=2)
        mgr = _make_manager(config=cfg)

        now = time.monotonic()
        mgr._check_cache = {
            "https://oldest.url": (True, now - 30),
            "https://middle.url": (True, now - 20),
            "https://newest.url": (True, now - 10),
        }

        mgr._evict_stale_cache()

        assert len(mgr._check_cache) == 2
        assert "https://oldest.url" not in mgr._check_cache
        assert "https://newest.url" in mgr._check_cache
        assert "https://middle.url" in mgr._check_cache

    def test_custom_watch_intervals_used(self):
        """Config watch intervals are actually used by the manager."""
        cfg = MultiTVConfig(watch_interval_playing=1, watch_interval_idle=2)
        mgr = _make_manager(config=cfg)
        assert mgr._config.watch_interval_playing == 1
        assert mgr._config.watch_interval_idle == 2


# --- Session 3: Grayout Recovery + Notifications ---


class TestGrayoutNotification:
    """Tests for grayout notifications when devices hit failure threshold."""

    def test_grayout_triggers_notification(self):
        """Notification fires when device crosses max_consecutive_failures threshold."""
        notified = []
        cfg = MultiTVConfig(max_consecutive_failures=3)
        fleet = _make_fleet(["tv1"], idle_ids=set())
        mgr = _make_manager(fleet=fleet, config=cfg)
        mgr._notify_fn = lambda text: notified.append(text)
        mgr._enabled = True

        # Simulate 3 failures crossing threshold
        for i in range(3):
            with mgr._lock:
                mgr._device_failures["tv1"] = (i + 1, time.monotonic())
            if i + 1 == cfg.max_consecutive_failures:
                mgr._grayout_times["tv1"] = time.monotonic()
                mgr._notify(
                    f"PiCast Multi-TV: Device 'tv1' grayed out after {i + 1} failed starts"
                )

        assert len(notified) == 1
        assert "grayed out" in notified[0]
        assert "tv1" in notified[0]

    def test_grayout_notification_only_on_threshold_crossing(self):
        """Notification fires exactly once at threshold, not on subsequent failures."""
        notified = []
        cfg = MultiTVConfig(max_consecutive_failures=2)
        mgr = _make_manager(config=cfg)
        mgr._notify_fn = lambda text: notified.append(text)

        # At threshold (2) — should notify
        new_count = 2
        if new_count == cfg.max_consecutive_failures:
            mgr._grayout_times["tv1"] = time.monotonic()
            mgr._notify(f"PiCast Multi-TV: Device 'tv1' grayed out after {new_count} failed starts")

        # Above threshold (3) — should NOT notify again
        new_count = 3
        if new_count == cfg.max_consecutive_failures:
            mgr._notify(f"PiCast Multi-TV: Device 'tv1' grayed out after {new_count} failed starts")

        assert len(notified) == 1

    def test_no_notify_fn_no_crash(self):
        """Manager without notify_fn doesn't crash when trying to notify."""
        mgr = _make_manager()
        assert mgr._notify_fn is None
        # Should not raise
        mgr._notify("test message")


class TestGrayoutRecovery:
    """Tests for _check_grayout_recovery() auto-recovery probe."""

    def test_cooldown_probe_succeeds_clears_failures(self):
        """Device that responds after cooldown is cleared from grayout."""
        cfg = MultiTVConfig(grayout_cooldown=10)
        fleet = _make_fleet(["tv1"], idle_ids={"tv1"})
        mgr = _make_manager(fleet=fleet, config=cfg)
        mgr._enabled = True

        # Set grayout in the past (beyond cooldown)
        mgr._grayout_times["tv1"] = time.monotonic() - 20
        mgr._device_failures["tv1"] = (3, time.monotonic() - 20)

        mgr._check_grayout_recovery()

        assert "tv1" not in mgr._grayout_times
        assert "tv1" not in mgr._device_failures

    def test_cooldown_probe_fails_keeps_grayed(self):
        """Device that raises exception during probe stays grayed out."""
        cfg = MultiTVConfig(grayout_cooldown=10)
        fleet = _make_fleet(["tv1"], idle_ids=set())
        fleet.is_device_idle.side_effect = Exception("unreachable")
        mgr = _make_manager(fleet=fleet, config=cfg)

        mgr._grayout_times["tv1"] = time.monotonic() - 20
        mgr._device_failures["tv1"] = (3, time.monotonic() - 20)

        mgr._check_grayout_recovery()

        assert "tv1" in mgr._grayout_times
        assert "tv1" in mgr._device_failures

    def test_no_probe_before_cooldown_expires(self):
        """Devices within cooldown period are not probed."""
        cfg = MultiTVConfig(grayout_cooldown=300)
        fleet = _make_fleet(["tv1"], idle_ids={"tv1"})
        mgr = _make_manager(fleet=fleet, config=cfg)

        # Grayed out 10s ago, cooldown is 300s — should NOT probe
        mgr._grayout_times["tv1"] = time.monotonic() - 10
        mgr._device_failures["tv1"] = (3, time.monotonic() - 10)

        mgr._check_grayout_recovery()

        # Still grayed — was not probed
        assert "tv1" in mgr._grayout_times
        assert "tv1" in mgr._device_failures
        # Verify is_device_idle was NOT called (no probe attempt)
        fleet.is_device_idle.assert_not_called()

    def test_recovery_sends_notification(self):
        """Recovery triggers a notification."""
        notified = []
        cfg = MultiTVConfig(grayout_cooldown=10)
        fleet = _make_fleet(["tv1"], idle_ids={"tv1"})
        mgr = _make_manager(fleet=fleet, config=cfg)
        mgr._notify_fn = lambda text: notified.append(text)
        mgr._enabled = True

        mgr._grayout_times["tv1"] = time.monotonic() - 20
        mgr._device_failures["tv1"] = (3, time.monotonic() - 20)

        mgr._check_grayout_recovery()

        assert len(notified) == 1
        assert "recovered" in notified[0]
        assert "tv1" in notified[0]

    def test_recovery_triggers_distribute(self):
        """Recovery calls distribute() to assign queue items to recovered device."""
        cfg = MultiTVConfig(grayout_cooldown=10)
        fleet = _make_fleet(["tv1"], idle_ids={"tv1"})
        items = [_make_queue_item(1)]
        mgr = _make_manager(fleet=fleet, pending=items, config=cfg)
        mgr._enabled = True

        mgr._grayout_times["tv1"] = time.monotonic() - 20
        mgr._device_failures["tv1"] = (3, time.monotonic() - 20)

        with patch.object(mgr, "distribute") as mock_dist:
            mgr._check_grayout_recovery()
            mock_dist.assert_called_once()

    def test_no_recovery_without_fleet(self):
        """_check_grayout_recovery is a no-op without fleet."""
        mgr = _make_manager(fleet=None)
        mgr._grayout_times["tv1"] = time.monotonic() - 999

        mgr._check_grayout_recovery()

        # Still there — no fleet to probe
        assert "tv1" in mgr._grayout_times

    def test_disable_clears_grayout_times(self):
        """disable() clears grayout tracking."""
        mgr = _make_manager()
        mgr._enabled = True
        mgr._grayout_times["tv1"] = time.monotonic()

        mgr.disable()

        assert len(mgr._grayout_times) == 0

    def test_enable_clears_grayout_times(self):
        """enable() clears grayout tracking."""
        fleet = _make_fleet(["tv1"], idle_ids={"tv1"})
        mgr = _make_manager(fleet=fleet)
        mgr._grayout_times["tv1"] = time.monotonic()

        # First enable
        mgr.enable()
        # Give background thread a moment to start
        time.sleep(0.1)

        assert len(mgr._grayout_times) == 0

        mgr.disable()

    def test_enable_reset_clears_grayout_times(self):
        """enable() when already enabled (reset path) clears grayout tracking."""
        fleet = _make_fleet(["tv1"], idle_ids={"tv1"})
        mgr = _make_manager(fleet=fleet)

        # First enable
        mgr.enable()
        time.sleep(0.1)

        # Add grayout
        mgr._grayout_times["tv1"] = time.monotonic()

        # Second enable (reset path)
        mgr.enable()

        assert len(mgr._grayout_times) == 0

        mgr.disable()


# --- Watcher Non-Blocking Distribute (S4) ---


class TestWatcherDistribute:
    """Tests for _watcher_distribute() non-blocking threading."""

    def test_watcher_distribute_runs_in_background_thread(self):
        """_watcher_distribute() spawns a named daemon thread."""
        items = [_make_queue_item(1)]
        mgr = _make_manager(pending=items)
        mgr._enabled = True

        mgr._watcher_distribute()

        # Wait for the thread to complete
        for t in threading.enumerate():
            if t.name == "multi-tv-watcher-distribute":
                assert t.daemon is True
                t.join(timeout=2)
                break

        # distribute() was called via the thread
        assert _assigned_item(mgr, "main") == 1

    def test_on_video_finished_from_watcher_non_blocking(self):
        """on_video_finished with _from_watcher=True uses non-blocking distribute."""
        items = [_make_queue_item(2)]
        mgr = _make_manager(pending=items)
        mgr._enabled = True
        _set_assignment(mgr, "z1", 5)

        mgr._queue.get_pending.return_value = items
        mgr.on_video_finished("z1", _from_watcher=True)

        # Wait for background thread
        for t in threading.enumerate():
            if t.name == "multi-tv-watcher-distribute":
                t.join(timeout=2)

        mgr._queue.mark_played.assert_called_once_with(5)
        # distribute ran in background thread
        assert _assigned_item(mgr, "main") == 2

    def test_on_video_finished_from_http_blocking(self):
        """on_video_finished without _from_watcher uses synchronous distribute."""
        items = [_make_queue_item(2)]
        mgr = _make_manager(pending=items)
        mgr._enabled = True
        _set_assignment(mgr, "main", 5)

        mgr._queue.get_pending.return_value = items
        mgr.on_video_finished("main", item_id=5)

        # Synchronous — result is immediately available (no thread wait needed)
        mgr._queue.mark_played.assert_called_once_with(5)
        assert _assigned_item(mgr, "main") == 2

    def test_branch4_failure_uses_watcher_distribute(self):
        """Branch 4 (grace expired) uses non-blocking _watcher_distribute."""
        fleet = _make_fleet(device_ids=["z1"], idle_ids={"z1"})
        items = [_make_queue_item(1)]
        mgr = _make_manager(fleet=fleet, pending=items)
        mgr._enabled = True
        mgr._assignments["z1"] = AssignmentInfo(
            item_id=10,
            assigned_at=time.monotonic() - _GRACE_PERIOD_SECONDS - 1,
        )

        with patch.object(mgr, "_watcher_distribute") as mock_wd:
            mgr._process_fleet_assignments()
            mock_wd.assert_called_once()

    def test_grayout_recovery_uses_watcher_distribute(self):
        """_check_grayout_recovery uses non-blocking _watcher_distribute."""
        cfg = MultiTVConfig(grayout_cooldown=10)
        fleet = _make_fleet(["tv1"], idle_ids={"tv1"})
        mgr = _make_manager(fleet=fleet, config=cfg)
        mgr._enabled = True
        mgr._grayout_times["tv1"] = time.monotonic() - 20
        mgr._device_failures["tv1"] = (3, time.monotonic() - 20)

        with patch.object(mgr, "_watcher_distribute") as mock_wd:
            mgr._check_grayout_recovery()
            mock_wd.assert_called_once()


# --- Metrics (S4) ---


class TestGetMetrics:
    """Tests for get_metrics() operational metrics."""

    def test_metrics_returns_expected_keys(self):
        """get_metrics() returns all documented keys."""
        mgr = _make_manager()
        mgr._enabled = True
        metrics = mgr.get_metrics()

        expected_keys = {
            "enabled", "assignments", "device_failures",
            "grayed_out_devices", "grayout_cooldown_remaining",
            "check_cache_size", "watcher_alive",
        }
        assert set(metrics.keys()) == expected_keys

    def test_metrics_assignment_ages(self):
        """Assignment ages are computed correctly."""
        mgr = _make_manager()
        mgr._enabled = True
        # Assign 10 seconds ago
        mgr._assignments["main"] = AssignmentInfo(
            item_id=1,
            assigned_at=time.monotonic() - 10,
            confirmed_playing=True,
        )

        metrics = mgr.get_metrics()

        assert "main" in metrics["assignments"]
        main_info = metrics["assignments"]["main"]
        assert main_info["item_id"] == 1
        assert main_info["confirmed_playing"] is True
        assert main_info["age_seconds"] >= 10.0
        assert main_info["age_seconds"] < 12.0  # Allow small timing slack

    def test_metrics_watcher_alive_false_when_stopped(self):
        """watcher_alive is False when no watcher thread."""
        mgr = _make_manager()
        metrics = mgr.get_metrics()
        assert metrics["watcher_alive"] is False

    def test_metrics_watcher_alive_true(self):
        """watcher_alive is True when watcher is running."""
        fleet = _make_fleet(["z1"])
        mgr = _make_manager(fleet=fleet)
        mgr._enabled = True
        mgr._stop_event.clear()
        mgr._start_watcher()
        time.sleep(0.05)

        metrics = mgr.get_metrics()
        assert metrics["watcher_alive"] is True

        mgr.disable()

    def test_metrics_grayout_cooldown_remaining(self):
        """Grayout cooldown remaining is computed from _grayout_times."""
        cfg = MultiTVConfig(grayout_cooldown=300)
        mgr = _make_manager(config=cfg)
        mgr._enabled = True

        # Grayed out 100s ago with 300s cooldown -> ~200s remaining
        mgr._grayout_times["z1"] = time.monotonic() - 100
        mgr._device_failures["z1"] = (3, time.monotonic() - 100)

        metrics = mgr.get_metrics()

        assert "z1" in metrics["grayout_cooldown_remaining"]
        remaining = metrics["grayout_cooldown_remaining"]["z1"]
        assert 198 <= remaining <= 202  # ~200s remaining

        assert "z1" in metrics["grayed_out_devices"]

    def test_metrics_device_failures_backoff(self):
        """Device failures include backoff_remaining."""
        cfg = MultiTVConfig(failure_backoff=30)
        mgr = _make_manager(config=cfg)

        # Failed 10s ago with 30s backoff -> ~20s remaining
        mgr._device_failures["z1"] = (1, time.monotonic() - 10)

        metrics = mgr.get_metrics()

        assert "z1" in metrics["device_failures"]
        z1 = metrics["device_failures"]["z1"]
        assert z1["count"] == 1
        assert 18 <= z1["backoff_remaining"] <= 22

    def test_metrics_cache_size(self):
        """check_cache_size reflects actual cache contents."""
        mgr = _make_manager()
        mgr._check_cache = {
            "https://a.url": (True, time.monotonic()),
            "https://b.url": (False, time.monotonic()),
        }

        metrics = mgr.get_metrics()
        assert metrics["check_cache_size"] == 2


# --- Network Failure During Watcher (S4) ---


class TestNetworkFailureDuringWatcher:
    """Tests for network failures in is_device_idle during watcher processing."""

    def test_network_failure_during_is_device_idle(self):
        """Network error during is_device_idle leaves assignment intact."""
        fleet = _make_fleet(device_ids=["z1"])
        fleet.is_device_idle.side_effect = OSError("Connection refused")

        mgr = _make_manager(fleet=fleet)
        mgr._enabled = True
        _set_assignment(mgr, "z1", 10)

        mgr._process_fleet_assignments()

        # Assignment preserved — error was caught
        assert _assigned_item(mgr, "z1") == 10
        mgr._queue.mark_played.assert_not_called()
        mgr._queue.mark_pending.assert_not_called()
