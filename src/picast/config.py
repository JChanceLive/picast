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
    notification_chat_id: int = 0  # Chat ID for push notifications
    daily_summary_hour: int = 8  # Hour (0-23) for daily summary


@dataclass
class PushoverConfig:
    """Configuration for Pushover push notifications."""

    enabled: bool = False
    api_token: str = ""
    user_key: str = ""
    daily_summary_hour: int = 8


@dataclass
class PipulseConfig:
    """PiPulse integration for block metadata."""

    enabled: bool = False
    host: str = "10.0.0.110"
    port: int = 5055


@dataclass
class ThemeConfig:
    """Per-block search theme for the discovery agent."""

    queries: list[str] = field(default_factory=list)
    min_duration: int = 0  # seconds (0 = no minimum)
    max_duration: int = 0  # seconds (0 = no maximum)
    max_results: int = 5  # per query


@dataclass
class AutoplayConfig:
    """Block-to-video autoplay triggered by PiPulse webhooks."""

    enabled: bool = False
    pool_mode: bool = False
    avoid_recent: int = 3  # Don't repeat last N plays per block
    min_pool_size: int = 3  # Warn if pool drops below this
    cross_block_learning: bool = True  # Emit/consume cross-block signals
    mappings: dict[str, str] = field(default_factory=dict)
    # mappings: block_name -> URL (legacy single-URL fallback)
    themes: dict[str, ThemeConfig] = field(default_factory=dict)
    discovery_delay: float = 5.0  # seconds between yt-dlp calls


@dataclass
class FleetDeviceConfig:
    """Configuration for a fleet-mode autopilot device."""

    host: str = ""
    port: int = 5050
    room: str = ""
    mood: str = ""
    mute: bool = True  # Mute by default; unmute via POST /api/volume on device


