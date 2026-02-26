# Resume: PiCast

Last updated: 2026-02-26

## State
v0.23.0 deployed to Pi. 594 tests pass. Full-featured YouTube queue player with autoplay pools, self-learning ratings, movie discovery, collections, Chrome extension, settings page, and persistent title overlay.

## Architecture
- **Pi (server):** Flask REST API + mpv player + SQLite (schema v8). Runs as systemd service on port 5050.
- **Mac (client):** Textual TUI connects to Pi's API. Also used for development.
- **Web UI:** Flask-served at `http://picast.local:5050`. PWA for mobile.
- **Chrome Extension:** Separate repo (`picast-extension/`). Sends Play/Queue requests to Pi.
- **Display:** Sceptre E20, 1600x900, physically upside-down. Kernel-level rotation via `cmdline.txt`.
- **Video sync:** `--video-sync=display-desync` (zero frame drops under Wayland).

## Next Action
No critical bugs. Current session work:
- Volume persistence (DB settings table) - DONE this session
- Wallpaper redesign (trimmed to 7 cards) - DONE this session
- Deploy as v0.23.1

## Key Features (v0.23.0)
- Queue management (add, reorder, replay, search, drag)
- YouTube, Twitch, Archive.org, and local file playback
- AutoPlay pools with self-learning (skip/completion tracking, weighted random)
- Archive.org movie discovery (genre, year, keyword search)
- Collections (saved playlists, import from YouTube)
- Sleep timer, stop-after-current, queue loop
- Playback history and library tracking
- Settings page (volume, display rotation, player controls, reboot)
- Persistent title overlay (OSD bottom-left)
- Chrome extension for quick queueing
- Telegram bot remote control (optional)
- SSE real-time events + error banners with retry
- Volume persistence across restarts (DB settings table, schema v8)

## Key Files
- `src/picast/server/app.py` - Flask routes, API endpoints, settings/display/reboot
- `src/picast/server/player.py` - mpv playback loop, IPC, autoplay integration
- `src/picast/server/database.py` - SQLite schema v8, migrations, retry with backoff, settings KV
- `src/picast/server/autoplay_pool.py` - Pool CRUD, weighted random, self-learning ratings
- `src/picast/server/discovery.py` - Archive.org movie discovery
- `src/picast/server/mpv_client.py` - mpv IPC client (JSON over Unix socket)
- `src/picast/server/queue_manager.py` - Queue persistence (SQLite)
- `src/picast/server/templates/player.html` - Queue page (now-playing, controls, add URL, queue list)
- `src/picast/server/templates/settings.html` - Settings page (volume, display, player)
- `src/picast/config.py` - Config loading from picast.toml
- `src/picast/__about__.py` - Version
- `scripts/generate-wallpaper.py` - Desktop wallpaper generator (3-column, 7 cards)

## Critical Knowledge
- **mpv IPC socket**: Takes 2-4s on Pi. Poll with 0.5s intervals, 10s timeout.
- **mpv v0.40 loadfile**: `["loadfile", url, flags, index, options]` -- index (int) required before options.
- **Comma-separated options**: ytdl-raw-options and CDN URLs break loadfile option parser. Use CLI args.
- **Two-phase idle polling**: Wait for idle=False (start), then idle=True (end). 150s timeout for Phase 1.
- **YouTube DRM**: ALL YouTube movies (paid + free) use Widevine. yt-dlp only gets trailer. Unfixable.
- **SD card I/O errors**: Pi SD cards have transient failures. DB retries with exponential backoff (0.5-8s).
- **SQLite locked DB**: Failed DML leaves implicit transaction open. Use `INSERT OR IGNORE` or always rollback.
- **display-desync**: Prevents frame drops under Wayland (default `audio` marks frames "late").
- **Kernel rotation**: `video=HDMI-A-1:panel_orientation=upside_down` in cmdline.txt. Requires reboot.
- **picast-update**: Compares `__about__.__version__` against GitHub. Must bump version for deploys.
- **Pool mode**: Two autoplay modes: legacy (toml mappings) and pool (SQLite, weighted random). Pool falls back to legacy when empty.
- **Self-learning weights**: `base * 0.7^skip_count * min(1.0 + completion_count * 0.2, 2.0)`. Auto-shelve at 5 skips.
- **iOS Safari PWA**: `confirm()` silently returns false. Use double-tap pattern instead.

## Session History
- Feb 26: v0.23.1 prep -- Volume persistence (DB settings table, schema v8), wallpaper redesign (7 cards, bigger fonts, icon branding). 594 tests.
- Feb 25: v0.23.0 -- OSD bottom-left, frame drops fixed (display-desync), settings page, all prior work merged. 591 tests.
- Feb 24: v0.22.0 -- Settings page (volume, display rotation, player controls, reboot). iOS Safari PWA fix.
- Feb 23: v0.21.2 -- AutoPlay self-learning (skip/completion tracking, weighted random, auto-shelve). Pool page UI.
- Feb 22: v0.21.0 -- AutoPlay pool system (add/remove/rate videos, weighted random selection, avoid-recent).
- Feb 21: v0.20.0 -- Archive.org catalog (Space 1999, Twilight Zone, etc.), catalog progress tracking.
- Feb 20: v0.19.0 -- Movie discovery (genre, year, keyword search from Archive.org). Discover modal UI.
- Feb 19: v0.13.3 -- SD card I/O retry, global JSON error handler, extension error display.
- Feb 18: v0.11-12 -- DRM detection, Archive.org source, thumbnail loading, Chrome extension v1.5.
- Feb 17: v0.8-10 -- Timestamp seek, IPC debugging, queue loop, video ID input.
- Feb 14: v0.8.0 -- Queue loop, watch counter, flicker fix.
- Feb 10: v0.4.0 -- PWA, mDNS, sleep timer, collections. 282 tests.
- Feb 09: Sessions 9-15. Wayland/HDMI, codec, Pi deploy, UI overhaul.
- Feb 08: Sessions 1-8. Foundation through PyPI publish. 169 tests.
