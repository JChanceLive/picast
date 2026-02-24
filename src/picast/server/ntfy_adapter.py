"""ntfy.sh notification adapter for PiCast.

Provides a send_fn(chat_id, text) callable that routes notifications
to an ntfy server, matching the interface expected by NotificationManager.
"""

import logging
import urllib.request

logger = logging.getLogger(__name__)


def create_ntfy_send_fn(
    server_url: str,
    alert_topic: str = "picast-alerts",
    summary_topic: str = "picast-health",
):
    """Create a send_fn compatible with NotificationManager.

    Routes SD card alerts to alert_topic (high priority) and
    everything else to summary_topic (default priority).

    Args:
        server_url: ntfy server base URL (e.g. "http://10.0.0.103:5555")
        alert_topic: Topic for SD card alerts (priority 4/high)
        summary_topic: Topic for daily summaries (priority 3/default)

    Returns:
        Callable(chat_id, text) that posts to ntfy. chat_id is ignored.
    """
    base = server_url.rstrip("/")

    def send_fn(chat_id, text: str):
        is_alert = "SD Card Alert" in text
        topic = alert_topic if is_alert else summary_topic
        priority = "4" if is_alert else "3"
        title = "PiCast SD Alert" if is_alert else "PiCast"
        tags = "warning" if is_alert else "tv"

        url = f"{base}/{topic}"
        req = urllib.request.Request(
            url,
            data=text.encode("utf-8"),
            headers={
                "Title": title,
                "Priority": priority,
                "Tags": tags,
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                resp.read()
        except Exception as e:
            logger.warning("Failed to send ntfy notification: %s", e)

    return send_fn