@dataclass
class AutopilotConfig:
    """AI Autopilot configuration for autonomous content selection."""

    enabled: bool = False
    mode: str = "single"  # "single" or "fleet"
    queue_depth: int = 4  # target items ahead in queue
    pool_only: bool = False  # restrict to existing pools
    discovery_ratio: float = 0.3  # % picks from discovery vs pool
    stale_threshold_hours: int = 48  # profile age before fallback
    fleet_devices: dict[str, FleetDeviceConfig] = field(default_factory=dict)


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

    host: str = "::"
    port: int = 5050
    mpv_socket: str = "/tmp/mpv-socket"
    db_file: str = ""
    ytdl_format: str = "bestvideo[height<=720][fps<=30][vcodec^=avc]+bestaudio/best[height<=720]"
    ytdl_format_live: str = "best[height<=480][vcodec^=avc]/best[height<=480]"
    ytdl_cookies_from_browser: str = ""  # e.g. "chromium"
    ytdl_po_token: str = ""  # PO token for headless setups
    data_dir: str = ""
    mpv_hwdec: str = "auto"  # mpv hardware decoding (Pi: v4l2m2m)
    osd_enabled: bool = True  # Show OSD text on TV via mpv
    osd_duration_ms: int = 2500  # OSD display duration in ms
    db_backup_interval_hours: int = 6  # SQLite backup interval (0 = disabled)
    fallback_url: str = ""  # URL to loop when queue is empty (screensaver)
    fallback_title: str = "Screensaver"  # Display name for fallback OSD

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
    pushover: PushoverConfig = field(default_factory=PushoverConfig)
    pipulse: PipulseConfig = field(default_factory=PipulseConfig)
    autoplay: AutoplayConfig = field(default_factory=AutoplayConfig)
    autopilot: AutopilotConfig = field(default_factory=AutopilotConfig)
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
    search_paths.extend(
        [
            Path("picast.toml"),
            Path.home() / ".config" / "picast" / "picast.toml",
        ]
    )

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
            ytdl_cookies_from_browser=s.get(
                "ytdl_cookies_from_browser",
                config.server.ytdl_cookies_from_browser,
            ),
            ytdl_po_token=s.get("ytdl_po_token", config.server.ytdl_po_token),
            data_dir=s.get("data_dir", config.server.data_dir),
            mpv_hwdec=s.get("mpv_hwdec", config.server.mpv_hwdec),
            osd_enabled=s.get("osd_enabled", config.server.osd_enabled),
            osd_duration_ms=s.get("osd_duration_ms", config.server.osd_duration_ms),
            db_backup_interval_hours=s.get(
                "db_backup_interval_hours",
                config.server.db_backup_interval_hours,
            ),
            fallback_url=s.get("fallback_url", config.server.fallback_url),
            fallback_title=s.get("fallback_title", config.server.fallback_title),
        )

    if "telegram" in data:
        t = data["telegram"]
        config.telegram = TelegramConfig(
            bot_token=t.get("bot_token", ""),
            allowed_users=t.get("allowed_users", []),
            enabled=t.get("enabled", bool(t.get("bot_token"))),
            notification_chat_id=t.get("notification_chat_id", 0),
            daily_summary_hour=t.get("daily_summary_hour", 8),
        )

    if "pushover" in data:
        p = data["pushover"]
        config.pushover = PushoverConfig(
            enabled=p.get("enabled", False),
            api_token=p.get("api_token", ""),
            user_key=p.get("user_key", ""),
            daily_summary_hour=p.get("daily_summary_hour", config.pushover.daily_summary_hour),
        )

    if "pipulse" in data:
        pp = data["pipulse"]
        config.pipulse = PipulseConfig(
            enabled=pp.get("enabled", False),
            host=pp.get("host", config.pipulse.host),
            port=pp.get("port", config.pipulse.port),
        )

    if "autoplay" in data:
        a = data["autoplay"]
        themes = {}
        for block_name, t in a.get("themes", {}).items():
            themes[block_name] = ThemeConfig(
                queries=t.get("queries", []),
                min_duration=t.get("min_duration", 0),
                max_duration=t.get("max_duration", 0),
                max_results=t.get("max_results", 5),
            )
        config.autoplay = AutoplayConfig(
            enabled=a.get("enabled", config.autoplay.enabled),
            pool_mode=a.get("pool_mode", config.autoplay.pool_mode),
            avoid_recent=a.get("avoid_recent", config.autoplay.avoid_recent),
            min_pool_size=a.get("min_pool_size", config.autoplay.min_pool_size),
            mappings=dict(a.get("mappings", {})),
            themes=themes,
            discovery_delay=a.get("discovery_delay", config.autoplay.discovery_delay),
            cross_block_learning=a.get(
                "cross_block_learning", config.autoplay.cross_block_learning
            ),
        )

    if "autopilot" in data:
        ap = data["autopilot"]
        fleet_devices = {}
        fleet_data = ap.get("fleet", {}).get("devices", {})
        for dev_name, dev_conf in fleet_data.items():
            fleet_devices[dev_name] = FleetDeviceConfig(
                host=dev_conf.get("host", ""),
                port=dev_conf.get("port", 5050),
                room=dev_conf.get("room", ""),
                mood=dev_conf.get("mood", ""),
                mute=dev_conf.get("mute", True),
            )
        config.autopilot = AutopilotConfig(
            enabled=ap.get("enabled", config.autopilot.enabled),
            mode=ap.get("mode", config.autopilot.mode),
            queue_depth=ap.get("queue_depth", config.autopilot.queue_depth),
            pool_only=ap.get("pool_only", config.autopilot.pool_only),
            discovery_ratio=ap.get(
                "discovery_ratio", config.autopilot.discovery_ratio
            ),
            stale_threshold_hours=ap.get(
                "stale_threshold_hours",
                config.autopilot.stale_threshold_hours,
            ),
            fleet_devices=fleet_devices,
        )

    if "devices" in data:
        for name, d in data["devices"].items():
            config.devices.append(
                DeviceConfig(
                    name=name,
                    host=d.get("host", "raspberrypi.local"),
                    port=d.get("port", 5000),
                    default=d.get("default", False),
                )
            )

    return config


def ytdl_auth_args(config: ServerConfig) -> list[str]:
    """Build yt-dlp auth arguments from config.

    Priority: cookies_from_browser > po_token > no auth.
    """
    if config.ytdl_cookies_from_browser:
        return [f"--cookies-from-browser={config.ytdl_cookies_from_browser}"]
    if config.ytdl_po_token:
        return ["--extractor-args", f"youtube:player-client=web;po_token={config.ytdl_po_token}"]
    return []


def ytdl_raw_options_auth(config: ServerConfig) -> str:
    """Build mpv --ytdl-raw-options auth string from config.

    Returns a comma-separated string suitable for appending to existing
    ytdl-raw-options. Empty string if no auth configured.
    """
    if config.ytdl_cookies_from_browser:
        return f"cookies-from-browser={config.ytdl_cookies_from_browser}"
    if config.ytdl_po_token:
        return f"extractor-args=youtube:player-client=web;po_token={config.ytdl_po_token}"
    return ""
