# Changelog

## [0.7.0] - 2026-02-12

### Added
- **Error tracking** - Failed videos now tracked in SQLite with error count, message, and timestamp (schema v3)
- **Server-Sent Events (SSE)** - Real-time push from server to Web UI via `GET /api/events` endpoint
- **EventBus** - Thread-safe event system persists events to DB and pushes to all SSE subscribers
- **mpv OSD** - On-screen display shows "Loading", "Now Playing", retry/fail messages on the TV via mpv `show-text`
- **Error classification** - Parses mpv exit codes and log files to identify 403s, extraction failures, timeouts, codec issues
- **Web UI error banner** - Real-time red error banner with title, detail, Retry and Dismiss buttons; auto-dismisses after 15s
- **Failed item display** - Queue shows failed items with error icon, error count, last error detail, and Retry button
- **Error API endpoints** - `GET /api/queue/failed`, `POST /api/queue/<id>/retry`, `POST /api/queue/clear-failed`
- **Recent events endpoint** - `GET /api/events/recent` returns last N events as JSON
- **Exponential backoff** - Retry delays escalate [1s, 5s, 30s] instead of flat 30s wait
- **DB backup** - Automatic SQLite backup every 6 hours via daemon thread
- **Systemd watchdog** - `sd_notify` READY/WATCHDOG protocol via raw Unix socket (no dependency needed)

### Changed
- `install-pi.sh` service now uses `Type=notify` with `WatchdogSec=30`
- Cascade "skip" action replaced with "failed" status that persists to DB
- Queue items now carry `error_count`, `last_error`, and `failed_at` fields
- Test suite enforces 80% code coverage via pytest-cov

### Fixed
- Silent video failures - errors now visible in Web UI, on TV (OSD), and in event log
- Rapid failure loops now properly tracked and marked as failed after 3 attempts

## [0.6.0] - 2026-02-12

### Added
- **Self-contained Pi operation** - Pi no longer depends on Mac for YouTube auth or code updates
- **YouTube auth config** - New `ytdl_cookies_from_browser` and `ytdl_po_token` fields in `picast.toml`, threaded through all yt-dlp and mpv calls
- **Auto-update system** - Daily check against GitHub `main` branch at 4 AM with systemd timer, automatic `pip install` + service restart
- **`picast-update` script** - Manual update trigger, installed to `~/.local/bin/`
- **Update status in health endpoint** - `/api/health` now includes last update log line
- **Deployment docs** - New `docs/deployment.md` covering auto-update and rsync coexistence

### Changed
- `install-pi.sh` now installs Chromium and sets `ytdl_cookies_from_browser = "chromium"` by default
- `install-pi.sh` adds sudoers entry for passwordless `systemctl restart picast`
- YouTube auth is now Pi-local (Chromium cookies or PO token) instead of Mac cookie export

### Removed
- Mac cookie export workflow from `docs/pi-network.md`

## [0.5.0] - 2026-02-12

### Added
- **Play Now button** - Play any queued video immediately from the web UI (green play button on each item)
- **Sleep timers** - Stop after current video, or sleep timer (30/60 min) with countdown display
- **Playlist import** - Import YouTube playlists directly into queue or as a saved collection
- **Floating controls** - Pin the control bar so it stays visible while scrolling the queue
- **Queue search** - Real-time filter/search within the queue list

### Fixed
- **Queue reorder jumping** - Moving items up/down no longer causes them to jump above the currently playing video. Reorder now reuses existing position slots instead of resetting from zero.

### Changed
- Renamed "Library" to "History" and "Playlists" to "Collections" throughout the UI
- "Play Again" button renamed to "Re-queue" to clarify it adds to end of queue, not immediate play

## [0.4.0] - 2026-02-11

### Added
- **Cyber neon UI** - Complete visual overhaul with magenta/cyan dark theme, glow effects, responsive mobile-first design
- **Slim control bar** - Horizontal layout with icon + label buttons (pause, skip, stop, volume, speed)
- **Queue thumbnails** - YouTube video thumbnails displayed inline in queue items
- **Video ID display** - Shows YouTube video ID on each queue item
- **Disaster recovery docs** - Documentation for common failure scenarios

### Fixed
- **Orphaned mpv on restart** - Properly kills mpv process when stopping/restarting playback
- **Broken stop_playback** - Fixed stop command to correctly halt queue processing
- **WAYLAND_DISPLAY passthrough** - mpv subprocess now receives Wayland environment for video output on Pi OS Bookworm
- **YouTube PO token** - Removed stale cookie auth, switched to PO token for reliable YouTube playback
- **Orphaned CI workflows** - Cleaned up unused GitHub Actions

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
- **Web UI** - Dark mode browser interface at http://picast.local:5050
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
