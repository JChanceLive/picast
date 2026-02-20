# Simplified: PiCast

## Layer 1: The Word

Remote

## Layer 2: The Sentence

Turn your TV into a smart screen by controlling a Raspberry Pi media player from your phone, Telegram, or terminal.

## Layer 3: The Paragraph

PiCast transforms a Raspberry Pi connected to your TV into a media center you control remotely. Instead of using a TV remote or typing on a TV interface, you manage everything from your phone, computer, or Telegram. Queue up YouTube videos, control playback, adjust volume, and organize collections -- all while the Pi does the heavy lifting of playing content on your big screen. It's like having a smart TV, but you built the brains yourself.

## Layer 4: The Page

**What It Is**

PiCast is software that runs on a Raspberry Pi connected to your TV via HDMI. You install it, and it starts a web server you can access from any device on your home network. Open the web interface on your phone, send a YouTube link, and it plays on your TV.

**Key Features**

- Queue multiple videos to play one after another
- Control playback: pause, skip, seek, adjust volume and speed
- Create named collections (like playlists) for easy access
- View your watch history and favorite videos
- Support for YouTube, Twitch, and local video files
- Dark mode web interface optimized for phones
- Control via Telegram messages from anywhere
- Terminal UI on Mac with keyboard shortcuts
- Auto-discovers other PiCast devices on your network

**Why It Matters**

Most smart TV interfaces are slow and frustrating. PiCast gives you a fast, phone-first interface while keeping the actual video processing on dedicated hardware. You get the control of a modern streaming app with the flexibility of open-source software you can customize.

**Technical Foundation**

PiCast uses Flask (web framework) for the control interface, mpv (video player) for playback, yt-dlp for YouTube downloads, and SQLite for queue and history storage. It runs as a systemd service that starts automatically when the Pi boots.

## Layer 5: The Textbook

### Architecture

PiCast is a client-server application where the Raspberry Pi acts as both the media playback device and the server hosting the control interface.

**Core Components:**

1. **Backend Server (Flask)**: REST API for all control operations. Manages the playback queue, interfaces with the media player, and serves the web UI on port 5050.

2. **Media Player (mpv)**: Command-line video player handling rendering and audio output. PiCast communicates via IPC socket, sending JSON commands for playback control.

3. **Download Engine (yt-dlp)**: Extracts direct video URLs from YouTube, Twitch, and other platforms. Handles authentication, format selection, and metadata extraction.

4. **Data Layer (SQLite)**: Stores queue, watch history, collections, and preferences. WAL mode with retry and backoff for SD card reliability.

### Control Interfaces

- **Web UI**: Responsive dark-mode SPA optimized for mobile. Real-time queue updates.
- **Telegram Bot**: Send YouTube links or commands from anywhere with internet.
- **Terminal TUI (Mac)**: Textual-based dashboard with keyboard shortcuts.
- **REST API**: Full HTTP API for programmatic control and automation.

### Multi-Pi Support

mDNS auto-discovery broadcasts presence on the local network. Multiple PiCast instances (bedroom, living room) appear in a unified control interface. Tab to switch between devices.

### Deployment

One-command install script handles mpv, yt-dlp, Python dependencies, and systemd service setup. The Pi auto-updates from GitHub main branch via `picast-update` command.
