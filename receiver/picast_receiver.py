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

__version__ = "0.4.0"

logger = logging.getLogger("picast-receiver")

app = Flask(__name__)

# --- Player State ---

_player_lock = threading.Lock()
_player_proc: subprocess.Popen | None = None
_current_video: dict = {}  # {url, title, started_at}
_autoplay_enabled: bool = True  # Always true for fleet receivers
_mpv_socket = "/tmp/picast-receiver-socket"

# --- Watchdog State ---

_watchdog_enabled = True
_intentional_stop = False
_last_known_url = ""
_last_known_title = ""
_last_known_volume = 100
_retry_count = 0
_last_stable_since = 0.0
_MAX_RETRIES = 5
_BACKOFF = [5, 15, 30, 60, 120]
_CHECK_INTERVAL = 10
_STABLE_RESET = 300  # Reset retry count after 5 min stable

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


def _play_url(url: str, title: str = "", mute: bool = False) -> bool:
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
        "--ytdl-format=best[height<=480]/best",
        "--demuxer-max-bytes=50M",
        "--demuxer-max-back-bytes=20M",
        "--cache=yes",
        "--cache-secs=30",
    ]

    if mute:
        cmd.append("--volume=0")

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
        global _last_known_url, _last_known_title, _intentional_stop
        global _last_stable_since, _retry_count
        _last_known_url = url
        _last_known_title = title
        _intentional_stop = False
        _last_stable_since = time.time()
        _retry_count = 0
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
    global _player_proc, _current_video, _intentional_stop
    _intentional_stop = True
    with _player_lock:
        if _player_proc is not None and _player_proc.poll() is None:
            _player_proc.terminate()
            try:
                _player_proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                _player_proc.kill()
            _player_proc = None
            _current_video = {}


def _mpv_command(command: list) -> dict | None:
    """Send a command to mpv via IPC socket and return the response.

    Returns the parsed JSON response dict on success, or None on failure.
    Response format: {"data": <value>, "error": "success"}
    """
    import socket as sock
    try:
        s = sock.socket(sock.AF_UNIX, sock.SOCK_STREAM)
        s.settimeout(2)
        s.connect(_mpv_socket)
        payload = json.dumps({"command": command}) + "\n"
        s.sendall(payload.encode())
        resp = s.recv(4096)
        s.close()
        return json.loads(resp)
    except (OSError, ConnectionRefusedError, json.JSONDecodeError):
        return None


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
    """Device status for fleet manager.

    When playing, queries mpv IPC for position, duration, paused state,
    and volume level. These fields enable the remote control UI to show
    progress bars and pause/resume button state.
    """
    idle = _is_idle()
    result = {
        "idle": idle,
        "title": _current_video.get("title", "") if not idle else "",
        "url": _current_video.get("url", "") if not idle else "",
        "autoplay_enabled": _autoplay_enabled,
    }
    if not idle:
        pos = _mpv_command(["get_property", "time-pos"])
        dur = _mpv_command(["get_property", "duration"])
        paused = _mpv_command(["get_property", "pause"])
        vol = _mpv_command(["get_property", "volume"])
        if pos:
            result["position"] = pos.get("data", 0)
        if dur:
            result["duration"] = dur.get("data", 0)
        if paused:
            result["paused"] = paused.get("data", False)
        if vol:
            result["volume"] = vol.get("data", 100)
    return jsonify(result)


@app.route("/api/play", methods=["POST"])
def api_play():
    """Play a URL immediately (stops current playback)."""
    data = request.get_json(silent=True) or {}
    url = data.get("url", "").strip()
    title = data.get("title", "").strip()
    mute = data.get("mute", False)

    if not url:
        return jsonify({"ok": False, "error": "url required"}), 400

    success = _play_url(url, title, mute=mute)
    if success:
        return jsonify({"ok": True, "url": url, "title": title})
    return jsonify({"ok": False, "error": "playback failed"}), 500


@app.route("/api/pause", methods=["POST"])
def api_pause():
    """Pause mpv playback via IPC socket."""
    if _is_idle():
        return jsonify({"ok": False, "error": "not playing"})

    result = _mpv_command(["set_property", "pause", True])
    return jsonify({"ok": result is not None})


