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

    app = create_app(config.server, devices=devices)

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
            except ImportError:
                logging.getLogger("picast").error(
                    "Telegram dependencies not installed. "
                    'Install with: pip install "picast[telegram]"'
                )

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


if __name__ == "__main__":
    run_server()
