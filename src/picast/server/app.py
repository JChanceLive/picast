"""Flask REST API for PiCast.

Provides HTTP endpoints for player control, queue management, and status.
This is the single source of truth - both the TUI and web UI talk to this.
"""

import json
import logging
import os
import random
import re
import shutil
import subprocess
import time

from flask import Flask, Response, jsonify, redirect, render_template, request

from picast.config import AutoplayConfig, ServerConfig, ThemeConfig
from picast.server.autoplay_pool import AutoPlayPool, extract_video_id
from picast.server.database import Database
from picast.server.discovery import DeviceRegistry
from picast.server.events import EventBus
from picast.server.library import Library
from picast.server.mpv_client import MPVClient
from picast.server.player import Player
from picast.server.queue_manager import QueueManager
from picast.server.catalog import CATEGORIES, CATALOG, get_series_by_category, get_series_by_id
from picast.server.sources import ArchiveSource, LocalSource, SourceRegistry, TwitchSource, YouTubeSource
from picast.server.youtube_discovery import DiscoveryAgent

logger = logging.getLogger(__name__)

_YT_VIDEO_ID_RE = re.compile(r'^[a-zA-Z0-9_-]{11}$')
_YT_PLAYLIST_ID_RE = re.compile(r'^(PL|UU|FL|OL|RD|LL)[a-zA-Z0-9_-]+$')


def _normalize_youtube_input(raw: str) -> str:
    """Expand bare YouTube video/playlist IDs to full URLs."""
    raw = raw.strip()
    if raw.startswith(("http://", "https://", "/", "file://")):
        return raw  # Already a URL
    if _YT_VIDEO_ID_RE.match(raw):
        return f"https://www.youtube.com/watch?v={raw}"
    if _YT_PLAYLIST_ID_RE.match(raw):
        return f"https://www.youtube.com/playlist?list={raw}"
    return raw  # Pass through, let validation handle it


