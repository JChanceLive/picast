# PiCast

Turn any Raspberry Pi into a media center. Queue YouTube videos, local files, and Twitch streams from your terminal, phone, or Telegram.

```
pip install picast
```

## What It Does

PiCast runs on a Raspberry Pi connected to your TV via HDMI. You control it from anywhere on your network:

- **Terminal** - Full TUI dashboard with keyboard shortcuts
- **Browser** - Dark mode web UI on your phone or laptop
- **Telegram** - Send commands or URLs from anywhere
- **API** - curl, scripts, or any HTTP client

## Architecture

```
┌──────────────────────────────┐
│           YOUR TV            │
└──────────────▲───────────────┘
               │ HDMI
┌──────────────┴───────────────┐
│        RASPBERRY PI          │
│                              │
│   picast-server (:5000)      │
│     ├── REST API (Flask)     │
│     ├── mpv (video player)   │
│     ├── yt-dlp (YouTube)     │
│     ├── SQLite (library)     │
│     └── Telegram bot         │
└──────────────▲───────────────┘
               │ HTTP / mDNS
┌──────────────┴───────────────┐
│  Mac: picast (TUI)           │
│  Phone: Web UI               │
│  Anywhere: Telegram          │
│  Multi-Pi: Tab to switch     │
└──────────────────────────────┘
```

## Quick Start

### Install on Pi (one command)

```bash
curl -sSL https://raw.githubusercontent.com/JChanceLive/picast/main/install-pi.sh | bash
```

This installs mpv, yt-dlp, PiCast, and sets up a systemd service that starts on boot.

### Install TUI on Mac

```bash
pip install "picast[tui]"
picast
```

### Open Web UI

Navigate to `http://raspberrypi.local:5000` on any device.

### Set Up Telegram Bot

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

Play, pause, skip, seek, adjust volume and speed. All from any client.

### Queue Management

Add URLs, reorder items, remove individual videos, clear played history.

### Library

Every video you watch is saved automatically. Search, favorite, add notes, re-queue old videos.

### Playlists

Create named playlists from your library. Queue an entire playlist with one command.

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
| POST | `/api/queue/reorder` | `{"items": [3,1,2]}` |
| POST | `/api/queue/clear-played` | - |
| POST | `/api/queue/clear` | - |

### Library

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

### Playlists

| Method | Endpoint | Body |
|--------|----------|------|
| GET | `/api/playlists` | - |
| POST | `/api/playlists` | `{"name": "..."}` |
| GET | `/api/playlists/:id` | - |
| PUT | `/api/playlists/:id` | `{"name": "..."}` |
| DELETE | `/api/playlists/:id` | - |
| POST | `/api/playlists/:id/items` | `{"library_id": 1}` |
| DELETE | `/api/playlists/:id/items/:lib_id` | - |
| POST | `/api/playlists/:id/queue` | - |

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
port = 5000
mpv_socket = "/tmp/mpv-socket"
ytdl_format = "bestvideo[height<=720]+bestaudio/best[height<=720]"

[telegram]
bot_token = "123456:ABC-DEF..."
allowed_users = [123456789]

[devices.living-room]
host = "picast-living.local"
port = 5000
default = true

[devices.bedroom]
host = "picast-bedroom.local"
port = 5000
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
**Mac (TUI):** Python 3.9+, network access to Pi

## Development

```bash
git clone https://github.com/JChanceLive/picast.git
cd picast
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,tui,telegram]"
pytest tests/ -v
```

## Migrating from raspi-youtube-queue-player

```bash
curl -X POST http://raspberrypi.local:5000/api/import/queue-txt \
  -H "Content-Type: application/json" \
  -d '{"path": "/home/pi/video-queue/queue.txt"}'
```

## License

MIT
