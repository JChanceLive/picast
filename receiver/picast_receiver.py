"""PiCast Receiver — minimal fleet display endpoint.

A thin Flask service for Pi Zero 2 W that accepts video URLs from the
fleet manager and plays them via mpv. No database, no queue management,
no autoplay system — just receive and play.

Usage:
    python picast_receiver.py [--port 5050] [--host 0.0.0.0]
"""

import argparse
import json
import logging
import os
import signal
import subprocess
import threading
import time

from flask import Flask, jsonify, request

__version__ = "0.1.0"

logger = logging.getLogger("picast-receiver")

app = Flask(__name__)

# --- Player State ---

_player_lock = threading.Lock()
_player_proc: subprocess.Popen | None = None
_current_video: dict = {}  # {url, title, started_at}
_autoplay_enabled: bool = True  # Always true for fleet receivers
_mpv_socket = "/tmp/picast-receiver-socket"

# Wayland environment for systemd services
_WAYLAND_ENV = {
    **os.environ,
    "XDG_RUNTIME_DIR": os.environ.get("XDG_RUNTIME_DIR", "/run/user/1000"),
    "WAYLAND_DISPLAY": os.environ.get("WAYLAND_DISPLAY", "wayland-0"),
}


def _is_idle() -> bool:
    """Check if mpv is idle (no video playing)."""
    with _player_lock:
        if _player_proc is None:
            return True
        if _player_proc.poll() is not None:
            return True
    return False


def _play_url(url: str, title: str = "") -> bool:
    """Play a URL via mpv. Stops any current playback first."""
    global _player_proc, _current_video

    _stop_playback()

    cmd = [
        "mpv",
        "--no-terminal",
        "--video-sync=display-desync",
        "--hwdec=auto",
        "--fullscreen",
        f"--input-ipc-server={_mpv_socket}",
        "--ytdl-format=bestvideo[height<=720]+bestaudio/best[height<=720]",
    ]

    if title:
        cmd.extend([
            "--osd-level=3",
            f"--osd-status-msg={title}",
            "--osd-align-x=left",
            "--osd-align-y=bottom",
            "--osd-margin-x=20",
            "--osd-margin-y=20",
        ])

    cmd.append(url)

    try:
        with _player_lock:
            _player_proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=_WAYLAND_ENV,
            )
            _current_video = {
                "url": url,
                "title": title,
                "started_at": time.time(),
            }
        logger.info("Playing: %s (%s)", title or "untitled", url)
        return True
    except FileNotFoundError:
        logger.error("mpv not found — is it installed?")
        return False
    except OSError as e:
        logger.error("Failed to start mpv: %s", e)
        return False


def _stop_playback():
    """Stop current mpv playback if any."""
    global _player_proc, _current_video
    with _player_lock:
        if _player_proc is not None and _player_proc.poll() is None:
            _player_proc.terminate()
            try:
                _player_proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                _player_proc.kill()
            _player_proc = None
            _current_video = {}


# --- API Routes ---


@app.route("/api/health", methods=["GET"])
def api_health():
    """Health check for fleet manager polling."""
    return jsonify({
        "ok": True,
        "version": __version__,
        "hostname": os.uname().nodename,
        "type": "receiver",
    })


@app.route("/api/status", methods=["GET"])
def api_status():
    """Device status for fleet manager."""
    idle = _is_idle()
    return jsonify({
        "idle": idle,
        "title": _current_video.get("title", "") if not idle else "",
        "url": _current_video.get("url", "") if not idle else "",
        "autoplay_enabled": _autoplay_enabled,
    })


@app.route("/api/play", methods=["POST"])
def api_play():
    """Play a URL immediately (stops current playback)."""
    data = request.get_json(silent=True) or {}
    url = data.get("url", "").strip()
    title = data.get("title", "").strip()

    if not url:
        return jsonify({"ok": False, "error": "url required"}), 400

    success = _play_url(url, title)
    if success:
        return jsonify({"ok": True, "url": url, "title": title})
    return jsonify({"ok": False, "error": "playback failed"}), 500


@app.route("/api/queue/add", methods=["POST"])
def api_queue_add():
    """Accept a video from fleet manager.

    Since this is a thin receiver with no queue, this is equivalent
    to /api/play — it plays immediately. The fleet manager treats
    this as "push content to device".
    """
    data = request.get_json(silent=True) or {}
    url = data.get("url", "").strip()
    title = data.get("title", "").strip()

    if not url:
        return jsonify({"ok": False, "error": "url required"}), 400

    success = _play_url(url, title)
    if success:
        return jsonify({"ok": True, "url": url, "title": title})
    return jsonify({"ok": False, "error": "playback failed"}), 500


@app.route("/api/stop", methods=["POST"])
def api_stop():
    """Stop current playback."""
    _stop_playback()
    return jsonify({"ok": True})


# --- Main ---


def _handle_shutdown(signum, frame):
    """Clean shutdown on SIGTERM/SIGINT."""
    logger.info("Shutting down (signal %d)...", signum)
    _stop_playback()
    raise SystemExit(0)


def main():
    parser = argparse.ArgumentParser(description="PiCast Receiver")
    parser.add_argument("--port", type=int, default=5050)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    signal.signal(signal.SIGTERM, _handle_shutdown)
    signal.signal(signal.SIGINT, _handle_shutdown)

    logger.info(
        "PiCast Receiver v%s starting on %s:%d",
        __version__, args.host, args.port,
    )
    app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
