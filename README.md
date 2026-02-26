# PiCast

Turn any Raspberry Pi into a media center. Install on the Pi, control from your phone.

```
pip install picast
```

## What It Does

PiCast runs on a Raspberry Pi connected to your TV via HDMI. You control it from anywhere on your network:

- **Phone** - Dark mode web UI, works instantly on any device
- **Telegram** - Send YouTube URLs or commands from anywhere
- **Terminal** - Full TUI dashboard with keyboard shortcuts
- **API** - curl, scripts, or any HTTP client

## Architecture

```
+------------------------------+
|           YOUR TV            |
+--------------^---------------+
               | HDMI
+--------------+---------------+
|        RASPBERRY PI          |
|                              |
|   picast-server (:5050)      |
|     +-- REST API (Flask)     |
|     +-- mpv (video player)   |
|     +-- yt-dlp (YouTube)     |
|     +-- SQLite (library)     |
|     +-- Autoplay pools       |
|     +-- Pushover alerts      |
|     +-- Telegram bot         |
+--------------^---------------+
               | HTTP / mDNS
+--------------+---------------+
|  Phone: Web UI               |
|  Anywhere: Telegram          |
|  Mac: picast (TUI)           |
|  Multi-Pi: Tab to switch     |
+------------------------------+
```

## Quick Start

### 1. Install on Pi (one command)

```bash
curl -sSL https://raw.githubusercontent.com/JChanceLive/picast/main/install-pi.sh | bash
```

This installs mpv, yt-dlp, PiCast, and sets up a systemd service that starts on boot. The installer will ask if you want to run the setup wizard for optional features.

### 2. Open the Web UI

Navigate to `http://picast.local:5050` on your phone or laptop. Queue videos, control playback, browse your library.

### 3. Configure Optional Features

Run the interactive setup wizard anytime:

```bash
picast-setup
```

The wizard walks you through:
- **Pushover** - Push notifications to your phone for SD card alerts
- **YouTube Auth** - Sign in via Chromium cookies for age-restricted videos
- **PiPulse** - Connect to PiPulse for rich autoplay block metadata

All steps are optional. PiCast works without any of them.

### Optional: Install TUI on Mac

```bash
pip install "picast[tui]"
picast
```

### Optional: Set Up Telegram Bot

