# PiCast - CLAUDE.md

Project-specific guidance for Claude Code when working on PiCast.

## Project Overview

PiCast is a YouTube queue player for Raspberry Pi. Mac runs the TUI client, Pi runs the server + mpv.

## BANNED Test Videos

**NEVER use these URLs for testing playback:**
- `https://www.youtube.com/watch?v=dQw4w9WgXcQ` (Rick Astley - Never Gonna Give You Up)

Use a short Creative Commons or public domain video instead when testing playback.

## Pi Device

| Key | Value |
|-----|-------|
| IP | DHCP (use `picast.local` to avoid stale IPs) |
| SSH User | `jopi` |
| Hostname | `picast` |
| SSH | `ssh picast` (uses ~/.ssh/config Host entry with key auth) |
| Service | `sudo systemctl restart picast` |
| Logs | `journalctl -u picast -f` |
| Config | `~/.config/picast/picast.toml` |
| Data | `~/.picast/picast.db` |
| Port | `5050` (changed from 5000 in Session 11) |

## Architecture

- **Pi (server):** Flask REST API + mpv player + SQLite. Runs as systemd service.
- **Mac (client):** Textual TUI connects to Pi's API. Also used for development.
- **Web UI:** Served by Flask at `http://picast.local:5050`
- **Chrome Extension:** Separate repo at `~/Documents/Projects/Claude/terminal/picast-extension/`. Sends Play/Queue requests to Pi. Has its own git remote.

## Key Files

| File | Purpose |
|------|---------|
| `src/picast/server/app.py` | Flask routes, API endpoints, settings/display/reboot |
| `src/picast/server/database.py` | SQLite schema (v7) + migrations + retry with backoff |
| `src/picast/server/queue_manager.py` | Queue persistence (SQLite) |
| `src/picast/server/player.py` | mpv playback loop (`--video-sync=display-desync`) |
| `src/picast/server/autoplay_pool.py` | Autoplay pool system with self-learning ratings |
| `src/picast/server/discovery.py` | YouTube Discovery Agent (yt-dlp theme search) |
| `src/picast/config.py` | Config loading from picast.toml |
| `src/picast/cli.py` | CLI entry points |
| `src/picast/tui/app.py` | Textual TUI |
| `src/picast/server/templates/settings.html` | Settings page (volume, display, player controls) |
| `install-pi.sh` | One-command Pi setup |

## Development Workflow

1. Edit code on Mac
2. Run tests: `cd picast && source .venv/bin/activate && pytest tests/ -v`
3. Test web UI locally: `./run.sh` (starts server with --no-player --quiet)
4. Deploy to Pi: `ssh picast "export PATH=\$HOME/.local/bin:\$PATH && picast-update"`

## Naming Convention (Session 11)

| Old Name | New Name |
|----------|----------|
| Player tab | Queue |
| Library | History |
| Playlists | Collections |

## Port

Default port is 5050 (changed from 5000 in Session 11).

## Version Bumping

**Always bump `src/picast/__about__.py`** when making changes. The Pi auto-updater compares this against GitHub main. If the version doesn't change, updates are silently skipped.

## Deploy Workflow (Quick Reference)

```bash
# 1. Run tests locally
.venv/bin/python -m pytest tests/ -x -q

# 2. Bump version in src/picast/__about__.py

# 3. Commit and push
git add -A && git commit -m "..." && git push origin main

# 4. Deploy to Pi
ssh picast "export PATH=\$HOME/.local/bin:\$PATH && picast-update"

# 5. Restart service
ssh picast "sudo systemctl restart picast"

# 6. Verify
curl -s http://picast.local:5050/api/health | python3 -m json.tool
```

## Debugging on Pi

```bash
# Live server logs
ssh picast "journalctl -u picast -f"

# Recent errors (last 6 hours)
ssh picast "journalctl -u picast --since '6 hours ago' --no-pager | grep -i error"

# Database health check
ssh picast "sqlite3 ~/.picast/picast.db 'PRAGMA integrity_check; PRAGMA wal_checkpoint(TRUNCATE);'"

# Service status
ssh picast "systemctl is-active picast"
```

## Chrome Extension (picast-extension)

Separate repo: `~/Documents/Projects/Claude/terminal/picast-extension/`
Remote: `git@github.com:JChanceLive/picast-extension.git`

