"""Tests for MultiTVManager — queue distribution across multiple TVs."""

import subprocess
import time
from unittest.mock import MagicMock, patch

import pytest

from picast.server.multi_tv import MultiTVManager, _CHECK_CACHE_TTL


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
        mgr._assignments = {"main": 1, "z1": 2}
        mgr.disable()
        assert not mgr.enabled
        assert mgr._assignments == {}

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
        assert mgr._assignments == {"main": 1}
        mgr._player.play_now.assert_called_once_with(item.url, item.title)

    def test_distribute_two_devices_two_items(self):
        """Main gets #1, fleet device gets #2."""
        items = [_make_queue_item(1, title="Video 1"), _make_queue_item(2, title="Video 2")]
        fleet = _make_fleet(device_ids=["z1"], idle_ids={"z1"})
        mgr = _make_manager(fleet=fleet, pending=items)
        mgr._enabled = True
        mgr.distribute()

        assert mgr._assignments.get("main") == 1
        assert mgr._assignments.get("z1") == 2
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

        assert mgr._assignments.get("main") == 1
        assert "z1" not in mgr._assignments
        assert "z2" not in mgr._assignments

    def test_distribute_skips_already_assigned(self):
        """Don't re-assign items that are already playing on a device."""
        items = [_make_queue_item(1), _make_queue_item(2)]
        mgr = _make_manager(pending=items)
        mgr._enabled = True
        mgr._assignments = {"main": 1}  # main already has item 1

        # No fleet, so only main as device. Main is assigned, so no action.
        mgr.distribute()
        # Main still has item 1, no change
        assert mgr._assignments == {"main": 1}

    def test_distribute_disabled_does_nothing(self):
        items = [_make_queue_item(1)]
        mgr = _make_manager(pending=items)
        mgr._enabled = False
        mgr.distribute()
        assert mgr._assignments == {}
        mgr._player.play_now.assert_not_called()


# --- On Video Finished ---


class TestOnVideoFinished:
    def test_on_video_finished_advances_queue(self):
        """When main finishes, it gets the next pending item."""
        items_round1 = [_make_queue_item(1), _make_queue_item(2)]
        items_round2 = [_make_queue_item(2)]  # item 1 is now played

        mgr = _make_manager(pending=items_round1)
        mgr._enabled = True
        mgr._assignments = {"main": 1}

        # After finishing, queue returns only item 2
        mgr._queue.get_pending.return_value = items_round2
        mgr.on_video_finished("main")

        mgr._queue.mark_played.assert_called_once_with(1)
        # Should try to distribute again
        assert mgr._assignments.get("main") == 2

    def test_on_video_finished_no_assignment(self):
        """Finishing with no prior assignment doesn't crash."""
        mgr = _make_manager()
        mgr._enabled = True
        mgr.on_video_finished("unknown-device")
        mgr._queue.mark_played.assert_not_called()


# --- On Queue Changed ---


class TestOnQueueChanged:
    def test_on_queue_changed_fills_idle(self):
        """New item added goes to idle TV."""
        item = _make_queue_item(5)
        mgr = _make_manager(pending=[item])
        mgr._enabled = True
        mgr.on_queue_changed()
        assert mgr._assignments.get("main") == 5

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
        assert mgr._assignments.get("main") == 2


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
        assert mgr._assignments.get("main") == 1
        assert "z1" not in mgr._assignments

    def test_autoplay_device_gets_queue_item(self):
        """Fleet device playing autoplay content is available for queue items."""
        items = [_make_queue_item(1), _make_queue_item(2)]
        # z1 not idle (playing autoplay) but available for queue
        fleet = _make_fleet(device_ids=["z1"], idle_ids=set(), available_ids={"z1"})
        mgr = _make_manager(fleet=fleet, pending=items)
        mgr._enabled = True
        mgr.distribute()

        assert mgr._assignments.get("main") == 1
        assert mgr._assignments.get("z1") == 2
        fleet.play_immediately.assert_called_once()


# --- Get Status ---


class TestGetStatus:
    def test_get_status_shape(self):
        """Verify response dict has expected keys."""
        fleet = _make_fleet(device_ids=["z1"])
        items = [_make_queue_item(1)]
        mgr = _make_manager(fleet=fleet, pending=items)
        mgr._enabled = True
        mgr._assignments = {"main": 1}

        status = mgr.get_status()

        assert "enabled" in status
        assert status["enabled"] is True
        assert "devices" in status
        assert len(status["devices"]) == 2  # main + z1
        assert "queue_remaining" in status
        assert "skipped_urls" in status
        assert "checking" in status
        assert status["checking"] is False

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
        mgr._assignments = {"main": 1}

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
        assert mgr._assignments.get("main") == 1
        assert "z1" not in mgr._assignments


# --- No Fleet ---


class TestNoFleet:
    def test_works_with_no_fleet(self):
        """Multi-TV works with main device only when fleet is None."""
        item = _make_queue_item(1)
        mgr = _make_manager(fleet=None, pending=[item])
        mgr._enabled = True
        mgr.distribute()

        assert mgr._assignments == {"main": 1}

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
