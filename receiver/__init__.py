"""PiCast receiver package — Flask Blueprint factory + reference player.

Public API:
    create_receiver_blueprint(player, *, watchdog=None, version, ...)
    PlayerAdapter                  (Protocol — typing only)
    MpvPlayer, ReceiverConfig
    ReceiverWatchdog, WatchdogConfig

The standalone CLI lives in `picast_receiver.py` (`python -m picast.receiver`).
"""

from .blueprint import create_receiver_blueprint
from .player import MpvPlayer, PlayerAdapter, ReceiverConfig
from .watchdog import ReceiverWatchdog, WatchdogConfig

__all__ = [
    "create_receiver_blueprint",
    "PlayerAdapter",
    "MpvPlayer",
    "ReceiverConfig",
    "ReceiverWatchdog",
    "WatchdogConfig",
]
