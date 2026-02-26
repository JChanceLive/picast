"""CLI entry points for PiCast.

picast-server: Runs the Flask server on the Pi
picast: Runs the Textual TUI on your Mac (Session 2)
"""

import argparse
import logging
import os
import socket
import sys
import threading
import time


def run_server():
    """Entry point for picast-server command."""
    parser = argparse.ArgumentParser(
        description="PiCast server - media center REST API for Raspberry Pi"
    )
    parser.add_argument(
        "--host", default=None, help="Host to bind to (default: 0.0.0.0)"
    )
    parser.add_argument(
        "--port", type=int, default=None, help="Port to listen on (default: 5050)"
    )
    parser.add_argument(
        "--config", default=None, help="Path to picast.toml config file"
    )
    parser.add_argument(
        "--debug", action="store_true", help="Enable debug mode"
    )
    parser.add_argument(
        "--log-level", default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Log level (default: INFO)"
    )
    parser.add_argument(
        "--no-player", action="store_true",
        help="Disable player loop (for testing web UI without mpv)"
    )
    parser.add_argument(
        "--quiet", action="store_true",
        help="Suppress per-request werkzeug logs"
    )
    parser.add_argument(
        "--test", action="store_true",
        help="Test mode: --no-player + --quiet (for Mac-side web UI testing)"
    )
    parser.add_argument(
        "--telegram", action="store_true",
        help="Enable Telegram bot (requires [telegram] config or PICAST_TELEGRAM_TOKEN env)"
    )
    args = parser.parse_args()

    # Configure logging
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    from picast.config import load_config
    from picast.server.app import create_app

    config = load_config(args.config)

    # CLI args override config file
    if args.host:
        config.server.host = args.host
    if args.port:
        config.server.port = args.port

    # Build device list from config
    devices = [(d.name, d.host, d.port) for d in config.devices]

    app = create_app(
        config.server, devices=devices,
        autoplay_config=config.autoplay, pipulse_config=config.pipulse,
    )

    # Regenerate desktop wallpaper in background (keeps version badge current)
    def _refresh_wallpaper():
        try:
            from picast.wallpaper import generate_wallpaper
            generate_wallpaper()
        except Exception as e:
            logging.getLogger("picast").warning("Wallpaper refresh failed: %s", e)

    threading.Thread(target=_refresh_wallpaper, daemon=True, name="wallpaper").start()

    # --test is shorthand for --no-player --quiet
    if args.test:
        args.no_player = True
        args.quiet = True

    # Disable player loop if --no-player (for Mac-side web UI testing)
    if args.no_player:
        app.player.stop()
        logging.getLogger("picast").info("Player loop disabled (--no-player)")

    # Suppress per-request werkzeug logs if --quiet
    if args.quiet:
        logging.getLogger("werkzeug").setLevel(logging.WARNING)

    # Start mDNS discovery
    if hasattr(app, 'device_registry'):
        app.device_registry.start_discovery()

    logging.getLogger("picast").info(
        "PiCast server starting on %s:%d", config.server.host, config.server.port
    )

    # Start Telegram bot if requested
    if args.telegram or config.telegram.enabled:
        import os
        token = config.telegram.bot_token or os.environ.get("PICAST_TELEGRAM_TOKEN", "")
        if not token:
            logging.getLogger("picast").error(
                "Telegram bot enabled but no token configured. "
                "Set [telegram] bot_token in picast.toml or PICAST_TELEGRAM_TOKEN env var."
            )
        else:
            try:
                from picast.server.telegram_bot import PiCastBot
                api_url = f"http://127.0.0.1:{config.server.port}"
                bot = PiCastBot(
                    token=token,
                    api_url=api_url,
                    allowed_users=config.telegram.allowed_users,
                )
                bot.start_background()
                logging.getLogger("picast").info("Telegram bot enabled")

                # Start notification manager if chat_id configured
                if config.telegram.notification_chat_id:
                    from picast.server.notifications import NotificationManager
                    notif_manager = NotificationManager(
                        db=app.db,
                        send_fn=bot.send_notification_sync,
                        chat_id=config.telegram.notification_chat_id,
                        daily_summary_hour=config.telegram.daily_summary_hour,
                    )
                    notif_manager.start()
                    app.db.set_notification_manager(notif_manager)
                    app.notification_manager = notif_manager
                    logging.getLogger("picast").info(
                        "Notification manager enabled (chat_id=%d)",
                        config.telegram.notification_chat_id,
                    )
            except ImportError:
                logging.getLogger("picast").error(
                    "Telegram dependencies not installed. "
                    'Install with: pip install "picast[telegram]"'
                )

    # Persist setup status flags for the settings page /api/settings/setup-status
    if config.pushover.enabled and config.pushover.api_token:
        app.db.set_setting("pushover_configured", "true")
    if config.server.ytdl_cookies_from_browser or config.server.ytdl_po_token:
        app.db.set_setting("youtube_configured", "true")

    # Start Pushover notification manager if configured
    if config.pushover.enabled:
        from picast.server.pushover_adapter import create_pushover_send_fn
        from picast.server.notifications import NotificationManager

        pushover_send_fn = create_pushover_send_fn(
            api_token=config.pushover.api_token,
            user_key=config.pushover.user_key,
        )

        if hasattr(app, 'notification_manager') and app.notification_manager:
            # Telegram already created a NotificationManager — swap transport
            app.notification_manager._send_fn = pushover_send_fn
            logging.getLogger("picast").info("Pushover replaced Telegram transport")
        else:
            # No Telegram — create a fresh NotificationManager with Pushover
            notif_manager = NotificationManager(
                db=app.db,
                send_fn=pushover_send_fn,
                chat_id=1,  # Dummy — Pushover ignores chat_id
                daily_summary_hour=config.pushover.daily_summary_hour,
            )
            notif_manager.start()
            app.db.set_notification_manager(notif_manager)
            app.notification_manager = notif_manager
            logging.getLogger("picast").info("Pushover notification manager enabled")

    # Notify systemd that we're ready (if running as a service)
    _notify_systemd("READY=1")

    # Start watchdog thread for systemd
    _start_watchdog()

    app.run(
        host=config.server.host,
        port=config.server.port,
        debug=args.debug,
        threaded=True,  # Handle concurrent requests (status polls + UI)
        use_reloader=False,  # Don't reload - we have background threads
    )


