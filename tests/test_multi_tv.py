"""Tests for MultiTVManager — queue distribution across multiple TVs."""

import subprocess
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from picast.server.multi_tv import (
    AssignmentInfo,
    MultiTVManager,
    _CHECK_CACHE_TTL,
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


def _make_manager(fleet=None, pending=None):
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
        mgr._device_failures = {"z1": 3}
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
        mgr._device_failures["z1"] = 2
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
        mgr._device_failures["z1"] = _MAX_CONSECUTIVE_FAILURES

        mgr.distribute()

        # Main gets item, z1 is grayed out
        assert _assigned_item(mgr, "main") == 1
        assert "z1" not in mgr._assignments

    def test_successful_play_clears_failure_counter(self):
        """Successful video finish clears failure counter."""
        mgr = _make_manager()
        mgr._enabled = True
        mgr._device_failures["z1"] = 2
        _set_assignment(mgr, "z1", 5)

        mgr.on_video_finished("z1")

        assert mgr._device_failures.get("z1") is None

    def test_enable_clears_all_failure_counters(self):
        """Re-enable clears all failure counters."""
        mgr = _make_manager()
        mgr._enabled = True
        mgr._device_failures = {"z1": 3, "z2": 2}

        # Reset via enable (already on path)
        mgr._assignments.clear()
        mgr._check_cache.clear()
        mgr._device_failures.clear()

        assert mgr._device_failures == {}

    def test_get_status_includes_grayed_out(self):
        """get_status() includes grayed_out_devices list."""
        mgr = _make_manager()
        mgr._enabled = True
        mgr._device_failures = {"z1": _MAX_CONSECUTIVE_FAILURES, "z2": 1}

        status = mgr.get_status()
        assert "grayed_out_devices" in status
        assert "z1" in status["grayed_out_devices"]
        assert "z2" not in status["grayed_out_devices"]

    def test_disable_clears_failure_counters(self):
        """Disable clears failure counters."""
        mgr = _make_manager()
        mgr._enabled = True
        mgr._device_failures = {"z1": 3}
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

        assert mgr._device_failures["z1"] == 1


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
