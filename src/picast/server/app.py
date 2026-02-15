"""Flask REST API for PiCast.

Provides HTTP endpoints for player control, queue management, and status.
This is the single source of truth - both the TUI and web UI talk to this.
"""

import json
import logging
import os
import re

from flask import Flask, Response, jsonify, redirect, render_template, request

from picast.config import ServerConfig
from picast.server.database import Database
from picast.server.discovery import DeviceRegistry
from picast.server.events import EventBus
from picast.server.library import Library
from picast.server.mpv_client import MPVClient
from picast.server.player import Player
from picast.server.queue_manager import QueueManager
from picast.server.sources import LocalSource, SourceRegistry, TwitchSource, YouTubeSource

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


def create_app(config: ServerConfig | None = None, devices: list | None = None) -> Flask:
    """Create and configure the Flask application.

    Args:
        config: Server configuration. Uses defaults if None.
        devices: List of (name, host, port) tuples for known devices.
    """
    if config is None:
        config = ServerConfig()

    # Ensure data directory exists
    os.makedirs(config.data_dir, exist_ok=True)

    template_dir = os.path.join(os.path.dirname(__file__), "templates")
    static_dir = os.path.join(os.path.dirname(__file__), "static")
    app = Flask(__name__, template_folder=template_dir, static_folder=static_dir)
    app.config["PICAST"] = config

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

    # Store on app for access in routes
    app.mpv = mpv
    app.queue = queue
    app.db = db
    app.library = library
    app.sources = sources
    app.player = player
    app.event_bus = event_bus
    app.device_registry = device_registry

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
        return jsonify(player.get_status())

    @app.route("/api/play", methods=["POST"])
    def play():
        """Play a URL immediately."""
        data = request.get_json(silent=True) or {}
        url = data.get("url", "").strip()
        if not url:
            return jsonify({"error": "url required"}), 400
        url = _normalize_youtube_input(url)
        title = data.get("title", "")
        player.play_now(url, title)
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
        player.skip()
        return jsonify({"ok": True})

    @app.route("/api/stop", methods=["POST"])
    def stop():
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
        ok = mpv.set_volume(int(level))
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
        valid, error = sources.validate_url(url)
        if not valid:
            return jsonify({"error": error}), 400
        title = data.get("title", "")
        # Use source registry for better detection
        if not title:
            meta = sources.get_metadata(url)
            if meta and meta.title:
                title = meta.title
        item = queue.add(url, title)
        return jsonify(item.to_dict()), 201

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

    return app


def _get_version() -> str:
    try:
        from picast.__about__ import __version__
        return __version__
    except ImportError:
        return "unknown"
