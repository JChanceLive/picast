# Changelog

## [0.3.1] - 2026-02-10

### Fixed
- **H.264 codec enforcement** - Force `[vcodec^=avc]` in yt-dlp format to avoid AV1/VP9 that Pi 3 can't hardware decode
- **Stale playing items** - `reset_stale_playing()` on startup prevents items stuck in "playing" state after crash
- **Stale mpv socket cleanup** - Remove orphaned socket file on startup if no mpv process is running

### Added
- **Cascade protection** - Detects rapid playback failures and backs off to prevent CPU thrashing
- **`ytdl_format_live` config** - Separate lower-quality format for live streams (Twitch)
- **Sync API client parity** - Added `delete_library_item`, `add_to_playlist`, `delete_playlist` to sync client

### Changed
- Default yt-dlp format now targets 720p30 H.264: `bestvideo[height<=720][fps<=30][vcodec^=avc]+bestaudio`
- Updated `picast.toml.example` with H.264 format and live stream format

## [0.3.0] - 2026-02-09

### Added
- **Wayland auto-detection** - Finds Wayland display socket for proper video output on Pi OS Bookworm
- **HDMI audio routing** - Auto-detects vc4hdmi ALSA device for direct HDMI audio output
- **Cascade protection** - Prevents rapid failure loops from thrashing CPU
- **Hardware decode** - `--hwdec=auto` flag for mpv, H.264 codec preference

### Fixed
- Frame drops on YouTube playback (was serving AV1, now forced to H.264)
- Flask threading safety for background player loop
- Web UI volume/speed controls now show real-time values from mpv

## [0.2.0] - 2026-02-08

### Added
- **SQLite Library** - Automatic play history with favorites, notes, search
- **Playlists** - Create, manage, and queue entire playlists
- **Web UI** - Dark mode browser interface at http://picast.local:5000
  - Player controls, library browser, playlist manager
  - Mobile-friendly responsive design
  - 1-second auto-refresh, keyboard shortcuts
- **Multi-Source Support** - Pluggable source handlers
  - YouTube (yt-dlp metadata extraction)
  - Twitch (streamlink integration)
  - Local files (browse directories, scan external drives)
  - Source auto-detection by URL pattern
- **Telegram Bot** - Remote control from anywhere
  - All player commands: /status, /play, /pause, /skip, /queue, /volume, /speed
  - Inline keyboard controls with live status updates
  - Auto-queue: send any URL to add it to the queue
  - User authorization whitelist
- **Multi-Pi Support** - Control multiple PiCast devices
  - Device registry in config file
  - mDNS/Zeroconf auto-discovery
  - TUI device switcher (Tab key)
  - Web UI device selector dropdown
- **GitHub Actions CI** - Test matrix across Python 3.9-3.14
- **PyPI publishing** - `pip install picast`

## [0.1.0] - 2026-02-07

### Added
- **Pi Server** - Flask REST API with 40+ endpoints
  - mpv JSON IPC control (play, pause, seek, volume, speed)
  - Thread-safe queue with JSON persistence
  - Background player loop with auto-advance
- **TUI Dashboard** - Textual terminal app
  - Now playing, queue list, controls
  - Keyboard shortcuts for all operations
- **One-Command Install** - `install-pi.sh` for Pi setup
- **Systemd Service** - Auto-start on boot