def create_app(
    config: ServerConfig | None = None,
    devices: list | None = None,
    autoplay_config: AutoplayConfig | None = None,
) -> Flask:
    """Create and configure the Flask application.

    Args:
        config: Server configuration. Uses defaults if None.
        devices: List of (name, host, port) tuples for known devices.
        autoplay_config: Autoplay schedule configuration.
    """
    if config is None:
        config = ServerConfig()

    # Ensure data directory exists
    os.makedirs(config.data_dir, exist_ok=True)

    template_dir = os.path.join(os.path.dirname(__file__), "templates")
    static_dir = os.path.join(os.path.dirname(__file__), "static")
    app = Flask(__name__, template_folder=template_dir, static_folder=static_dir)
    app.config["PICAST"] = config

    # Inject version into all templates for cache-busting
    from picast.__about__ import __version__ as _app_version

    @app.context_processor
    def inject_version():
        return {"version": _app_version}

    # Initialize components
    mpv = MPVClient(config.mpv_socket)
    db = Database(config.db_file)
    queue = QueueManager(db)
    library = Library(db)

    # One-time migration: import queue.json if it exists and queue table is empty
    json_queue_path = os.path.join(config.data_dir, "queue.json")
    if os.path.exists(json_queue_path) and not queue.get_all():
        try:
            with open(json_queue_path, "r") as f:
                data = json.load(f)
            for item_data in data.get("items", []):
                qi = queue.add(item_data.get("url", ""), item_data.get("title", ""))
                status = item_data.get("status", "pending")
                if status == "played":
                    queue.mark_played(qi.id)
                elif status == "skipped":
                    queue.mark_skipped(qi.id)
                elif status == "playing":
                    queue.mark_pending(qi.id)
            migrated_path = json_queue_path + ".migrated"
            os.rename(json_queue_path, migrated_path)
            logger.info("Migrated queue.json (%d items) to SQLite", len(data.get("items", [])))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to migrate queue.json: %s", e)

    # Event bus for SSE push notifications
    event_bus = EventBus(db)

    # Source registry
    sources = SourceRegistry()
    sources.register(YouTubeSource(config.ytdl_format, config=config))
    sources.register(LocalSource())
    sources.register(TwitchSource())
    sources.register(ArchiveSource())

    # Device registry
    device_registry = DeviceRegistry(local_port=config.port)
    for name, host, port in (devices or []):
        device_registry.add_from_config(name, host, port)

    player = Player(
        mpv, queue, config.ytdl_format, config.ytdl_format_live,
        library=library, config=config, event_bus=event_bus,
    )

    # Start the player loop
    player.start()

    # Close DB connections after each request to prevent fd leaks
    @app.teardown_appcontext
    def close_db(exc):
        db.close()

    # Global JSON error handler — prevents bare HTML 500s
    @app.errorhandler(Exception)
    def handle_exception(e):
        from werkzeug.exceptions import HTTPException
        if isinstance(e, HTTPException):
            return e  # Let Flask handle normal HTTP errors (400, 404, etc.)
        logger.exception("Unhandled error: %s", e)
        return jsonify({"error": str(e)}), 500

    # Allow cross-origin requests (Chrome extension, etc.)
    @app.after_request
    def add_cors_headers(response):
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type"
        return response

    # Store on app for access in routes
    app.mpv = mpv
    app.queue = queue
    app.db = db
    app.library = library
    app.sources = sources
    app.player = player
    app.event_bus = event_bus
    app.device_registry = device_registry

    # Autoplay state (ephemeral, like loop_enabled)
    _autoplay_config = autoplay_config or AutoplayConfig()
    _autoplay_enabled = _autoplay_config.enabled
    _autoplay_pool = AutoPlayPool(
        db,
        avoid_recent=_autoplay_config.avoid_recent,
        cross_block_learning=_autoplay_config.cross_block_learning,
    )
    _autoplay_current = {"video_id": None, "block_name": None, "title": None}
    _autoplay_start_time = {"value": None}  # monotonic() when autoplay video started
    _autoplay_completing = {"value": None}  # snapshot for deferred completion processing

    def _snapshot_autoplay_for_completion(reason: str):
        """Snapshot current autoplay context and clear it. Used by endpoints."""
        if _autoplay_current["video_id"]:
            _autoplay_completing["value"] = {
                "video_id": _autoplay_current["video_id"],
                "block_name": _autoplay_current["block_name"],
                "start_time": _autoplay_start_time["value"],
                "stop_reason": reason,
            }
            _autoplay_current["video_id"] = None
            _autoplay_current["block_name"] = None
            _autoplay_current["title"] = None
            _autoplay_start_time["value"] = None

    def _handle_item_complete(item, play_duration, was_skipped, was_stopped):
        """Process autoplay completion with context-aware self-learning."""
        # Determine context: endpoint snapshot (skip/stop/play/trigger) or natural end
        ctx = _autoplay_completing["value"]
        if ctx:
            _autoplay_completing["value"] = None
            video_id = ctx["video_id"]
            block_name = ctx["block_name"]
            stop_reason = ctx["stop_reason"]
        elif _autoplay_current["video_id"]:
            # Natural completion (no endpoint intervention)
            # Guard: verify the completing item matches current autoplay video.
            # play_now() skip can cause the old item's callback to fire after
            # _autoplay_current was already set for the NEW video (race condition).
            item_vid = extract_video_id(item.url) if item else None
            if item_vid and item_vid != _autoplay_current["video_id"]:
                # Old item completing — not the current autoplay video
                return
            video_id = _autoplay_current["video_id"]
            block_name = _autoplay_current["block_name"]
            stop_reason = "completed"
            _autoplay_current["video_id"] = None
            _autoplay_current["block_name"] = None
            _autoplay_current["title"] = None
            _autoplay_start_time["value"] = None
        else:
            # Not an autoplay video
            return

        # Determine if video was completed (natural end or >80% of known duration)
        video_data = _autoplay_pool.get_video(block_name, video_id)
        known_duration = video_data.get("duration", 0) if video_data else 0
        is_completed = (
            stop_reason == "completed"
            or (known_duration > 0 and play_duration >= known_duration * 0.8)
        )

        if is_completed:
            stop_reason = "completed"

        # Update history with watch data
        duration_watched = int(play_duration)
        _autoplay_pool.update_last_history(
            video_id, block_name,
            duration_watched=duration_watched,
            completed=1 if is_completed else 0,
            stop_reason=stop_reason,
        )

        # Apply implicit rating
        if is_completed:
            _autoplay_pool.record_completion(block_name, video_id)
            logger.info("AutoPlay self-learn: completed %s in %s (%ds)", video_id, block_name, duration_watched)
        elif stop_reason == "user_skip":
            _autoplay_pool.record_skip(block_name, video_id)
            logger.info("AutoPlay self-learn: skipped %s in %s after %ds", video_id, block_name, duration_watched)
        # block_transition, manual_override, user_stop: no implicit rating change

    player.on_item_complete = _handle_item_complete

    # Auto-seed pool from legacy mappings on first run
    if _autoplay_config.pool_mode and _autoplay_config.mappings:
        seeded = _autoplay_pool.seed_from_mappings(_autoplay_config.mappings)
        if seeded:
            logger.info("AutoPlay pool: seeded %d videos from legacy mappings", seeded)

    # Discovery agent for pool enrichment
    _discovery_agent = DiscoveryAgent(
        pool=_autoplay_pool,
        server_config=config,
        delay=_autoplay_config.discovery_delay,
    )
    _discovery_themes = _autoplay_config.themes

    # --- Web UI Pages ---

    @app.route("/")
    def web_queue():
        return render_template(
            "player.html", active="queue",
            devices=device_registry.list_devices(),
        )

    @app.route("/history")
    def web_history():
        return render_template(
            "history.html", active="history",
            devices=device_registry.list_devices(),
        )

    @app.route("/collections")
    def web_collections():
        return render_template(
            "collections.html", active="collections",
            devices=device_registry.list_devices(),
        )

    @app.route("/catalog")
    def web_catalog():
        return render_template(
            "catalog.html", active="catalog",
            devices=device_registry.list_devices(),
        )

    @app.route("/pool")
    def web_pool():
        return render_template(
            "pool.html", active="pool",
            devices=device_registry.list_devices(),
        )

    @app.route("/settings")
    def web_settings():
        return render_template(
            "settings.html", active="settings",
            devices=device_registry.list_devices(),
        )

    @app.route("/add-to-collection")
    def web_add_to_collection():
        url = request.args.get("url", "")
        title = request.args.get("title", "")
        source_type = request.args.get("source_type", "youtube")
        playlists_list = library.list_playlists()
        return render_template(
            "add_to_collection.html", active="queue",
            url=url, title=title, source_type=source_type,
            playlists=playlists_list,
        )

    @app.route("/add-to-collection/fav", methods=["POST"])
    def web_add_to_fav():
        url = request.form.get("url", "")
        title = request.form.get("title", "")
        source_type = request.form.get("source_type", "youtube")
        entry = library.add(url, title, source_type)
        if not entry.get("favorite"):
            library.toggle_favorite(entry["id"])
        return redirect("/")

    @app.route("/add-to-collection/<int:playlist_id>", methods=["POST"])
    def web_add_to_playlist(playlist_id):
        url = request.form.get("url", "")
        title = request.form.get("title", "")
        source_type = request.form.get("source_type", "youtube")
        entry = library.add(url, title, source_type)
        library.add_to_playlist(playlist_id, entry["id"])
        return redirect("/")

    # Redirects from old UI routes
    app.add_url_rule("/library", "web_library_redirect",
                     lambda: redirect("/history", code=301))
    app.add_url_rule("/playlists", "web_playlists_redirect",
                     lambda: redirect("/collections", code=301))

    # --- Player Control Endpoints ---

    @app.route("/api/status")
    def status():
        """Get full player status."""
        nonlocal _autoplay_enabled
        s = player.get_status()
        s["autoplay_enabled"] = _autoplay_enabled
        s["autoplay_current"] = _autoplay_current
        return jsonify(s)

    @app.route("/api/play", methods=["POST"])
    def play():
        """Play a URL immediately."""
        data = request.get_json(silent=True) or {}
        url = data.get("url", "").strip()
        if not url:
            return jsonify({"error": "url required"}), 400
        url = _normalize_youtube_input(url)
        title = data.get("title", "")
        start_time = float(data.get("start_time", 0) or 0)
        if start_time > 0:
            logger.info("Play request with start_time=%ds for %s", start_time, url)
        # Snapshot autoplay context before clearing (manual override)
        _snapshot_autoplay_for_completion("manual_override")
        try:
            player.play_now(url, title, start_time=start_time)
        except Exception as e:
            logger.exception("Play failed for %s: %s", url, e)
            return jsonify({"error": f"Play failed: {e}"}), 500
        return jsonify({"ok": True, "message": f"Playing: {url}"})

    @app.route("/api/pause", methods=["POST"])
    def pause():
        ok = mpv.pause()
        return jsonify({"ok": ok})

    @app.route("/api/resume", methods=["POST"])
    def resume():
        ok = mpv.resume()
        return jsonify({"ok": ok})

    @app.route("/api/toggle", methods=["POST"])
    def toggle():
        ok = mpv.toggle_pause()
        return jsonify({"ok": ok})

    @app.route("/api/skip", methods=["POST"])
    def skip():
        # Snapshot autoplay context before skip (user_skip)
        _snapshot_autoplay_for_completion("user_skip")
        player.skip()
        return jsonify({"ok": True})

    @app.route("/api/stop", methods=["POST"])
    def stop():
        # Snapshot autoplay context before stop (user_stop)
        _snapshot_autoplay_for_completion("user_stop")
        player.stop_playback()
        return jsonify({"ok": True})

    @app.route("/api/resume-queue", methods=["POST"])
    def resume_queue():
        """Resume queue processing after stop."""
        player.resume_playback()
        return jsonify({"ok": True})

    @app.route("/api/seek", methods=["POST"])
    def seek():
        data = request.get_json(silent=True) or {}
        position = data.get("position")
        if position is None:
            return jsonify({"error": "position required"}), 400
        mode = data.get("mode", "absolute")
        ok = mpv.seek(float(position), mode)
        return jsonify({"ok": ok})

    @app.route("/api/volume", methods=["POST"])
    def volume():
        data = request.get_json(silent=True) or {}
        level = data.get("level")
        if level is None:
            return jsonify({"error": "level required"}), 400
        level = max(0, min(100, int(level)))
        ok = mpv.set_volume(level)
        db.set_setting("volume", str(level))
        return jsonify({"ok": ok})

    @app.route("/api/speed", methods=["POST"])
    def speed():
        data = request.get_json(silent=True) or {}
        spd = data.get("speed")
        if spd is None:
            return jsonify({"error": "speed required"}), 400
        ok = mpv.set_speed(float(spd))
        return jsonify({"ok": ok})

    # --- Queue Endpoints ---

    @app.route("/api/queue")
    def get_queue():
        items = queue.get_all()
        result = []
        for item in items:
            d = item.to_dict()
            lib_entry = library.get_by_url(item.url)
            d["watch_count"] = lib_entry["play_count"] if lib_entry else 0
            result.append(d)
        return jsonify(result)

    @app.route("/api/queue/add", methods=["POST"])
    def queue_add():
        data = request.get_json(silent=True) or {}
        url = data.get("url", "").strip()
        if not url:
            return jsonify({"error": "url required"}), 400
        # Normalize bare video IDs to full YouTube URLs
        url = _normalize_youtube_input(url)
        # Validate URL before queueing
        try:
            valid, error = sources.validate_url(url)
        except Exception as e:
            logger.exception("URL validation failed for %s: %s", url, e)
            return jsonify({"error": f"Validation failed: {e}"}), 500
        if not valid:
            return jsonify({"error": error}), 400
        title = data.get("title", "")
        # Use source registry for better detection
        if not title:
            try:
                meta = sources.get_metadata(url)
                if meta and meta.title:
                    title = meta.title
            except Exception:
                pass  # Non-critical — proceed without title
        try:
            item = queue.add(url, title)
        except Exception as e:
            logger.exception("Queue add failed for %s: %s", url, e)
            return jsonify({"error": f"Queue add failed: {e}"}), 500
        return jsonify(item.to_dict()), 201

    @app.route("/api/queue/<int:item_id>/play", methods=["POST"])
    def queue_play_item(item_id):
        """Play an existing queue item immediately by its ID."""
        try:
            player.play_item_now(item_id)
        except ValueError as e:
            return jsonify({"error": str(e)}), 404
        except Exception as e:
            logger.exception("Play item failed for %d: %s", item_id, e)
            return jsonify({"error": f"Play failed: {e}"}), 500
        return jsonify({"ok": True})

    @app.route("/api/queue/<int:item_id>", methods=["DELETE"])
    def queue_remove(item_id):
        ok = queue.remove(item_id)
        if not ok:
            return jsonify({"error": "item not found"}), 404
        return jsonify({"ok": True})

    @app.route("/api/queue/reorder", methods=["POST"])
    def queue_reorder():
        data = request.get_json(silent=True) or {}
        items = data.get("item_ids") or data.get("items") or []
        if not items:
            return jsonify({"error": "items required (list of IDs)"}), 400
        queue.reorder(items)
        return jsonify({"ok": True})

    @app.route("/api/queue/replay", methods=["POST"])
    def queue_replay():
        """Re-queue a played/skipped item at the end of the pending queue."""
        data = request.get_json(silent=True) or {}
        item_id = data.get("id")
        if item_id is None:
            return jsonify({"error": "id required"}), 400
        ok = queue.replay(item_id)
        if not ok:
            return jsonify({"error": "item not found"}), 404
        return jsonify({"ok": True})

    @app.route("/api/queue/clear-played", methods=["POST"])
    def queue_clear_played():
        queue.clear_played()
        return jsonify({"ok": True})

    @app.route("/api/queue/clear", methods=["POST"])
    def queue_clear():
        queue.clear_all()
        return jsonify({"ok": True})

    @app.route("/api/queue/loop", methods=["POST"])
    def queue_loop_toggle():
        """Toggle queue loop mode."""
        data = request.get_json(silent=True) or {}
        enabled = data.get("enabled", True)
        player.set_loop(bool(enabled))
        return jsonify({"ok": True, "loop_enabled": bool(enabled)})

    @app.route("/api/queue/import-playlist", methods=["POST"])
    def queue_import_playlist():
        """Import all videos from a YouTube playlist into the queue."""
        data = request.get_json(silent=True) or {}
        url = data.get("url", "").strip()
        if not url:
            return jsonify({"error": "url required"}), 400
        url = _normalize_youtube_input(url)

        yt_handler = sources.get_handler("youtube")
        if not yt_handler or not hasattr(yt_handler, "is_playlist"):
            return jsonify({"error": "YouTube source not available"}), 500

        if not yt_handler.is_playlist(url):
            return jsonify({"error": "URL is not a playlist"}), 400

        playlist_title, items = yt_handler.extract_playlist(url)
        if not items:
            return jsonify({"error": "No videos found in playlist"}), 404

        added = 0
        failed = 0
        for video_url, title in items:
            try:
                queue.add(video_url, title)
                added += 1
            except Exception:
                failed += 1

        return jsonify({"ok": True, "added": added, "failed": failed})

    @app.route("/api/playlists/import-playlist", methods=["POST"])
    def playlists_import_playlist():
        """Import a YouTube playlist as a Collection."""
        data = request.get_json(silent=True) or {}
        url = data.get("url", "")
        if not url:
            return jsonify({"error": "url required"}), 400

        yt_handler = sources.get_handler("youtube")
        if not yt_handler or not hasattr(yt_handler, "is_playlist"):
            return jsonify({"error": "YouTube source not available"}), 500

        if not yt_handler.is_playlist(url):
            return jsonify({"error": "URL is not a playlist"}), 400

        playlist_title, items = yt_handler.extract_playlist(url)
        if not items:
            return jsonify({"error": "No videos found in playlist"}), 404

        # Use playlist title or fall back to generic name
        collection_name = playlist_title or data.get("name", "Imported Playlist")
        try:
            pl = library.create_playlist(collection_name)
        except Exception:
            # Name conflict - append count
            collection_name = f"{collection_name} ({len(items)} videos)"
            try:
                pl = library.create_playlist(collection_name)
            except Exception:
                return jsonify({"error": "Could not create collection (name already exists)"}), 409

        added = 0
        failed = 0
        for video_url, title in items:
            try:
                entry = library.add(video_url, title, "youtube")
                library.add_to_playlist(pl["id"], entry["id"])
                added += 1
            except Exception:
                failed += 1

        return jsonify({
            "ok": True,
            "added": added,
            "failed": failed,
            "collection_id": pl["id"],
            "collection_name": collection_name,
        })

    # --- Timer Endpoints ---

    @app.route("/api/timer")
    def timer_get():
        """Get current timer state."""
        return jsonify(player.get_timer_state())

    @app.route("/api/timer/stop-after-current", methods=["POST"])
    def timer_stop_after_current():
        """Toggle stop-after-current-video."""
        data = request.get_json(silent=True) or {}
        enabled = data.get("enabled", True)
        player.set_stop_after_current(bool(enabled))
        return jsonify({"ok": True, "stop_after_current": bool(enabled)})

    @app.route("/api/timer/stop-in", methods=["POST"])
    def timer_stop_in():
        """Set a sleep timer in minutes. 0 = cancel."""
        data = request.get_json(silent=True) or {}
        minutes = data.get("minutes")
        if minutes is None:
            return jsonify({"error": "minutes required"}), 400
        minutes = int(minutes)
        if minutes < 0:
            return jsonify({"error": "minutes must be >= 0"}), 400
        player.set_stop_timer(minutes)
        return jsonify({"ok": True, "minutes": minutes})

    # --- AutoPlay Schedule Endpoints ---

    @app.route("/api/autoplay")
    def autoplay_status():
        """Get autoplay config and state."""
        nonlocal _autoplay_enabled
        resp = {
            "enabled": _autoplay_enabled,
            "pool_mode": _autoplay_config.pool_mode,
            "avoid_recent": _autoplay_config.avoid_recent,
            "mappings": _autoplay_config.mappings,
        }
        if _autoplay_config.pool_mode:
            resp["pools"] = _autoplay_pool.get_all_blocks()
        return jsonify(resp)

    @app.route("/api/autoplay/toggle", methods=["POST"])
    def autoplay_toggle():
        """Toggle autoplay on/off."""
        nonlocal _autoplay_enabled
        _autoplay_enabled = not _autoplay_enabled
        logger.info("AutoPlay: %s", "enabled" if _autoplay_enabled else "disabled")
        return jsonify({"ok": True, "enabled": _autoplay_enabled})

    @app.route("/api/autoplay/trigger", methods=["POST"])
    def autoplay_trigger():
        """Receive a block transition from PiPulse and auto-play mapped video."""
        nonlocal _autoplay_enabled
        data = request.get_json(silent=True) or {}
        block_name = data.get("block_name", "")
        display_name = data.get("display_name", block_name)

        if not block_name:
            return jsonify({"ok": False, "skipped": "no block_name"}), 400

        if not _autoplay_enabled:
            logger.info("AutoPlay trigger: %s (disabled)", block_name)
            return jsonify({"ok": True, "skipped": "autoplay disabled"})

        # Pool mode: weighted random selection from pool
        if _autoplay_config.pool_mode:
            selected = _autoplay_pool.select_video(block_name)
            if selected:
                url = _autoplay_pool.video_id_to_url(selected["video_id"])
                vid_title = selected["title"] or selected["video_id"]
                title = f"AutoPlay: {display_name} - {vid_title}"
                try:
                    # Snapshot previous autoplay before block transition
                    _snapshot_autoplay_for_completion("block_transition")
                    player.play_now(url, title)
                    # Track current autoplay video for UI rating buttons
                    _autoplay_current["video_id"] = selected["video_id"]
                    _autoplay_current["block_name"] = block_name
                    _autoplay_current["title"] = vid_title
                    _autoplay_start_time["value"] = time.monotonic()
                    logger.info("AutoPlay pool: %s -> %s (%s)", block_name, selected["video_id"], vid_title)
                    return jsonify({
                        "ok": True, "played": url, "block": block_name,
                        "video_id": selected["video_id"], "pool_mode": True,
                    })
                except Exception as e:
                    logger.exception("AutoPlay pool trigger failed for %s: %s", block_name, e)
                    return jsonify({"ok": False, "error": str(e)}), 500
            # Pool empty — fall through to legacy mapping
            logger.info("AutoPlay pool empty for %s, trying legacy mapping", block_name)

        # Legacy mode: single URL from config
        url = _autoplay_config.mappings.get(block_name)
        if not url:
            logger.info("AutoPlay trigger: %s (no mapping)", block_name)
            return jsonify({"ok": True, "skipped": "no mapping for block"})

        title = f"AutoPlay: {display_name}"
        try:
            _snapshot_autoplay_for_completion("block_transition")
            player.play_now(url, title)
            # Track current autoplay video for UI
            vid_id = extract_video_id(url) or url
            _autoplay_current["video_id"] = vid_id
            _autoplay_current["block_name"] = block_name
            _autoplay_current["title"] = display_name
            _autoplay_start_time["value"] = time.monotonic()
            logger.info("AutoPlay trigger: %s -> %s", block_name, url)
            return jsonify({"ok": True, "played": url, "block": block_name})
        except Exception as e:
            logger.exception("AutoPlay trigger failed for %s: %s", block_name, e)
            return jsonify({"ok": False, "error": str(e)}), 500

    # --- AutoPlay Pool Endpoints ---

    @app.route("/api/autoplay/pool")
    def autoplay_pool_summary():
        """Get summary of all block pools."""
        blocks = _autoplay_pool.get_all_blocks()
        return jsonify(blocks)

    @app.route("/api/autoplay/pool/<block_name>")
    def autoplay_pool_get(block_name):
        """Get all videos in a block's pool."""
        include_retired = request.args.get("retired") == "1"
        pool = _autoplay_pool.get_pool(block_name, include_retired=include_retired)
        return jsonify(pool)

    @app.route("/api/autoplay/pool/<block_name>", methods=["POST"])
    def autoplay_pool_add(block_name):
        """Add a video to a block's pool."""
        data = request.get_json(silent=True) or {}
        url = data.get("url", "").strip()
        if not url:
            return jsonify({"error": "url required"}), 400
        title = data.get("title", "")
        tags = data.get("tags", "")
        source = data.get("source", "manual")
        result = _autoplay_pool.add_video(block_name, url, title, tags, source)
        if result is None:
            return jsonify({"error": "video already in pool"}), 409
        return jsonify(result), 201

    @app.route("/api/autoplay/pool/<block_name>/<video_id>", methods=["DELETE"])
    def autoplay_pool_remove(block_name, video_id):
        """Retire a video from a block's pool."""
        ok = _autoplay_pool.remove_video(block_name, video_id)
        if not ok:
            return jsonify({"error": "video not found in pool"}), 404
        return jsonify({"ok": True})

    @app.route("/api/autoplay/pool/<block_name>/<video_id>/restore", methods=["POST"])
    def autoplay_pool_restore(block_name, video_id):
        """Restore a retired video."""
        ok = _autoplay_pool.restore_video(block_name, video_id)
        if not ok:
            return jsonify({"error": "video not found or not retired"}), 404
        return jsonify({"ok": True})

    @app.route("/api/autoplay/rate", methods=["POST"])
    def autoplay_rate():
        """Rate a video. Accepts video_id+block_name, or rates the last played."""
        data = request.get_json(silent=True) or {}
        rating = data.get("rating")
        if rating is None:
            return jsonify({"error": "rating required (-1, 0, or 1)"}), 400
        rating = int(rating)

        video_id = data.get("video_id")
        block_name = data.get("block_name")

        # If no video specified, rate the last played
        if not video_id or not block_name:
            last = _autoplay_pool.get_last_played()
            if not last:
                return jsonify({"error": "no recent autoplay to rate"}), 404
            video_id = last["video_id"]
            block_name = last["block_name"]

        ok = _autoplay_pool.rate_video(block_name, video_id, rating)
        if not ok:
            return jsonify({"error": "video not found"}), 404
        label = {-1: "disliked", 0: "neutral", 1: "liked"}.get(rating, str(rating))
        return jsonify({"ok": True, "video_id": video_id, "block": block_name, "rating": label})

    @app.route("/api/autoplay/history")
    def autoplay_history():
        """Get autoplay play history."""
        block = request.args.get("block")
        limit = int(request.args.get("limit", 20))
        history = _autoplay_pool.get_history(block_name=block, limit=limit)
        return jsonify(history)

    @app.route("/api/autoplay/seed", methods=["POST"])
    def autoplay_seed():
        """Seed pools from legacy mappings in config."""
        count = _autoplay_pool.seed_from_mappings(_autoplay_config.mappings)
        return jsonify({"ok": True, "seeded": count})

    # --- AutoPlay Discovery Endpoints ---

    @app.route("/api/autoplay/discover/<block_name>", methods=["POST"])
    def autoplay_discover_block(block_name):
        """Run YouTube discovery for a single block.

        Uses configured theme or accepts JSON overrides:
        {queries, min_duration, max_duration, max_results}
        """
        if block_name not in _discovery_themes:
            return jsonify({"error": f"No theme configured for block '{block_name}'"}), 404

        base_theme = _discovery_themes[block_name]
        data = request.get_json(silent=True) or {}

        # Allow overrides from request body
        theme = ThemeConfig(
            queries=data.get("queries", base_theme.queries),
            min_duration=data.get("min_duration", base_theme.min_duration),
            max_duration=data.get("max_duration", base_theme.max_duration),
            max_results=data.get("max_results", base_theme.max_results),
        )

        stats = _discovery_agent.discover_for_block(block_name, theme)
        return jsonify(stats)

    @app.route("/api/autoplay/discover", methods=["POST"])
    def autoplay_discover_all():
        """Run YouTube discovery for all configured blocks."""
        all_stats = _discovery_agent.discover_all(_discovery_themes)
        total_found = sum(s["found"] for s in all_stats)
        total_added = sum(s["added"] for s in all_stats)
        return jsonify({
            "ok": True,
            "blocks": all_stats,
            "total_found": total_found,
            "total_added": total_added,
        })

    # --- AutoPlay Cross-Block Learning Endpoints ---

    @app.route("/api/autoplay/suggestions/<block_name>")
    def autoplay_suggestions(block_name):
        """Get cross-block video suggestions for a block."""
        limit = request.args.get("limit", 10, type=int)
        suggestions = _autoplay_pool.get_cross_block_suggestions(block_name, limit)
        return jsonify(suggestions)

    @app.route("/api/autoplay/suggestions/<block_name>/accept", methods=["POST"])
    def autoplay_suggestion_accept(block_name):
        """Accept a suggestion — add video to target block's pool."""
        data = request.get_json(silent=True) or {}
        video_id = data.get("video_id", "").strip()
        if not video_id:
            return jsonify({"error": "video_id required"}), 400
        # Get video info from source block
        source_block = data.get("source_block", "")
        source = _autoplay_pool.get_video(source_block, video_id) if source_block else None
        title = source["title"] if source else data.get("title", "")
        url = _autoplay_pool.video_id_to_url(video_id)
        result = _autoplay_pool.add_video(block_name, url, title, source="cross_block")
        if result is None:
            return jsonify({"error": "video already in pool"}), 409
        return jsonify(result), 201

    @app.route("/api/autoplay/suggestions/<block_name>/dismiss", methods=["POST"])
    def autoplay_suggestion_dismiss(block_name):
        """Dismiss a suggestion (no-op for now, placeholder for future logic)."""
        return jsonify({"ok": True})

    # --- AutoPlay Export / Import Endpoints ---

    @app.route("/api/autoplay/export")
    def autoplay_export():
        """Export all pools as JSON (or YAML if Accept header requests it)."""
        data = _autoplay_pool.export_pools()
        accept = request.headers.get("Accept", "")
        if "yaml" in accept or "yml" in accept:
            try:
                import yaml
                yaml_str = yaml.dump(data, default_flow_style=False, sort_keys=False)
                return Response(yaml_str, mimetype="text/yaml")
            except ImportError:
                pass  # Fall through to JSON
        return jsonify(data)

    @app.route("/api/autoplay/import", methods=["POST"])
    def autoplay_import():
        """Import pools from JSON or YAML body."""
        content_type = request.content_type or ""
        merge = request.args.get("merge", "1") == "1"

        if "yaml" in content_type or "yml" in content_type:
            try:
                import yaml
                data = yaml.safe_load(request.data)
            except ImportError:
                return jsonify({"error": "PyYAML not installed on server"}), 500
            except Exception as e:
                return jsonify({"error": f"Invalid YAML: {e}"}), 400
        else:
            data = request.get_json(silent=True)

        if not data or not isinstance(data, dict):
            return jsonify({"error": "Request body must be JSON or YAML with 'blocks' key"}), 400

        stats = _autoplay_pool.import_pools(data, merge=merge)
        return jsonify({"ok": True, **stats})

    # --- Discover Endpoints ---

    DISCOVER_GENRES = [
        {"id": "horror", "label": "Horror"},
        {"id": "comedy", "label": "Comedy"},
        {"id": "drama", "label": "Drama"},
        {"id": "sci-fi", "label": "Sci-Fi"},
        {"id": "thriller", "label": "Thriller"},
        {"id": "western", "label": "Western"},
        {"id": "mystery", "label": "Mystery"},
        {"id": "romance", "label": "Romance"},
        {"id": "adventure", "label": "Adventure"},
        {"id": "action", "label": "Action"},
        {"id": "animation", "label": "Animation"},
        {"id": "documentary", "label": "Documentary"},
        {"id": "war", "label": "War"},
        {"id": "noir", "label": "Film Noir"},
        {"id": "musical", "label": "Musical"},
        {"id": "fantasy", "label": "Fantasy"},
    ]

    @app.route("/api/discover/genres")
    def discover_genres():
        """Return available genres for movie discovery."""
        return jsonify(DISCOVER_GENRES)

    @app.route("/api/discover/roll", methods=["POST"])
    def discover_roll():
        """Roll random movies from Archive.org, clear queue, and autoplay."""
        data = request.get_json(silent=True) or {}
        genre = data.get("genre", "").strip()
        decade = data.get("decade", "").strip()
        keyword = data.get("keyword", "").strip()
        sort = data.get("sort", "downloads").strip()
        year_start = int(data.get("year_start", 0) or 0)
        year_end = int(data.get("year_end", 0) or 0)
        if year_start > 0 and year_start < 1980:
            year_start = 1980

        archive_handler = sources.get_handler("archive")
        if not archive_handler:
            return jsonify({"error": "Archive source not available"}), 500

        # Fetch top 50 from Archive.org
        results = archive_handler.search(
            genre=genre, decade=decade, keyword=keyword, sort=sort, rows=50,
            year_start=year_start, year_end=year_end,
        )
        if not results:
            return jsonify({"error": "No movies found for this filter"}), 404

        # Exclude recently rolled URLs (last 100)
        recent_rows = db.fetchall(
            "SELECT url FROM discover_history ORDER BY rolled_at DESC LIMIT 100"
        )
        recent_urls = {r["url"] for r in recent_rows}
        pool = [item for item in results if item.url not in recent_urls]

        # Fall back to full results if filtering removed everything
        if not pool:
            pool = results

        # Random sample of 10 (or fewer if pool is small)
        sample = random.sample(pool, min(10, len(pool)))

        # Clear existing queue, then add rolled movies
        player.stop_playback()
        queue.clear_all()

        now = time.time()
        added_movies = []
        for item in sample:
            queue.add(item.url, item.title)
            db.execute(
                "INSERT INTO discover_history (url, title, genre, decade, rolled_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (item.url, item.title, genre, decade, now),
            )
            added_movies.append({"url": item.url, "title": item.title})
        db.commit()

        # Autoplay the first movie
        if added_movies:
            player.play_now(added_movies[0]["url"], added_movies[0]["title"])

        return jsonify({
            "ok": True,
            "added": len(added_movies),
            "genre": genre,
            "decade": decade,
            "movies": added_movies,
        })

    # --- Catalog Endpoints ---

    @app.route("/api/catalog/categories")
    def catalog_categories():
        return jsonify(CATEGORIES)

    @app.route("/api/catalog/categories/<category_id>")
    def catalog_category(category_id):
        series = get_series_by_category(category_id)
        return jsonify([s.to_dict() for s in series])

    @app.route("/api/catalog/series/<series_id>")
    def catalog_series(series_id):
        series = get_series_by_id(series_id)
        if not series:
            return jsonify({"error": "series not found"}), 404
        data = series.to_dict(include_episodes=True)
        # Include progress if available
        row = db.fetchone(
            "SELECT last_episode_index FROM catalog_progress WHERE series_id = ?",
            (series_id,),
        )
        data["progress"] = row["last_episode_index"] if row else None
        return jsonify(data)

    @app.route("/api/catalog/series/<series_id>/queue-all", methods=["POST"])
    def catalog_queue_all(series_id):
        series = get_series_by_id(series_id)
        if not series:
            return jsonify({"error": "series not found"}), 404
        added = 0
        for season in series.seasons:
            for ep in season.episodes:
                queue.add(ep.url, f"{series.title} - {ep.title}")
                added += 1
        return jsonify({"ok": True, "added": added})

    @app.route("/api/catalog/series/<series_id>/queue-season", methods=["POST"])
    def catalog_queue_season(series_id):
        series = get_series_by_id(series_id)
        if not series:
            return jsonify({"error": "series not found"}), 404
        data = request.get_json(silent=True) or {}
        season_num = data.get("season")
        if season_num is None:
            return jsonify({"error": "season required"}), 400
        added = 0
        for season in series.seasons:
            if season.number == int(season_num):
                for ep in season.episodes:
                    queue.add(ep.url, f"{series.title} - {ep.title}")
                    added += 1
                break
        return jsonify({"ok": True, "added": added})

    @app.route("/api/catalog/progress")
    def catalog_progress():
        rows = db.fetchall(
            "SELECT series_id, last_episode_index, updated_at FROM catalog_progress "
            "ORDER BY updated_at DESC"
        )
        result = []
        for row in rows:
            series = get_series_by_id(row["series_id"])
            if series:
                result.append({
                    "series_id": row["series_id"],
                    "series_title": series.title,
                    "last_episode_index": row["last_episode_index"],
                    "total_episodes": series.total_episodes,
                    "updated_at": row["updated_at"],
                })
        return jsonify(result)

    @app.route("/api/catalog/series/<series_id>/continue", methods=["POST"])
    def catalog_continue(series_id):
        series = get_series_by_id(series_id)
        if not series:
            return jsonify({"error": "series not found"}), 404
        row = db.fetchone(
            "SELECT last_episode_index FROM catalog_progress WHERE series_id = ?",
            (series_id,),
        )
        next_idx = (row["last_episode_index"] + 1) if row else 0
        ep = series.get_episode_by_index(next_idx)
        if not ep:
            return jsonify({"error": "No more episodes"}), 404
        title = f"{series.title} - {ep.title}"
        player.play_now(ep.url, title)
        return jsonify({
            "ok": True,
            "episode": {"title": title, "url": ep.url, "index": next_idx},
        })

    # --- Analytics Endpoint ---

    @app.route("/api/analytics")
    def analytics():
        """Get watch analytics for the given time window."""
        hours = int(request.args.get("hours", 24))
        notif = getattr(app, "notification_manager", None)
        if notif:
            return jsonify(notif.get_watch_analytics(hours))
        # Fallback: basic analytics from DB directly
        cutoff = time.time() - (hours * 3600)
        total_row = db.fetchone(
            "SELECT COUNT(*) as cnt, COALESCE(SUM(duration_watched), 0) as total "
            "FROM watch_sessions WHERE started_at >= ?",
            (cutoff,),
        )
        return jsonify({
            "hours": hours,
            "total_sessions": total_row["cnt"] if total_row else 0,
            "total_duration": total_row["total"] if total_row else 0,
            "top_by_time": [],
            "top_by_count": [],
        })

    # --- Health Check ---

    @app.route("/api/health")
    def health():
        resp = {
            "status": "ok",
            "version": _get_version(),
            "player_running": player.is_running,
            "mpv_connected": mpv.connected,
            "queue_pending": len(queue.get_pending()),
            "queue_total": len(queue.get_all()),
        }
        # SD error count (last hour)
        try:
            sd_row = db.fetchone(
                "SELECT COUNT(*) as cnt FROM sd_errors WHERE occurred_at >= ?",
                (time.time() - 3600,),
            )
            resp["sd_errors_1h"] = sd_row["cnt"] if sd_row else 0
        except Exception:
            resp["sd_errors_1h"] = 0
        # Include last auto-update status if available
        update_log = os.path.join(config.data_dir, "update.log")
        if os.path.exists(update_log):
            try:
                with open(update_log) as f:
                    lines = f.readlines()
                    if lines:
                        resp["last_update"] = lines[-1].strip()
            except OSError:
                pass
        return jsonify(resp)

    # --- Event Endpoints ---

    @app.route("/api/events")
    def events_stream():
        """SSE stream of real-time events."""
        def generate():
            q = event_bus.subscribe()
            try:
                while True:
                    try:
                        event = q.get(timeout=30)
                        data = json.dumps(event)
                        yield f"event: {event['type']}\ndata: {data}\n\n"
                    except Exception:
                        # Timeout - send keepalive heartbeat
                        yield ": heartbeat\n\n"
            finally:
                event_bus.unsubscribe(q)

        return Response(generate(), mimetype="text/event-stream")

    @app.route("/api/events/recent")
    def events_recent():
        """Get recent events."""
        limit = int(request.args.get("limit", 20))
        return jsonify(event_bus.recent(limit))

    # --- Queue Error Endpoints ---

    @app.route("/api/queue/failed")
    def queue_get_failed():
        """Get all failed queue items."""
        return jsonify([item.to_dict() for item in queue.get_failed()])

    @app.route("/api/queue/<int:item_id>/retry", methods=["POST"])
    def queue_retry_failed(item_id):
        """Retry a failed queue item."""
        ok = queue.retry_failed(item_id)
        if not ok:
            return jsonify({"error": "item not found or not failed"}), 404
        event_bus.emit("retry", f"Retrying item {item_id}", queue_item_id=item_id)
        return jsonify({"ok": True})

    @app.route("/api/queue/clear-failed", methods=["POST"])
    def queue_clear_failed():
        """Remove all failed items."""
        queue.clear_failed()
        return jsonify({"ok": True})

    # --- Library Endpoints ---

    @app.route("/api/library")
    def library_browse():
        source = request.args.get("source")
        fav = request.args.get("favorites") == "1"
        sort = request.args.get("sort", "recent")
        limit = int(request.args.get("limit", 50))
        offset = int(request.args.get("offset", 0))
        items = library.browse(
            source_type=source, favorites_only=fav,
            sort=sort, limit=limit, offset=offset,
        )
        return jsonify(items)

    @app.route("/api/library/search")
    def library_search():
        q = request.args.get("q", "")
        if not q:
            return jsonify({"error": "q parameter required"}), 400
        return jsonify(library.search(q))

    @app.route("/api/library/recent")
    def library_recent():
        limit = int(request.args.get("limit", 20))
        return jsonify(library.recent(limit))

    @app.route("/api/library/stats")
    def library_stats():
        """Get library statistics: totals, favorites, source breakdown, top played."""
        return jsonify(library.stats())

    @app.route("/api/library/count")
    def library_count():
        source = request.args.get("source")
        return jsonify({"count": library.count(source)})

    @app.route("/api/library/<int:library_id>")
    def library_get(library_id):
        entry = library.get(library_id)
        if not entry:
            return jsonify({"error": "not found"}), 404
        return jsonify(entry)

    @app.route("/api/library/<int:library_id>/notes", methods=["PUT"])
    def library_update_notes(library_id):
        data = request.get_json(silent=True) or {}
        notes = data.get("notes", "")
        library.update_notes(library_id, notes)
        return jsonify({"ok": True})

    @app.route("/api/library/<int:library_id>/favorite", methods=["POST"])
    def library_toggle_favorite(library_id):
        fav = library.toggle_favorite(library_id)
        return jsonify({"ok": True, "favorite": fav})

    @app.route("/api/library/<int:library_id>", methods=["DELETE"])
    def library_delete(library_id):
        library.delete(library_id)
        return jsonify({"ok": True})

    @app.route("/api/library/<int:library_id>/queue", methods=["POST"])
    def library_queue(library_id):
        """Add a library item back to the playback queue."""
        entry = library.get(library_id)
        if not entry:
            return jsonify({"error": "not found"}), 404
        item = queue.add(entry["url"], entry["title"])
        return jsonify(item.to_dict()), 201

    # --- Playlist Endpoints ---

    @app.route("/api/playlists")
    def playlists_list():
        return jsonify(library.list_playlists())

    @app.route("/api/playlists", methods=["POST"])
    def playlists_create():
        data = request.get_json(silent=True) or {}
        name = data.get("name", "").strip()
        if not name:
            return jsonify({"error": "name required"}), 400
        desc = data.get("description", "")
        try:
            pl = library.create_playlist(name, desc)
            return jsonify(pl), 201
        except Exception:
            return jsonify({"error": "playlist name already exists"}), 409

    @app.route("/api/playlists/<int:playlist_id>")
    def playlists_get(playlist_id):
        pl = library.get_playlist(playlist_id)
        if not pl:
            return jsonify({"error": "not found"}), 404
        pl["items"] = library.get_playlist_items(playlist_id)
        return jsonify(pl)

    @app.route("/api/playlists/<int:playlist_id>", methods=["PUT"])
    def playlists_update(playlist_id):
        data = request.get_json(silent=True) or {}
        library.update_playlist(
            playlist_id,
            name=data.get("name"),
            description=data.get("description"),
        )
        return jsonify({"ok": True})

    @app.route("/api/playlists/<int:playlist_id>", methods=["DELETE"])
    def playlists_delete(playlist_id):
        library.delete_playlist(playlist_id)
        return jsonify({"ok": True})

    @app.route("/api/playlists/<int:playlist_id>/items", methods=["POST"])
    def playlists_add_item(playlist_id):
        data = request.get_json(silent=True) or {}
        library_id = data.get("library_id")
        if library_id is None:
            return jsonify({"error": "library_id required"}), 400
        result = library.add_to_playlist(playlist_id, library_id)
        if result is None:
            return jsonify({"error": "already in playlist or invalid IDs"}), 409
        return jsonify(result), 201

    @app.route("/api/playlists/<int:playlist_id>/add-by-url", methods=["POST"])
    def playlists_add_by_url(playlist_id):
        """Add a video to a playlist by URL. Creates library entry if needed."""
        data = request.get_json(silent=True) or {}
        url = data.get("url")
        if not url:
            return jsonify({"error": "url required"}), 400
        title = data.get("title", "")
        source_type = data.get("source_type", "youtube")
        entry = library.add(url, title, source_type)
        result = library.add_to_playlist(playlist_id, entry["id"])
        if result is None:
            return jsonify({"error": "already in playlist"}), 409
        return jsonify(result), 201

    @app.route("/api/library/fav-by-url", methods=["POST"])
    def library_fav_by_url():
        """Add a video to favorites by URL. Creates library entry if needed."""
        data = request.get_json(silent=True) or {}
        url = data.get("url")
        if not url:
            return jsonify({"error": "url required"}), 400
        title = data.get("title", "")
        source_type = data.get("source_type", "youtube")
        entry = library.add(url, title, source_type)
        if not entry.get("favorite"):
            library.toggle_favorite(entry["id"])
        return jsonify({"ok": True, "library_id": entry["id"]})

    @app.route("/api/playlists/<int:playlist_id>/items/<int:library_id>", methods=["DELETE"])
    def playlists_remove_item(playlist_id, library_id):
        library.remove_from_playlist(playlist_id, library_id)
        return jsonify({"ok": True})

    @app.route("/api/playlists/<int:playlist_id>/queue", methods=["POST"])
    def playlists_queue(playlist_id):
        """Add all playlist items to the playback queue."""
        items = library.queue_playlist(playlist_id)
        added = 0
        for item in items:
            queue.add(item["url"], item["title"])
            added += 1
        return jsonify({"ok": True, "queued": added})

    # --- Source Endpoints ---

    @app.route("/api/sources")
    def sources_list():
        """List available source types."""
        return jsonify(sources.list_sources())

    @app.route("/api/sources/detect", methods=["POST"])
    def sources_detect():
        """Detect source type for a URL."""
        data = request.get_json(silent=True) or {}
        url = data.get("url", "")
        if not url:
            return jsonify({"error": "url required"}), 400
        source_type = sources.detect(url)
        return jsonify({"source_type": source_type})

    @app.route("/api/sources/metadata", methods=["POST"])
    def sources_metadata():
        """Get metadata for a URL using the appropriate source handler."""
        data = request.get_json(silent=True) or {}
        url = data.get("url", "")
        if not url:
            return jsonify({"error": "url required"}), 400
        item = sources.get_metadata(url)
        if item:
            return jsonify(item.to_dict())
        return jsonify({"error": "could not fetch metadata"}), 404

    @app.route("/api/sources/browse")
    def sources_browse():
        """Browse local files. Pass ?path= for subdirectories."""
        path = request.args.get("path", "")
        local_handler = sources.get_handler("local")
        if not local_handler:
            return jsonify({"error": "local source not available"}), 404
        items = local_handler.browse(path)
        return jsonify([item.to_dict() for item in items])

    @app.route("/api/sources/drives")
    def sources_drives():
        """List available external drives."""
        local_handler = sources.get_handler("local")
        if not local_handler:
            return jsonify([])
        return jsonify(local_handler.scan_drives())

    # --- Device Endpoints ---

    @app.route("/api/devices")
    def devices_list():
        """List all known PiCast devices."""
        include_offline = request.args.get("offline") == "1"
        return jsonify(device_registry.list_devices(include_offline))

    @app.route("/api/devices/<name>")
    def devices_get(name):
        """Get a specific device by name."""
        device = device_registry.get_device(name)
        if not device:
            return jsonify({"error": "device not found"}), 404
        return jsonify(device)

    @app.route("/api/devices/<name>/health")
    def devices_health(name):
        """Check if a device is reachable."""
        from picast.server.discovery import check_device_health
        device = device_registry.get_device(name)
        if not device:
            return jsonify({"error": "device not found"}), 404
        healthy = check_device_health(device["host"], device["port"])
        return jsonify({"name": name, "healthy": healthy})

    # --- Import (migration from bash version) ---

    @app.route("/api/import/queue-txt", methods=["POST"])
    def import_queue_txt():
        data = request.get_json(silent=True) or {}
        path = data.get("path", os.path.expanduser("~/video-queue/queue.txt"))
        if not os.path.exists(path):
            return jsonify({"error": f"File not found: {path}"}), 404
        count = queue.import_queue_txt(path)
        return jsonify({"ok": True, "imported": count})

    # --- System Settings Endpoints ---

    @app.route("/api/system/volume")
    def system_volume_get():
        """Read current mpv software volume."""
        vol = mpv.get_property("volume", 100)
        return jsonify({"volume": round(vol)})

    @app.route("/api/system/volume", methods=["POST"])
    def system_volume_set():
        """Set mpv software volume and persist to DB."""
        data = request.get_json(silent=True) or {}
        vol = data.get("volume")
        if vol is None:
            return jsonify({"error": "volume required"}), 400
        vol = max(0, min(100, int(vol)))
        ok = mpv.set_volume(vol)
        db.set_setting("volume", str(vol))
        return jsonify({"ok": ok, "volume": vol})

    _CMDLINE_PATH = "/boot/firmware/cmdline.txt"
    _PANEL_ORIENTATION = "video=HDMI-A-1:panel_orientation=upside_down"

    def _get_display_rotation() -> int:
        """Read display rotation from kernel cmdline (0=normal, 2=180)."""
        try:
            result = subprocess.run(
                ["cat", _CMDLINE_PATH],
                capture_output=True, text=True, timeout=5,
            )
            return 2 if _PANEL_ORIENTATION in result.stdout else 0
        except Exception:
            return 0

    @app.route("/api/system/display")
    def system_display_get():
        """Read display rotation from kernel cmdline."""
        rotate = _get_display_rotation()
        return jsonify({"rotate": rotate})

    @app.route("/api/system/display", methods=["POST"])
    def system_display_set():
        """Set display rotation via kernel cmdline (requires reboot)."""
        data = request.get_json(silent=True) or {}
        rotate = data.get("rotate")
        if rotate is None:
            return jsonify({"error": "rotate required"}), 400
        rotate = int(rotate)
        if rotate not in (0, 2):
            return jsonify({"error": "rotate must be 0 (normal) or 2 (180)"}), 400

        try:
            result = subprocess.run(
                ["sudo", "cat", _CMDLINE_PATH],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode != 0:
                return jsonify({"error": "Failed to read cmdline.txt"}), 500
            cmdline = result.stdout.strip()

            if rotate == 2 and _PANEL_ORIENTATION not in cmdline:
                cmdline = cmdline + " " + _PANEL_ORIENTATION
            elif rotate == 0:
                cmdline = cmdline.replace(" " + _PANEL_ORIENTATION, "")
                cmdline = cmdline.replace(_PANEL_ORIENTATION, "")

            result = subprocess.run(
                ["sudo", "tee", _CMDLINE_PATH],
                input=cmdline + "\n", capture_output=True, text=True, timeout=5,
            )
            if result.returncode != 0:
                return jsonify({"error": "Failed to write cmdline.txt"}), 500

            return jsonify({"ok": True, "rotate": rotate, "reboot_required": True})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/system/reboot", methods=["POST"])
    def system_reboot():
        """Reboot the Pi (needed for display rotation changes)."""
        try:
            subprocess.Popen(
                ["sudo", "reboot"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            return jsonify({"ok": True, "message": "Reboot initiated"})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    @app.route("/api/system/info")
    def system_info():
        """Get system information."""
        info = {"version": _get_version()}

        # Hostname
        try:
            info["hostname"] = os.uname().nodename
        except Exception:
            info["hostname"] = "unknown"

        # IP address
        try:
            import socket
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            info["ip"] = s.getsockname()[0]
            s.close()
        except Exception:
            info["ip"] = "unknown"

        # Uptime
        try:
            result = subprocess.run(
                ["uptime", "-p"], capture_output=True, text=True, timeout=5,
            )
            info["uptime"] = result.stdout.strip() if result.returncode == 0 else None
        except Exception:
            info["uptime"] = None

        # CPU temperature
        try:
            with open("/sys/class/thermal/thermal_zone0/temp") as f:
                temp_raw = int(f.read().strip())
            info["cpu_temp"] = f"{temp_raw / 1000:.1f}°C"
        except Exception:
            info["cpu_temp"] = None

        # Disk usage
        try:
            total, used, free = shutil.disk_usage("/")
            info["disk"] = f"{used // (1024**3)}GB / {total // (1024**3)}GB ({100 * used // total}%)"
        except Exception:
            info["disk"] = None

        # Audio device
        try:
            result = subprocess.run(
                ["aplay", "-l"], capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                lines = [l for l in result.stdout.splitlines() if l.startswith("card")]
                info["audio_device"] = lines[0] if lines else "No audio devices"
            else:
                info["audio_device"] = None
        except Exception:
            info["audio_device"] = None

        return jsonify(info)

    @app.route("/api/system/osd")
    def system_osd_get():
        """Get OSD enabled state from mpv."""
        level = mpv.get_property("osd-level", 3)
        return jsonify({"enabled": level > 0, "level": level})

    @app.route("/api/system/osd", methods=["POST"])
    def system_osd_set():
        """Toggle mpv OSD overlay on/off (level 3 = overlay, 0 = hidden)."""
        level = mpv.get_property("osd-level", 3)
        new_level = 0 if level > 0 else 3
        ok = mpv.set_property("osd-level", new_level)
        return jsonify({"ok": ok, "enabled": new_level > 0, "level": new_level})

    @app.route("/api/system/restart", methods=["POST"])
    def system_restart():
        """Restart the PiCast service."""
        try:
            subprocess.Popen(
                ["sudo", "systemctl", "restart", "picast"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            return jsonify({"ok": True, "message": "Restart initiated"})
        except Exception as e:
            return jsonify({"error": str(e)}), 500

    return app


def _get_version() -> str:
    try:
        from picast.__about__ import __version__
        return __version__
    except ImportError:
        return "unknown"
