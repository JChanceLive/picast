"""Flask Blueprint factory for the PiCast receiver.

Composes a PlayerAdapter (mandatory) and a ReceiverWatchdog (optional)
into the standard receiver HTTP surface. Both picast-z1 (the original
host) and StarScreen (the OPi5+ wall display) consume this blueprint.

Endpoints (preserved from receiver v0.8.0):
    GET  /api/health
    GET  /api/status
    POST /api/play
    POST /api/pause
    POST /api/resume
    POST /api/queue/add        (== /api/play in receiver — no real queue)
    POST /api/volume
    POST /api/stop
    GET  /api/watchdog
    POST /api/watchdog
"""

from __future__ import annotations

import logging
import os

from flask import Blueprint, jsonify, request


logger = logging.getLogger("picast-receiver.blueprint")


def create_receiver_blueprint(
    player,
    *,
    watchdog=None,
    version: str = "0.9.0",
    device_type: str = "receiver",
    name: str = "picast_receiver",
    include_health: bool = True,
) -> Blueprint:
    """Build a Flask Blueprint exposing the receiver protocol.

    Args:
        player: Anything that satisfies PlayerAdapter.
        watchdog: Optional ReceiverWatchdog. If None, /api/watchdog
                  routes return a stub status with enabled=False.
        version: Reported in /api/health.
        device_type: Reported in /api/health (e.g. "receiver",
                     "starscreen").
        name: Blueprint name. Override if multiple blueprints from
              the same factory share an app.
        include_health: When False, omit the /api/health route so a
                        host app (e.g. StarScreen) can keep its own
                        enriched health endpoint.
    """
    bp = Blueprint(name, __name__)

    if include_health:
        @bp.route("/api/health", methods=["GET"])
        def api_health():
            return jsonify({
                "ok": True,
                "version": version,
                "hostname": os.uname().nodename,
                "type": device_type,
            })

    @bp.route("/api/status", methods=["GET"])
    def api_status():
        result = player.status()
        # Always include autoplay_enabled — fleet receivers report True.
        result.setdefault("autoplay_enabled", True)
        return jsonify(result)

    @bp.route("/api/play", methods=["POST"])
    def api_play():
        data = request.get_json(silent=True) or {}
        url = (data.get("url") or "").strip()
        title = (data.get("title") or "").strip()
        mute = bool(data.get("mute", False))
        if not url:
            return jsonify({"ok": False, "error": "url required"}), 400
        # Forward optional extras (audio_url, codec, ...) PiPulse sends
        # for resolved devices. Players that don't care can ignore them.
        extras = {k: v for k, v in data.items() if k not in {"url", "title", "mute"}}
        if player.play(url, title, mute=mute, **extras):
            return jsonify({"ok": True, "url": url, "title": title})
        return jsonify({"ok": False, "error": "playback failed"}), 500

    @bp.route("/api/pause", methods=["POST"])
    def api_pause():
        if player.is_idle():
            return jsonify({"ok": False, "error": "not playing"})
        return jsonify({"ok": player.pause()})

    @bp.route("/api/resume", methods=["POST"])
    def api_resume():
        if player.is_idle():
            return jsonify({"ok": False, "error": "not playing"})
        return jsonify({"ok": player.resume()})

    @bp.route("/api/queue/add", methods=["POST"])
    def api_queue_add():
        # Thin receiver has no queue; "add" is "play now".
        data = request.get_json(silent=True) or {}
        url = (data.get("url") or "").strip()
        title = (data.get("title") or "").strip()
        if not url:
            return jsonify({"ok": False, "error": "url required"}), 400
        if player.play(url, title):
            return jsonify({"ok": True, "url": url, "title": title})
        return jsonify({"ok": False, "error": "playback failed"}), 500

    @bp.route("/api/volume", methods=["POST"])
    def api_volume():
        data = request.get_json(silent=True) or {}
        level = max(0, min(100, int(data.get("level", 100))))
        if player.is_idle():
            return jsonify({"ok": False, "error": "not playing"})
        if player.set_volume(level):
            return jsonify({"ok": True, "level": level})
        return jsonify({"ok": False, "error": "mpv IPC failed"})

    @bp.route("/api/stop", methods=["POST"])
    def api_stop():
        player.stop()
        return jsonify({"ok": True})

    @bp.route("/api/watchdog", methods=["GET"])
    def api_watchdog_status():
        if watchdog is None:
            return jsonify({
                "enabled": False,
                "retry_count": 0,
                "max_retries": 0,
                "last_url": player.last_url,
                "last_drop_time": 0.0,
                "stall_count": 0,
            })
        return jsonify(watchdog.status())

    @bp.route("/api/watchdog", methods=["POST"])
    def api_watchdog_toggle():
        if watchdog is None:
            return jsonify({"ok": False, "error": "watchdog not configured"})
        data = request.get_json(silent=True) or {}
        watchdog.set_enabled(bool(data.get("enabled", True)))
        return jsonify({"ok": True, "enabled": watchdog.enabled})

    return bp