| File | Purpose |
|------|---------|
| `popup.js` | Extension logic: discovery, timestamp capture, API calls |
| `popup.html` | Extension UI |
| `manifest.json` | Manifest V3 config (v1.5.0) |

**After editing popup.js:** User must reload extension in `chrome://extensions` (no auto-update for local extensions).

**Extension -> Server flow:**
1. Extension calls `POST /api/play` or `POST /api/queue/add`
2. Server returns JSON `{"ok": true}` or `{"error": "..."}` with status code
3. Extension shows error message from response body (v1.5.1+)

## Pi Hardware

| Component | Detail |
|-----------|--------|
| Kernel | 6.12.62 |
| Compositor | labwc (Wayland) |
| GPU driver | vc4-kms-v3d, 256MB VRAM |
| Monitor | Sceptre E20, 1600x900@60Hz, HDMI-A-1, physically mounted upside-down |
| mpv | v0.40.0, uses `--video-sync=display-desync` (zero frame drops) |
| User | `jopi` (passwordless sudo) |

## Display Rotation

The monitor is physically upside-down. Rotation is handled at **kernel level** via `/boot/firmware/cmdline.txt`:

```
video=HDMI-A-1:panel_orientation=upside_down
```

**What does NOT work:**
- `display_hdmi_rotate=2` in config.txt (incompatible with vc4-kms-v3d)
- `wlr-randr --transform 180` (works but causes frame drops - compositor per-frame transform)
- `--vo=drm` or `--vo=dmabuf-wayland` (labwc holds DRM master)

**Rotation changes require a reboot.** The settings page has a double-tap "Apply & Reboot" button.

## mpv Configuration

| Setting | Value | Why |
|---------|-------|-----|
| `--video-sync` | `display-desync` | Prevents frame drops (default `audio` marks frames "late" under Wayland) |
| `--osd-level` | `3` | Persistent title overlay |
| `--osd-status-msg` | `${media-title}` | Shows video title bottom-left |

**Frame drop history:** `--framedrop=vo` + `--video-sync=audio` caused ~22 drops/sec. Root cause was Wayland compositor latency making frames appear "late". `display-desync` decouples video from audio sync = 0 drops. Pi GPU handles 720p24 fine.

## Known Pi Issues

### Transient SD Card I/O Errors

The Pi's SD card occasionally has transient `disk I/O error` on SQLite operations. The database layer retries with backoff (0.5s, 2s delays) to handle this. If both retries fail, the error surfaces as a JSON 500 response.

**If recurring:** Check SD card health with `ssh picast "sudo dmesg | grep -i mmc"`. May need SD card replacement.

### Error Handling

- `app.py` has a global `@app.errorhandler(Exception)` that converts unhandled exceptions to JSON `{"error": "..."}` responses
- `/api/play` and `/api/queue/add` have explicit try/except for clear error messages
- Extension parses error response body to show actual error text (not just "Error (500)")

<!-- MEMORY:START -->
# picast

_Last updated: 2026-02-26 | 31 active memories, 196 total_