@app.route("/api/resume", methods=["POST"])
def api_resume():
    """Resume mpv playback via IPC socket."""
    if _is_idle():
        return jsonify({"ok": False, "error": "not playing"})

    result = _mpv_command(["set_property", "pause", False])
    return jsonify({"ok": result is not None})


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


@app.route("/api/volume", methods=["POST"])
def api_volume():
    """Set mpv volume at runtime via IPC socket."""
    data = request.get_json(silent=True) or {}
    level = max(0, min(100, data.get("level", 100)))

    if _is_idle():
        return jsonify({"ok": False, "error": "not playing"})

    result = _mpv_command(["set_property", "volume", level])
    if result is not None:
        global _last_known_volume
        _last_known_volume = level
        return jsonify({"ok": True, "level": level})
    return jsonify({"ok": False, "error": "mpv IPC failed"})


@app.route("/api/stop", methods=["POST"])
def api_stop():
    """Stop current playback."""
    _stop_playback()
    return jsonify({"ok": True})


# --- Watchdog ---


_last_drop_time = 0.0


def _watchdog_loop():
    """Background thread: detect mpv death from OOM and auto-reconnect."""
    global _retry_count, _last_stable_since, _last_drop_time

    while True:
        time.sleep(_CHECK_INTERVAL)
        try:
            if not _watchdog_enabled:
                continue
            if not _last_known_url:
                continue
            if _intentional_stop:
                continue

            with _player_lock:
                alive = (
                    _player_proc is not None
                    and _player_proc.poll() is None
                )

            if alive:
                # Track stability — reset retries after 5 min stable
                if _last_stable_since > 0:
                    stable_secs = time.time() - _last_stable_since
                    if stable_secs >= _STABLE_RESET and _retry_count > 0:
                        logger.info(
                            "Watchdog: stable %.0fs, resetting retry count",
                            stable_secs,
                        )
                        _retry_count = 0
                continue

            # mpv is dead — attempt reconnect
            _last_drop_time = time.time()
            if _retry_count >= _MAX_RETRIES:
                logger.warning(
                    "Watchdog: max retries (%d) reached, giving up on %s",
                    _MAX_RETRIES, _last_known_url,
                )
                continue

            backoff = _BACKOFF[min(_retry_count, len(_BACKOFF) - 1)]
            _retry_count += 1
            logger.warning(
                "Watchdog: mpv died, retry %d/%d in %ds — %s",
                _retry_count, _MAX_RETRIES, backoff,
                _last_known_title or _last_known_url,
            )
            time.sleep(backoff)

            # Re-check — might have been intentionally stopped during backoff
            if _intentional_stop or not _watchdog_enabled:
                continue

            success = _play_url(_last_known_url, _last_known_title)
            if success and _last_known_volume != 100:
                # Wait for mpv IPC socket to come up
                time.sleep(2)
                _mpv_command(["set_property", "volume", _last_known_volume])
                logger.info(
                    "Watchdog: restored volume to %d", _last_known_volume
                )

        except Exception:
            logger.exception("Watchdog: unexpected error (continuing)")


@app.route("/api/watchdog", methods=["GET"])
def api_watchdog_status():
    """Watchdog status for monitoring."""
    return jsonify({
        "enabled": _watchdog_enabled,
        "retry_count": _retry_count,
        "max_retries": _MAX_RETRIES,
        "last_url": _last_known_url,
        "last_drop_time": _last_drop_time,
    })


@app.route("/api/watchdog", methods=["POST"])
def api_watchdog_toggle():
    """Enable or disable watchdog."""
    global _watchdog_enabled
    data = request.get_json(silent=True) or {}
    _watchdog_enabled = bool(data.get("enabled", True))
    logger.info("Watchdog %s", "enabled" if _watchdog_enabled else "disabled")
    return jsonify({"ok": True, "enabled": _watchdog_enabled})


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

    # Start watchdog thread
    wd = threading.Thread(target=_watchdog_loop, daemon=True, name="watchdog")
    wd.start()
    logger.info("Watchdog thread started (interval=%ds)", _CHECK_INTERVAL)

    app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
