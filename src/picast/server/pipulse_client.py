"""PiPulse API client for fetching block metadata."""

import json
import logging
import urllib.request
import urllib.error

logger = logging.getLogger(__name__)


def fetch_block_metadata(host: str, port: int, timeout: int = 2) -> dict | None:
    """Fetch block metadata from PiPulse API.

    Returns dict of block_name -> metadata, or None on any failure.
    """
    url = f"http://{host}:{port}/api/pitim/blocks"
    try:
        req = urllib.request.urlopen(url, timeout=timeout)
        data = json.loads(req.read())
        return data.get("blocks", {})
    except Exception as e:
        logger.warning("PiPulse fetch failed (%s): %s", url, e)
        return None