def _notify_systemd(state: str):
    """Send a notification to systemd via NOTIFY_SOCKET.

    No external dependency needed - uses raw socket protocol.
    """
    notify_socket = os.environ.get("NOTIFY_SOCKET")
    if not notify_socket:
        return
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        if notify_socket.startswith("@"):
            # Abstract socket
            notify_socket = "\0" + notify_socket[1:]
        sock.connect(notify_socket)
        sock.sendall(state.encode())
        sock.close()
    except OSError:
        pass


def _start_watchdog():
    """Start a background thread that pings the systemd watchdog.

    Only active when WATCHDOG_USEC is set by systemd.
    """
    watchdog_usec = os.environ.get("WATCHDOG_USEC")
    if not watchdog_usec:
        return
    interval = int(watchdog_usec) / 1_000_000 / 2  # Ping at half the timeout

    def watchdog_loop():
        while True:
            _notify_systemd("WATCHDOG=1")
            time.sleep(interval)

    t = threading.Thread(target=watchdog_loop, daemon=True, name="sd-watchdog")
    t.start()
    logging.getLogger("picast").debug("Systemd watchdog started (interval=%.0fs)", interval)


def run_tui():
    """Entry point for picast TUI command."""
    parser = argparse.ArgumentParser(
        description="PiCast TUI - terminal dashboard for controlling PiCast"
    )
    parser.add_argument(
        "--host", default=None, help="Pi host (default: from config or raspberrypi.local)"
    )
    parser.add_argument(
        "--port", type=int, default=None, help="Pi port (default: from config or 5000)"
    )
    parser.add_argument(
        "--config", default=None, help="Path to picast.toml config file"
    )
    args = parser.parse_args()

    from picast.config import load_config

    config = load_config(args.config)
    device = config.get_default_device()

    host = args.host or device.host
    port = args.port or device.port

    # Build device list from config for TUI device switcher
    devices = [(d.name, d.host, d.port) for d in config.devices]

    try:
        from picast.tui.app import PiCastApp
    except ImportError:
        print("TUI dependencies not installed. Install with:")
        print('  pip install "picast[tui]"')
        sys.exit(1)

    app = PiCastApp(host=host, port=port, devices=devices)
    app.run()


