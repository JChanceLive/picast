"""Configuration loader for PiCast."""

import os
from dataclasses import dataclass, field
from pathlib import Path

try:
    import tomllib
except ImportError:
    import tomli as tomllib  # Python 3.9-3.10 fallback


@dataclass
class TelegramConfig:
    """Configuration for the Telegram bot."""

    bot_token: str = ""
    allowed_users: list[int] = field(default_factory=list)
    enabled: bool = False


@dataclass
class DeviceConfig:
    """Configuration for a single Pi device."""

    name: str = "default"
    host: str = "raspberrypi.local"
    port: int = 5000
    default: bool = True


@dataclass
class ServerConfig:
    """Configuration for the Pi server."""

    host: str = "0.0.0.0"
    port: int = 5050
    mpv_socket: str = "/tmp/mpv-socket"
    db_file: str = ""
    ytdl_format: str = "bestvideo[height<=720][fps<=30][vcodec^=avc]+bestaudio/best[height<=720]"
    ytdl_format_live: str = "bestvideo[height<=480][vcodec^=avc]+bestaudio/best[height<=480]"
    data_dir: str = ""

    def __post_init__(self):
        if not self.data_dir:
            self.data_dir = os.path.expanduser("~/.picast")
        if not self.db_file:
            self.db_file = os.path.join(self.data_dir, "picast.db")


@dataclass
class Config:
    """Top-level PiCast configuration."""

    server: ServerConfig = field(default_factory=ServerConfig)
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    devices: list[DeviceConfig] = field(default_factory=list)

    def get_default_device(self) -> DeviceConfig:
        for d in self.devices:
            if d.default:
                return d
        if self.devices:
            return self.devices[0]
        return DeviceConfig()


def load_config(path: str | None = None) -> Config:
    """Load configuration from picast.toml.

    Search order:
    1. Explicit path argument
    2. ./picast.toml
    3. ~/.config/picast/picast.toml
    4. Defaults
    """
    search_paths = []
    if path:
        search_paths.append(Path(path))
    search_paths.extend([
        Path("picast.toml"),
        Path.home() / ".config" / "picast" / "picast.toml",
    ])

    for p in search_paths:
        if p.exists():
            with open(p, "rb") as f:
                data = tomllib.load(f)
            return _parse_config(data)

    return Config()


def _parse_config(data: dict) -> Config:
    """Parse a TOML dict into Config."""
    config = Config()

    if "server" in data:
        s = data["server"]
        config.server = ServerConfig(
            host=s.get("host", config.server.host),
            port=s.get("port", config.server.port),
            mpv_socket=s.get("mpv_socket", config.server.mpv_socket),
            db_file=s.get("db_file", config.server.db_file),
            ytdl_format=s.get("ytdl_format", config.server.ytdl_format),
            ytdl_format_live=s.get("ytdl_format_live", config.server.ytdl_format_live),
            data_dir=s.get("data_dir", config.server.data_dir),
        )

    if "telegram" in data:
        t = data["telegram"]
        config.telegram = TelegramConfig(
            bot_token=t.get("bot_token", ""),
            allowed_users=t.get("allowed_users", []),
            enabled=t.get("enabled", bool(t.get("bot_token"))),
        )

    if "devices" in data:
        for name, d in data["devices"].items():
            config.devices.append(DeviceConfig(
                name=name,
                host=d.get("host", "raspberrypi.local"),
                port=d.get("port", 5000),
                default=d.get("default", False),
            ))

    return config
