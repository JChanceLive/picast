"""Telegram notification manager for PiCast.

Sends push alerts for SD card health issues and daily watch analytics
summaries via Telegram.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from picast.server.database import Database

logger = logging.getLogger(__name__)

# SD error thresholds
SD_ERROR_WINDOW = 3600       # 1 hour sliding window
SD_ERROR_THRESHOLD = 3       # Errors in window to trigger alert
ALERT_COOLDOWN = 1800        # 30 min between alerts
HOURLY_CHECK_INTERVAL = 3600  # 1 hour between background checks


class NotificationManager:
    """Manages Telegram push notifications for PiCast.

    Features:
    - SD card error monitoring with sliding window threshold
    - Daily watch analytics summary
    - Alert cooldown to prevent spam

    Args:
        db: PiCast database instance
        send_fn: Callable(chat_id, text) to send Telegram messages.
                 Typically telegram_bot.send_notification_sync.
        chat_id: Telegram chat ID for notifications
        daily_summary_hour: Hour (0-23) to send daily summary (default: 8 AM)
    """

    def __init__(
        self,
        db: "Database",
        send_fn=None,
        chat_id: int = 0,
        daily_summary_hour: int = 8,
    ):
        self._db = db
        self._send_fn = send_fn
        self._chat_id = chat_id
        self._daily_summary_hour = daily_summary_hour
        self._last_alert_time: float = 0
        self._thread: threading.Thread | None = None
        self._running = False
        self._last_daily_date: str = ""

    def start(self):
        """Start the background notification thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._background_loop,
            daemon=True,
            name="notifications",
        )
        self._thread.start()
        logger.info("Notification manager started (chat_id=%d, summary_hour=%d)",
                     self._chat_id, self._daily_summary_hour)

    def stop(self):
        """Stop the background thread."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None

    def record_sd_error(self, error_type: str, detail: str):
        """Record an SD card error and check threshold.

        Called by database._retry_on_io_error() on disk I/O errors.
        """
        now = time.time()
        try:
            self._db.execute(
                "INSERT INTO sd_errors (error_type, detail, occurred_at) VALUES (?, ?, ?)",
                (error_type, detail[:500], now),
            )
            self._db.commit()
        except Exception as e:
            logger.warning("Failed to record SD error: %s", e)
            return

        # Check if threshold exceeded
        self._check_sd_threshold()

    def _check_sd_threshold(self):
        """Check if SD errors exceed threshold in sliding window."""
        now = time.time()
        window_start = now - SD_ERROR_WINDOW

        try:
            row = self._db.fetchone(
                "SELECT COUNT(*) as cnt FROM sd_errors WHERE occurred_at >= ?",
                (window_start,),
            )
            count = row["cnt"] if row else 0
        except Exception:
            return

        if count >= SD_ERROR_THRESHOLD:
            # Check cooldown
            if now - self._last_alert_time < ALERT_COOLDOWN:
                return
            self._last_alert_time = now

            msg = (
                f"⚠️ PiCast SD Card Alert\n\n"
                f"{count} disk I/O errors in the last hour.\n"
                f"Check SD card health:\n"
                f"  ssh picast \"sudo dmesg | grep -i mmc\""
            )
            self._send(msg)

    def _send(self, text: str):
        """Send a Telegram notification."""
        if not self._send_fn or not self._chat_id:
            logger.debug("Notification (no send_fn/chat_id): %s", text[:100])
            return
        try:
            self._send_fn(self._chat_id, text)
        except Exception as e:
            logger.warning("Failed to send notification: %s", e)

    def _background_loop(self):
        """Hourly background check for scheduled notifications."""
        while self._running:
            time.sleep(HOURLY_CHECK_INTERVAL)
            if not self._running:
                break

            # Check if it's time for daily summary
            now = time.localtime()
            today = time.strftime("%Y-%m-%d", now)
            if now.tm_hour == self._daily_summary_hour and today != self._last_daily_date:
                self._last_daily_date = today
                self._send_daily_summary()

    def _send_daily_summary(self):
        """Send daily watch analytics summary."""
        analytics = self.get_watch_analytics(hours=24)
        if not analytics["total_sessions"]:
            return  # Nothing watched, skip summary

        lines = ["📺 PiCast Daily Summary\n"]

        total_mins = int(analytics["total_duration"] / 60)
        hours, mins = divmod(total_mins, 60)
        if hours:
            lines.append(f"Watch time: {hours}h {mins}m")
        else:
            lines.append(f"Watch time: {mins}m")
        lines.append(f"Videos watched: {analytics['total_sessions']}")

        if analytics["top_by_time"]:
            lines.append("\nMost watched:")
            for item in analytics["top_by_time"][:3]:
                dur_mins = int(item["total_duration"] / 60)
                lines.append(f"  • {item['title'][:40]} ({dur_mins}m)")

        if analytics["top_by_count"]:
            lines.append("\nMost repeated:")
            for item in analytics["top_by_count"][:3]:
                lines.append(f"  • {item['title'][:40]} (×{item['count']})")

        # SD health summary
        sd_row = self._db.fetchone(
            "SELECT COUNT(*) as cnt FROM sd_errors WHERE occurred_at >= ?",
            (time.time() - 86400,),
        )
        sd_count = sd_row["cnt"] if sd_row else 0
        if sd_count > 0:
            lines.append(f"\n⚠️ SD errors (24h): {sd_count}")
        else:
            lines.append("\n✅ SD card: healthy")

        self._send("\n".join(lines))

    def get_watch_analytics(self, hours: int = 24) -> dict:
        """Get watch analytics for the given time window.

        Returns dict with total_sessions, total_duration, top_by_time, top_by_count.
        """
        cutoff = time.time() - (hours * 3600)

        total_row = self._db.fetchone(
            "SELECT COUNT(*) as cnt, COALESCE(SUM(duration_watched), 0) as total "
            "FROM watch_sessions WHERE started_at >= ?",
            (cutoff,),
        )

        top_time = self._db.fetchall(
            "SELECT title, SUM(duration_watched) as total_duration "
            "FROM watch_sessions WHERE started_at >= ? "
            "GROUP BY url ORDER BY total_duration DESC LIMIT 5",
            (cutoff,),
        )

        top_count = self._db.fetchall(
            "SELECT title, COUNT(*) as count "
            "FROM watch_sessions WHERE started_at >= ? "
            "GROUP BY url ORDER BY count DESC LIMIT 5",
            (cutoff,),
        )

        return {
            "hours": hours,
            "total_sessions": total_row["cnt"] if total_row else 0,
            "total_duration": total_row["total"] if total_row else 0,
            "top_by_time": [dict(r) for r in top_time],
            "top_by_count": [dict(r) for r in top_count],
        }

    def get_sd_errors(self, hours: int = 1) -> int:
        """Get SD error count for the given window."""
        cutoff = time.time() - (hours * 3600)
        row = self._db.fetchone(
            "SELECT COUNT(*) as cnt FROM sd_errors WHERE occurred_at >= ?",
            (cutoff,),
        )
        return row["cnt"] if row else 0