def run_pool_cli():
    """Entry point for picast-pool command. Manage autoplay pools via HTTP API."""
    import json
    import urllib.request
    import urllib.error

    parser = argparse.ArgumentParser(
        description="PiCast AutoPlay Pool Manager"
    )
    parser.add_argument(
        "--server", default="http://localhost:5050",
        help="PiCast server URL (default: http://localhost:5050)"
    )
    sub = parser.add_subparsers(dest="command")

    # list
    p_list = sub.add_parser("list", help="List blocks or videos in a block")
    p_list.add_argument("block", nargs="?", help="Block name (omit for all blocks)")

    # add
    p_add = sub.add_parser("add", help="Add video to a block's pool")
    p_add.add_argument("block", help="Block name")
    p_add.add_argument("url", help="YouTube URL")
    p_add.add_argument("--title", default="", help="Video title")

    # rate
    p_rate = sub.add_parser("rate", help="Rate last played video")
    p_rate.add_argument("rating", type=int, choices=[-1, 0, 1], help="Rating (-1, 0, or 1)")

    # remove
    p_remove = sub.add_parser("remove", help="Retire video from pool")
    p_remove.add_argument("block", help="Block name")
    p_remove.add_argument("video_id", help="YouTube video ID")

    # history
    p_hist = sub.add_parser("history", help="Show play history")
    p_hist.add_argument("--block", default=None, help="Filter by block")
    p_hist.add_argument("--limit", type=int, default=20, help="Number of entries")

    # import
    p_import = sub.add_parser("import", help="Bulk import URLs from a text file")
    p_import.add_argument("block", help="Block name")
    p_import.add_argument("file", help="Text file with one URL per line")

    # discover
    p_discover = sub.add_parser("discover", help="Discover new videos via YouTube search")
    p_discover.add_argument("block", nargs="?", help="Block name (omit for all blocks)")
    p_discover.add_argument("--query", default=None, help="Override search query")
    p_discover.add_argument("--min-duration", type=int, default=None, help="Min duration (seconds)")
    p_discover.add_argument("--max-duration", type=int, default=None, help="Max duration (seconds)")
    p_discover.add_argument("--max-results", type=int, default=None, help="Max results per query")

    # export
    p_export = sub.add_parser("export", help="Export pools to YAML file")
    p_export.add_argument("--file", default=None, help="Output file (default: stdout)")

    # import-pools (not 'import' to avoid Python keyword confusion in help)
    p_import_pools = sub.add_parser("import-pools", help="Import pools from YAML file")
    p_import_pools.add_argument("file", help="YAML file to import")
    p_import_pools.add_argument("--replace", action="store_true", help="Replace mode (deactivate existing before import)")

    args = parser.parse_args()
    base = args.server.rstrip("/")

    def api_get(path):
        url = f"{base}{path}"
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read())
        except urllib.error.URLError as e:
            print(f"Error: Could not connect to {base} - {e}")
            sys.exit(1)

    def api_post(path, data=None, timeout=10):
        url = f"{base}{path}"
        body = json.dumps(data or {}).encode()
        try:
            req = urllib.request.Request(url, data=body, method="POST",
                                        headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return json.loads(e.read())
        except urllib.error.URLError as e:
            print(f"Error: Could not connect to {base} - {e}")
            sys.exit(1)

    def api_delete(path):
        url = f"{base}{path}"
        try:
            req = urllib.request.Request(url, method="DELETE")
            with urllib.request.urlopen(req, timeout=10) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return json.loads(e.read())
        except urllib.error.URLError as e:
            print(f"Error: Could not connect to {base} - {e}")
            sys.exit(1)

    if args.command == "list":
        if args.block:
            videos = api_get(f"/api/autoplay/pool/{args.block}")
            if not videos:
                print(f"No videos in '{args.block}' pool")
                return
            print(f"Pool: {args.block} ({len(videos)} videos)")
            print("-" * 60)
            for v in videos:
                rating = {1: "+", -1: "-", 0: " "}.get(v.get("rating", 0), " ")
                plays = v.get("play_count", 0)
                title = v.get("title") or v["video_id"]
                print(f"  [{rating}] {v['video_id']:15s}  {plays:3d}x  {title}")
        else:
            blocks = api_get("/api/autoplay/pool")
            if not blocks:
                print("No autoplay pools configured")
                return
            print(f"{'Block':<25s} {'Videos':>7s} {'Liked':>6s} {'Disliked':>9s}")
            print("-" * 50)
            for b in blocks:
                print(f"  {b['block_name']:<23s} {b['pool_size']:>5d}   {b.get('liked', 0):>4d}   {b.get('disliked', 0):>7d}")

    elif args.command == "add":
        result = api_post(f"/api/autoplay/pool/{args.block}", {"url": args.url, "title": args.title})
        if result.get("video_id"):
            print(f"Added {result['video_id']} to '{args.block}'")
        else:
            print(f"Error: {result.get('error', 'unknown')}")

    elif args.command == "rate":
        result = api_post("/api/autoplay/rate", {"rating": args.rating})
        if result.get("ok"):
            print(f"Rated {result['video_id']} as {result['rating']}")
        else:
            print(f"Error: {result.get('error', 'unknown')}")

    elif args.command == "remove":
        result = api_delete(f"/api/autoplay/pool/{args.block}/{args.video_id}")
        if result.get("ok"):
            print(f"Removed {args.video_id} from '{args.block}'")
        else:
            print(f"Error: {result.get('error', 'unknown')}")

    elif args.command == "history":
        params = f"?limit={args.limit}"
        if args.block:
            params += f"&block={args.block}"
        history = api_get(f"/api/autoplay/history{params}")
        if not history:
            print("No play history")
            return
        print(f"{'Block':<20s} {'Video':<15s} {'Title':<30s} {'Played At'}")
        print("-" * 80)
        for h in history:
            title = (h.get("title") or h["video_id"])[:28]
            played = h.get("played_at", "")[:19]
            print(f"  {h['block_name']:<18s} {h['video_id']:<13s} {title:<28s} {played}")

    elif args.command == "import":
        if not os.path.exists(args.file):
            print(f"File not found: {args.file}")
            sys.exit(1)
        with open(args.file) as f:
            urls = [line.strip() for line in f if line.strip() and not line.startswith("#")]
        added = 0
        failed = 0
        for url in urls:
            result = api_post(f"/api/autoplay/pool/{args.block}", {"url": url})
            if result.get("video_id"):
                added += 1
            else:
                failed += 1
                print(f"  Skip: {url} ({result.get('error', 'unknown')})")
        print(f"Imported {added}/{len(urls)} videos to '{args.block}'" +
              (f" ({failed} failed)" if failed else ""))

    elif args.command == "discover":
        if args.block:
            # Single block discovery
            body = {}
            if args.query:
                body["queries"] = [args.query]
            if args.min_duration is not None:
                body["min_duration"] = args.min_duration
            if args.max_duration is not None:
                body["max_duration"] = args.max_duration
            if args.max_results is not None:
                body["max_results"] = args.max_results
            print(f"Discovering videos for '{args.block}'...")
            result = api_post(f"/api/autoplay/discover/{args.block}", body, timeout=120)
            if result.get("error"):
                print(f"Error: {result['error']}")
            else:
                print(f"  Queries: {result.get('queries_run', 0)}, "
                      f"Found: {result.get('found', 0)}, "
                      f"Added: {result.get('added', 0)}, "
                      f"Skipped: {result.get('skipped', 0)}")
        else:
            # All blocks
            print("Discovering videos for all configured blocks...")
            result = api_post("/api/autoplay/discover", timeout=300)
            if result.get("error"):
                print(f"Error: {result['error']}")
            else:
                for block_stats in result.get("blocks", []):
                    print(f"  {block_stats['block']}: "
                          f"found={block_stats['found']}, "
                          f"added={block_stats['added']}, "
                          f"skipped={block_stats['skipped']}")
                print(f"\nTotal: found={result.get('total_found', 0)}, "
                      f"added={result.get('total_added', 0)}")

    elif args.command == "export":
        data = api_get("/api/autoplay/export")
        try:
            import yaml
            output = yaml.dump(data, default_flow_style=False, sort_keys=False)
        except ImportError:
            output = json.dumps(data, indent=2)
            print("(PyYAML not installed, exporting as JSON)", file=sys.stderr)

        if args.file:
            with open(args.file, "w") as f:
                f.write(output)
            print(f"Exported to {args.file}")
        else:
            print(output)

    elif args.command == "import-pools":
        if not os.path.exists(args.file):
            print(f"File not found: {args.file}")
            sys.exit(1)
        with open(args.file) as f:
            raw = f.read()
        # Try YAML first, fall back to JSON
        try:
            import yaml
            data = yaml.safe_load(raw)
        except ImportError:
            data = json.loads(raw)
        except Exception:
            data = json.loads(raw)

        merge = not args.replace
        merge_param = "1" if merge else "0"
        url = f"{base}/api/autoplay/import?merge={merge_param}"
        body = json.dumps(data).encode()
        try:
            req = urllib.request.Request(
                url, data=body, method="POST",
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=30) as resp:
                result = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            result = json.loads(e.read())
        except urllib.error.URLError as e:
            print(f"Error: Could not connect to {base} - {e}")
            sys.exit(1)

        if result.get("ok"):
            mode = "replace" if args.replace else "merge"
            print(f"Imported ({mode}): {result.get('added', 0)} added, "
                  f"{result.get('skipped', 0)} skipped, "
                  f"{result.get('blocks', 0)} blocks")
        else:
            print(f"Error: {result.get('error', 'unknown')}")

    else:
        parser.print_help()


def run_export_cli():
    """Shortcut: picast-export [--file OUT] [--server URL]."""
    import json
    import urllib.request
    import urllib.error

    parser = argparse.ArgumentParser(description="Export PiCast autoplay pools")
    parser.add_argument("--server", default="http://picast.local:5050", help="PiCast server URL")
    parser.add_argument("--file", "-o", default=None, help="Output file (default: stdout)")
    args = parser.parse_args()

    base = args.server.rstrip("/")
    try:
        req = urllib.request.Request(f"{base}/api/autoplay/export")
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
    except urllib.error.URLError as e:
        print(f"Error: Could not connect to {base} - {e}")
        sys.exit(1)

    try:
        import yaml
        output = yaml.dump(data, default_flow_style=False, sort_keys=False)
    except ImportError:
        output = json.dumps(data, indent=2)

    if args.file:
        with open(args.file, "w") as f:
            f.write(output)
        print(f"Exported to {args.file}")
    else:
        print(output)


def run_setup():
    """Entry point for picast-setup command."""
    parser = argparse.ArgumentParser(
        description="PiCast interactive setup wizard"
    )
    parser.add_argument(
        "--config", default=None,
        help="Path to picast.toml (default: ~/.config/picast/picast.toml)"
    )
    args = parser.parse_args()

    from picast.setup_wizard import run_wizard
    run_wizard(config_path=args.config)


if __name__ == "__main__":
    run_server()