1. Message [@BotFather](https://t.me/BotFather) on Telegram to create a bot
2. Add the token to `~/.config/picast/picast.toml`:

```toml
[telegram]
bot_token = "123456:ABC-DEF..."
allowed_users = [YOUR_TELEGRAM_ID]
```

3. Restart: `sudo systemctl restart picast`

## Features

### Player Control

Play, pause, skip, seek, adjust volume and speed. Play any queued video instantly with the Play Now button. All from any client.

### Queue Management

Add URLs, reorder items with up/down arrows, play any video immediately, import YouTube playlists. Sleep timers and stop-after-current for unattended playback.

### Autoplay Pools

Create per-block video pools that play automatically on a schedule. Videos are weighted by ratings and play history:

- **Thumbs up/down** - Explicit rating adjusts weight (3x for liked, 0.1x for disliked)
- **Skip penalty** - Each skip reduces weight (0.7x cumulative, auto-shelve at 5 skips)
- **Completion boost** - Finishing a video naturally boosts its weight
- **Cross-block learning** - Videos you love in one block get suggested for others

Manage pools from the web UI Pool page or via `picast-pool` CLI.

### Block Metadata

Enrich autoplay blocks with display names, emojis, time ranges, and taglines. Edit blocks in the Settings page or import metadata from PiPulse with one click.

### Push Notifications (Pushover)

Get notified on your phone for SD card health alerts and daily summaries. Configure via `picast-setup` or edit `picast.toml` directly.

### History

Every video you watch is saved automatically. Search, favorite, add notes, re-queue old videos.

### Collections

Create named collections from your history. Import YouTube playlists as collections. Queue an entire collection with one command.

### Multi-Source

| Source | How | Example |
|--------|-----|---------|
| YouTube | Auto-detected | `https://youtube.com/watch?v=...` |
| Twitch | Auto-detected | `https://twitch.tv/username` |
| Local files | Path or browse | `/media/usb/movie.mp4` |

### Multi-Pi

Control multiple PiCast devices from one TUI or web session. Devices are found via:

- Config file: `[devices.living-room]` sections in `picast.toml`
- mDNS: auto-discovers other PiCast instances on your network

Press **Tab** in the TUI or use the dropdown in the web UI to switch.

## TUI Keybindings

| Key | Action |
|-----|--------|
| Space | Play / Pause |
| S | Skip |
| A | Add URL |
| +/- | Volume |
| <> | Speed |
| L | Library |
| P | Playlists |
| Tab | Switch device |
| D | Remove from queue |
| C | Clear played |
| ? | Help |
| Q | Quit |

## CLI Tools

| Command | Purpose |
|---------|---------|
| `picast-server` | Run the Pi server |
| `picast` | TUI dashboard (Mac) |
| `picast-setup` | Interactive setup wizard |
| `picast-pool` | Manage autoplay pools |
| `picast-export` | Export pools to YAML/JSON |

## API Reference

### Player

| Method | Endpoint | Body |
|--------|----------|------|
| GET | `/api/status` | - |
| POST | `/api/play` | `{"url": "..."}` |
| POST | `/api/pause` | - |
| POST | `/api/resume` | - |
| POST | `/api/toggle` | - |
| POST | `/api/skip` | - |
| POST | `/api/stop` | - |
| POST | `/api/seek` | `{"position": 30}` |
| POST | `/api/volume` | `{"level": 80}` |
| POST | `/api/speed` | `{"speed": 1.5}` |

### Queue

| Method | Endpoint | Body |
|--------|----------|------|
| GET | `/api/queue` | - |
| POST | `/api/queue/add` | `{"url": "..."}` |
| DELETE | `/api/queue/:id` | - |
| POST | `/api/queue/reorder` | `{"item_ids": [3,1,2]}` |
| POST | `/api/queue/replay` | `{"id": 1}` |
| POST | `/api/queue/import-playlist` | `{"url": "..."}` |
| POST | `/api/queue/clear-played` | - |
| POST | `/api/queue/clear` | - |

### Autoplay

| Method | Endpoint | Body |
|--------|----------|------|
| GET | `/api/autoplay` | - |
| POST | `/api/autoplay/trigger` | `{"block_name": "...", "display_name": "..."}` |
| POST | `/api/autoplay/rate` | `{"rating": 1}` |
| GET | `/api/autoplay/pool` | - |
| GET | `/api/autoplay/pool/:block` | - |
| POST | `/api/autoplay/pool/:block` | `{"url": "...", "title": "..."}` |
| DELETE | `/api/autoplay/pool/:block/:video_id` | - |
| GET | `/api/autoplay/suggestions/:block` | - |
| GET | `/api/autoplay/export` | - |
| POST | `/api/autoplay/import` | JSON pool data |

### Timers

| Method | Endpoint | Body |
|--------|----------|------|
| GET | `/api/timer` | - |
| POST | `/api/timer/stop-after-current` | `{"enabled": true}` |
| POST | `/api/timer/stop-in` | `{"minutes": 30}` |

### History

| Method | Endpoint | Body |
|--------|----------|------|
| GET | `/api/library` | - |
| GET | `/api/library/search?q=...` | - |
| GET | `/api/library/recent` | - |
| GET | `/api/library/:id` | - |
| PUT | `/api/library/:id/notes` | `{"notes": "..."}` |
| POST | `/api/library/:id/favorite` | - |
| POST | `/api/library/:id/queue` | - |
| DELETE | `/api/library/:id` | - |

### Collections

| Method | Endpoint | Body |
|--------|----------|------|
| GET | `/api/playlists` | - |
| POST | `/api/playlists` | `{"name": "..."}` |
| GET | `/api/playlists/:id` | - |
| PUT | `/api/playlists/:id` | `{"name": "..."}` |
| DELETE | `/api/playlists/:id` | - |
| POST | `/api/playlists/:id/items` | `{"library_id": 1}` |
| POST | `/api/playlists/:id/add-by-url` | `{"url": "..."}` |
| DELETE | `/api/playlists/:id/items/:lib_id` | - |
| POST | `/api/playlists/:id/queue` | - |
| POST | `/api/playlists/import-playlist` | `{"url": "..."}` |

### Settings

| Method | Endpoint | Body |
|--------|----------|------|
| GET | `/api/settings/setup-status` | - |
| GET | `/api/settings/pipulse` | - |
| POST | `/api/settings/pipulse` | `{"enabled": true, "host": "...", "port": 5055}` |
| GET | `/api/settings/blocks` | - |
| POST | `/api/settings/blocks` | `{"block_name": "...", "display_name": "..."}` |
| DELETE | `/api/settings/blocks/:name` | - |
| POST | `/api/settings/blocks/import` | - |

### Devices

| Method | Endpoint | Body |
|--------|----------|------|
| GET | `/api/devices` | - |
| GET | `/api/devices/:name` | - |
| GET | `/api/devices/:name/health` | - |

### Sources

| Method | Endpoint | Body |
|--------|----------|------|
| GET | `/api/sources` | - |
| POST | `/api/sources/detect` | `{"url": "..."}` |
| POST | `/api/sources/metadata` | `{"url": "..."}` |
| GET | `/api/sources/browse?path=...` | - |
| GET | `/api/sources/drives` | - |

## Configuration

Config file: `~/.config/picast/picast.toml` (or `./picast.toml`)

```toml
[server]
host = "0.0.0.0"
port = 5050
mpv_socket = "/tmp/mpv-socket"
ytdl_format = "bestvideo[height<=1080][fps<=30]+bestaudio/best[height<=1080]"
ytdl_cookies_from_browser = "chromium"  # YouTube auth

[pushover]
enabled = true
api_token = "your-app-token"
user_key = "your-user-key"

[pipulse]
enabled = true
host = "10.0.0.103"
port = 5055

[autoplay]
enabled = true
pool_mode = true
avoid_recent = 3
cross_block_learning = true

[telegram]
bot_token = "123456:ABC-DEF..."
allowed_users = [123456789]

[devices.living-room]
host = "picast-living.local"
port = 5050
default = true
```

## Installation Options

```bash
pip install picast              # Server only (for Pi)
pip install "picast[tui]"       # + Terminal UI (for Mac)
pip install "picast[telegram]"  # + Telegram bot
pip install "picast[discovery]" # + mDNS auto-discovery
pip install "picast[tui,telegram,discovery]"  # Everything
```

## Requirements

**Pi:** Raspberry Pi 3B+ or newer, Raspberry Pi OS, HDMI to TV, network
**YouTube:** Chromium cookies or PO token ([setup guide](docs/youtube-setup.md))
**Mac (TUI):** Python 3.9+, network access to Pi

## Development

```bash
git clone https://github.com/JChanceLive/picast.git
cd picast
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,tui,telegram]"
pytest tests/ -v
```

## License

MIT
