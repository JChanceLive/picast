"""Tests for notification manager and watch analytics."""

import time
from unittest.mock import MagicMock, patch

from picast.server.notifications import (
    ALERT_COOLDOWN,
    SD_ERROR_THRESHOLD,
    NotificationManager,
)


class TestNotificationManager:
    """Test NotificationManager logic."""

    def test_record_sd_error(self, db):
        mgr = NotificationManager(db=db)
        mgr.record_sd_error("disk_io", "disk I/O error")
        row = db.fetchone("SELECT COUNT(*) as cnt FROM sd_errors")
        assert row["cnt"] == 1

    def test_sd_error_threshold_triggers_alert(self, db):
        sent = []
        mgr = NotificationManager(
            db=db,
            send_fn=lambda cid, txt: sent.append(txt),
            chat_id=123,
        )
        # Record errors up to threshold
        for i in range(SD_ERROR_THRESHOLD):
            mgr.record_sd_error("disk_io", f"error {i}")

        assert len(sent) == 1
        assert "SD Card Alert" in sent[0]

    def test_sd_error_cooldown(self, db):
        sent = []
        mgr = NotificationManager(
            db=db,
            send_fn=lambda cid, txt: sent.append(txt),
            chat_id=123,
        )
        # First batch triggers alert
        for i in range(SD_ERROR_THRESHOLD):
            mgr.record_sd_error("disk_io", f"error {i}")
        assert len(sent) == 1

        # Second batch within cooldown should NOT trigger
        for i in range(SD_ERROR_THRESHOLD):
            mgr.record_sd_error("disk_io", f"error again {i}")
        assert len(sent) == 1  # Still 1

    def test_no_alert_without_chat_id(self, db):
        sent = []
        mgr = NotificationManager(
            db=db,
            send_fn=lambda cid, txt: sent.append(txt),
            chat_id=0,  # No chat ID
        )
        for i in range(SD_ERROR_THRESHOLD):
            mgr.record_sd_error("disk_io", f"error {i}")
        assert len(sent) == 0

    def test_get_sd_errors(self, db):
        mgr = NotificationManager(db=db)
        mgr.record_sd_error("disk_io", "error 1")
        mgr.record_sd_error("disk_io", "error 2")
        assert mgr.get_sd_errors(hours=1) == 2


class TestWatchAnalytics:
    """Test watch analytics computation."""

    def test_empty_analytics(self, db):
        mgr = NotificationManager(db=db)
        analytics = mgr.get_watch_analytics(hours=24)
        assert analytics["total_sessions"] == 0
        assert analytics["total_duration"] == 0
        assert analytics["top_by_time"] == []
        assert analytics["top_by_count"] == []

    def test_analytics_with_sessions(self, db):
        now = time.time()
        # Insert some watch sessions
        db.execute(
            "INSERT INTO watch_sessions (url, title, source_type, started_at, ended_at, duration_watched) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("https://a.com", "Video A", "youtube", now - 3600, now - 3000, 600),
        )
        db.execute(
            "INSERT INTO watch_sessions (url, title, source_type, started_at, ended_at, duration_watched) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("https://b.com", "Video B", "archive", now - 2000, now - 1000, 1000),
        )
        db.commit()

        mgr = NotificationManager(db=db)
        analytics = mgr.get_watch_analytics(hours=24)
        assert analytics["total_sessions"] == 2
        assert analytics["total_duration"] == 1600
        assert len(analytics["top_by_time"]) == 2
        # Video B has more duration
        assert analytics["top_by_time"][0]["title"] == "Video B"

    def test_analytics_respects_window(self, db):
        now = time.time()
        # Insert session outside window (2 days ago)
        db.execute(
            "INSERT INTO watch_sessions (url, title, source_type, started_at, ended_at, duration_watched) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("https://old.com", "Old Video", "youtube", now - 200000, now - 199000, 1000),
        )
        db.commit()

        mgr = NotificationManager(db=db)
        analytics = mgr.get_watch_analytics(hours=24)
        assert analytics["total_sessions"] == 0


class TestAnalyticsAPI:
    """Test the /api/analytics endpoint."""

    def test_analytics_endpoint(self, client):
        resp = client.get("/api/analytics")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "total_sessions" in data
        assert "total_duration" in data

    def test_analytics_custom_hours(self, client):
        resp = client.get("/api/analytics?hours=48")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["hours"] == 48


class TestPushoverIntegration:
    """Test NotificationManager with Pushover adapter."""

    @patch("picast.server.pushover_adapter.urllib.request.urlopen")
    def test_sd_threshold_triggers_pushover_send(self, mock_urlopen, db):
        mock_urlopen.return_value.__enter__ = MagicMock(return_value=MagicMock(read=MagicMock(return_value=b"")))
        mock_urlopen.return_value.__exit__ = MagicMock(return_value=False)

        from picast.server.pushover_adapter import create_pushover_send_fn

        send_fn = create_pushover_send_fn("tok_abc", "user_xyz")
        mgr = NotificationManager(
            db=db,
            send_fn=send_fn,
            chat_id=1,  # Dummy — Pushover ignores it
        )

        for i in range(SD_ERROR_THRESHOLD):
            mgr.record_sd_error("disk_io", f"error {i}")

        assert mock_urlopen.call_count == 1
        req = mock_urlopen.call_args[0][0]
        body = req.data.decode("utf-8")
        assert "priority=1" in body
        assert "title=PiCast+SD+Alert" in body


class TestHealthEndpointSD:
    """Test /api/health includes SD error count."""

    def test_health_includes_sd_errors(self, client):
        resp = client.get("/api/health")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "sd_errors_1h" in data
        assert data["sd_errors_1h"] == 0
