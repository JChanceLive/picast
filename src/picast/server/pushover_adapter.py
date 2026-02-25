"""Pushover notification adapter for PiCast.

Provides a send_fn(chat_id, text) callable that routes notifications
to Pushover, matching the interface expected by NotificationManager.
"""

import logging
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)

PUSHOVER_API_URL = "https://api.pushover.net/1/messages.json"


class SoundTier:
    """Pushover sound tiers for notification severity."""
    CASUAL   = {"sound": "classical", "priority": 0}
    ROUTINE  = {"sound": "gamelan",   "priority": 0}
    MEDIUM   = {"sound": "pushover",  "priority": 1}
    ALERT    = {"sound": "falling",   "priority": 1}
    URGENT   = {"sound": "siren",     "priority": 2}
    CRITICAL = {"sound": "alien",     "priority": 2}


def create_pushover_send_fn(api_token: str, user_key: str):
    """Create a send_fn compatible with NotificationManager.

    Routes SD card alerts with priority 1 (high) and
    everything else with priority 0 (normal).

    Args:
        api_token: Pushover application API token
        user_key: Pushover user key

    Returns:
        Callable(chat_id, text) that posts to Pushover. chat_id is ignored.
    """

    def send_fn(chat_id, text: str):
        is_alert = "SD Card Alert" in text
        tier = SoundTier.ALERT if is_alert else SoundTier.CASUAL
        title = "PiCast SD Alert" if is_alert else "PiCast"

        params = {
            "token": api_token,
            "user": user_key,
            "message": text,
            "title": title,
            "priority": tier["priority"],
            "sound": tier["sound"],
        }
        # Emergency priority requires retry/expire
        if tier["priority"] == 2:
            params["retry"] = 120
            params["expire"] = 3600

        data = urllib.parse.urlencode(params).encode("utf-8")
        req = urllib.request.Request(PUSHOVER_API_URL, data=data)
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                resp.read()
        except Exception as e:
            logger.warning("Failed to send Pushover notification: %s", e)

    return send_fn