## Architecture
- PiCast database access pattern: `self.queue._db` provides database access from player via queue_manager reference, en... [picast, database, player, architecture]
- Multi-backend notification pattern across Pi fleet: PiCast v0.14.0 NotificationManager requires `notification_chat_id... [picast, picam, pipulse, notifications, telegram, pushover, architecture]
- PiCast Discovery Agent uses YouTube API (yt-dlp) to populate autoplay pools based on theme-based search queries confi... [picast, autoplay, discovery, youtube, architecture]
- PiCast persistent title overlay uses mpv OSD level 3 with `--osd-status-msg=${media-title}` positioned bottom-left (a... [picast, mpv, osd, overlay, ui]

## Key Decisions
- Catalog uses Archive.org public domain shows (Space 1999, Twilight Zone) instead of copyrighted content (Stargate SG-... [picast, catalog, archive-org]
- Discovery Agent implemented as separate class in new `src/picast/server/sources/discovery.py` (not integrated into Yo... [picast, autoplay, discovery, design, separation-of-concerns]
- Pushover chosen as ntfy replacement: provides proper APNS infrastructure for reliable iOS background push, one-time $... [pushover, ntfy, notifications, ios-push, decision, trade-offs]
- Kernel-level `panel_orientation=upside_down` in /boot/firmware/cmdline.txt chosen for display rotation over firmware ... [picast, display, rotation, kms, performance]
- PiCast idle TV wallpaper redesign decision: User requested viewing box contents before trimming; after review, 7 boxe... [picast, wallpaper, tv-ui, design, decision]

## Patterns & Conventions
- DiscoveryAgent uses same `APIClient` and `YouTubeAPI` pattern as YouTubeSource for code reuse; search_and_add() metho... [picast, autoplay, discovery, api-client, pattern]
- YouTube Discovery Agent module-level mocking pattern: @patch decorator targets 'picast.server.youtube_discovery.subpr... [picast, testing, mocking, pattern]
- PiCast hamburger navigation pattern: dice icon and pool emoji (calendar 📅) remain fixed in header, all other nav lin... [picast, web-ui, navigation, mobile, responsive]
- PiCast volume persistence pattern: Store volume in new `settings` table (schema v8) with key-value pairs, save via `/... [picast, volume, persistence, database, mpv, architecture]
- Git branch cleanup protocol: branches created during development are deleted after merge because they serve scaffoldi... [git, workflow, safety, infrastructure]
- PiCast display rotation control hierarchy: kernel-level rotation via `video=HDMI-A-1:panel_orientation=upside_down` i... [picast, display, rotation, kms, performance, api, mpv, ipc, socat, cmdline, sudo, osd]
- AoE wrapper script bash safety patterns: Empty python3 output in arithmetic expressions breaks bash ($(( - 0)) errors... [aoe, wrapper, bash, error-handling, signal-handling, ux, workflow, session-management, dashboard, session-history, logging, preferences]
- PiCast autoplay pool initialization requires two-stage deployment: (1) Enable pool_mode in picast.toml [autoplay] sec... [picast, autoplay, deployment, pool-mode, configuration, sqlite, performance, ssh, pip-install, git, github, sudoers, automation]
- PiCast wallpaper generation deployment requires three-step process: (1) scp icon.png to Pi ~/.picast/icon.png, (2) sc... [picast, deployment, wallpaper, script-execution, optional-dependencies, pattern]
- PiCast feature flag wiring pattern: New boolean config flags in picast.toml [autoplay] section (e.g., seasonal_rotati... [picast, config, feature-flags, autoplay, pattern]

## Gotchas & Pitfalls
- Telegram bots persist indefinitely and are NOT automatically deleted due to owner inactivity — bots can only be remov... [picast, pipulse, telegram, notifications, bot-lifecycle]
- iOS Safari PWA mode silently returns `false` from `confirm()` dialogs without displaying them; PiCast settings page r... [picast, web-ui, ios-safari, mobile, debugging]
- Wrapper script must trap SIGINT before running claude to ensure summary card displays even if user Ctrl+C during sess... [aoe, wrapper, signal-handling, ux]
- Mock patches in pytest must target the module where import occurs: @patch('picast.server.youtube_discovery.shutil.whi... [testing, mocking, pytest]
- TOML table scoping: keys appended after a `[table.subtable]` header are parsed as belonging to that table, not the pa... [picast, toml, config, deployment]

## Current Progress
- PiCast v0.24.0 Session 3 COMPLETE: Cross-block learning fully integrated with 663/663 tests passing. Signal emission ... [picast, v0.24.0, cross-block-learning, testing, release-ready]
- PiCast v0.23.3 released with volume persistence (DB settings table schema v8), TV wallpaper redesign (7 kept cards, 1... [picast, release, wallpaper, volume, systemd]
- Volume persistence implemented: added `settings` table to schema (v8) with `get_setting`/`set_setting` methods in Dat... [picast, volume-persistence, database, schema-v8]
- Ultra Claude Stack (3-layer automation: Memory Extractor + TUI/MCP integration + brain.md sync) is COMPLETE and live.... [ultra-claude-stack, automation, system-architecture]

## Context
- PiCast autoplay roadmap: Sessions 1-2 complete (pool system + web UI); Session 3 (optional) proposes YouTube discover... [picast, autoplay, roadmap, discovery-agent]
- User preference clarified: /done is the systematic session checkpoint (replaces /save); build full integration (JSONL... [workflow, preferences, priorities, session-management]
- User preference for /done workflow: maximize automation (auto-save handles metrics/memory capture) while using explic... [workflow, preferences, session-history]

_For deeper context, use memory_search, memory_related, or memory_ask tools._
<!-- MEMORY:END -->
