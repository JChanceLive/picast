"""PiCast Receiver — minimal fleet display endpoint.

Thin CLI wrapper that builds the receiver Blueprint with the default
MpvPlayer + ReceiverWatchdog and runs it as a Flask app. All playback,
watchdog, and HTTP logic lives in the sibling modules:

    picast.receiver.player      — PlayerAdapter Protocol + MpvPlayer
    picast.receiver.watchdog    — ReceiverWatchdog
    picast.receiver.blueprint   — create_receiver_blueprint()

Usage:
    python picast_receiver.py [--port 5050] [--host 0.0.0.0]

Behavior is byte-identical to v0.8.0 for picast-z1.
"""

from __future__ import annotations

import argparse
import logging
import signal

from flask import Flask

# Support both package import (StarScreen: `from picast.receiver import ...`)
# and direct-script execution (picast-z1: `python picast_receiver.py`).
try:
    from .blueprint import create_receiver_blueprint
    from .player import MpvPlayer, ReceiverConfig
    from .watchdog import ReceiverWatchdog, WatchdogConfig
except ImportError:
    import os, sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from blueprint import create_receiver_blueprint  # type: ignore
    from player import MpvPlayer, ReceiverConfig  # type: ignore
    from watchdog import ReceiverWatchdog, WatchdogConfig  # type: ignore


__version__ = "0.9.0"

logger = logging.getLogger("picast-receiver")


def main() -> None:
    parser = argparse.ArgumentParser(description="PiCast Receiver")
    parser.add_argument("--port", type=int, default=5050)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    player = MpvPlayer(ReceiverConfig())
    watchdog = ReceiverWatchdog(player, WatchdogConfig())

    def _handle_shutdown(signum, _frame):
        logger.info("Shutting down (signal %d)...", signum)
        watchdog.stop()
        player.stop()
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, _handle_shutdown)
    signal.signal(signal.SIGINT, _handle_shutdown)

    app = Flask(__name__)
    app.register_blueprint(
        create_receiver_blueprint(
            player, watchdog=watchdog, version=__version__,
        ),
    )

    logger.info(
        "PiCast Receiver v%s starting on %s:%d",
        __version__, args.host, args.port,
    )
    watchdog.start()
    app.run(host=args.host, port=args.port, debug=args.debug)


if __name__ == "__main__":
    main()
