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

_Last updated: 2026-02-26 | 39 active memories, 145 total_

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
- PiCast v0.23.0 adopts `--video-sync=display-desync` instead of `--framedrop=vo`: testing confirmed that display-desyn... [picast, mpv, video-sync, performance, v0.23.0]

## Patterns & Conventions
- DiscoveryAgent uses same `APIClient` and `YouTubeAPI` pattern as YouTubeSource for code reuse; search_and_add() metho... [picast, autoplay, discovery, api-client, pattern]
- YouTube Discovery Agent module-level mocking pattern: @patch decorator targets 'picast.server.youtube_discovery.subpr... [picast, testing, mocking, pattern]
- PiCast hamburger navigation pattern: dice icon and pool emoji (calendar 📅) remain fixed in header, all other nav lin... [picast, web-ui, navigation, mobile, responsive]
- PiCast API endpoints for display rotation (`/api/system/display/rotate`) and volume (`/api/system/volume/set`) both u... [picast, api, mpv, ipc, socat]
- PiCast notification refactoring maintains consistent pattern across all alert points: motion_scan.py (_send_alert + c... [picam, pushover, notifications, pattern]
- AoE `command` field completely replaces default `tool: "claude"` behavior when set (not run alongside). All 19 AoE se... [ultra-claude-stack, aoe, configuration]
- Git branch cleanup protocol: branches created during development are deleted after merge because they serve scaffoldi... [git, workflow, safety]
- Bulk git operations across terminal ecosystem follow 3-tier structure: Tier 1 (gitignore only), Tier 2 (gitignore + d... [git, workflow, infrastructure]
- AoE wrapper script bash safety patterns: Empty python3 output in arithmetic expressions breaks bash ($(( - 0)) errors... [aoe, wrapper, bash, error-handling, signal-handling, ux]
- /done command captures session metadata for dashboard visibility and structured handoff. User workflow: auto-save hoo... [workflow, session-management, dashboard, aoe, session-history, logging, preferences]
- OSD disable polling pattern: use `for i in $(seq 1 30); do echo '{"command":["set_property","osd-level",0]}' | socat ... [picast, mpv, osd, socat, ipc]
- PiCast display rotation control hierarchy: kernel-level rotation via `video=HDMI-A-1:panel_orientation=upside_down` i... [picast, display, rotation, kms, performance, api, cmdline, sudo]
- PiCast autoplay pool initialization requires two-stage deployment: (1) Enable pool_mode in picast.toml [autoplay] sec... [picast, autoplay, deployment, pool-mode, configuration, sqlite, performance, ssh, pip-install, git, github, sudoers, automation]

## Gotchas & Pitfalls
- picast-update compares __version__ in src/picast/__about__.py against installed version and silently skips update if ... [picast, deployment, version-management]
- Telegram bots persist indefinitely and are NOT automatically deleted due to owner inactivity — bots can only be remov... [picast, pipulse, telegram, notifications, bot-lifecycle]
- TOML table scoping: keys appended after a `[table.subtable]` header are parsed as belonging to that table, not the pa... [picast, toml, config, deployment]
- iOS Safari PWA mode silently returns `false` from `confirm()` dialogs without displaying them; PiCast settings page r... [picast, web-ui, ios-safari, mobile, debugging]
- Wrapper script must trap SIGINT before running claude to ensure summary card displays even if user Ctrl+C during sess... [aoe, wrapper, signal-handling, ux]
- Mock patches in pytest must target the module where import occurs: @patch('picast.server.youtube_discovery.shutil.whi... [testing, mocking, pytest]
- Raspberry Pi hardware display/audio control gotchas: (1) `display_hdmi_rotate` in config.txt doesn't work with KMS dr... [picast, raspberry-pi, display, audio, kms, wlr-randr, wayland, systemd]
- mpv frame drop diagnostics gotchas: (1) `--framedrop=vo` + default `--video-sync=audio` is too strict for Pi's Waylan... [picast, mpv, frame-drops, video-sync, performance, wayland, osd, debugging]

## Current Progress
- PiCast v0.24.0 kernel-level display rotation validated on Pi: monitor displays correct orientation (right-side-up on ... [picast, display, rotation, v0.24.0, deployment, validation]
- PiCast v0.16.0-v0.19.0 complete: Pushover migration replaced ntfy across PiCast/PiCam/PiPulse with sound tier system ... [picast, autoplay, pool-mode, pushover, migration, web-ui, deployment]
- PiCast v0.20.0 YouTube Discovery Agent deployed and operational: DiscoveryAgent class with yt-dlp integration, theme-... [picast, discovery-agent, autoplay, v0.20.0, deployment, testing]
- PiCast v0.23.0 deployed to production: replaced `--framedrop=vo` + `--video-sync=audio` with `--video-sync=display-de... [picast, v0.23.0, deployment, frame-drops]
- PiCast v0.22.2 deployed to production: self-learning autoplay with schema v7 (skip_count, completion_count, watch_dur... [picast, v0.22.2, deployment, self-learning, overlay, performance]
- Ultra Claude Stack (3-layer automation: Memory Extractor + TUI/MCP integration + brain.md sync) is COMPLETE and live.... [ultra-claude-stack, automation, system-architecture]

## Context
- PiCast autoplay roadmap: Sessions 1-2 complete (pool system + web UI); Session 3 (optional) proposes YouTube discover... [picast, autoplay, roadmap, discovery-agent]
- User preference clarified: /done is the systematic session checkpoint (replaces /save); build full integration (JSONL... [workflow, preferences, priorities, session-management]
- User preference for /done workflow: maximize automation (auto-save handles metrics/memory capture) while using explic... [workflow, preferences, session-history]

_For deeper context, use memory_search, memory_related, or memory_ask tools._
<!-- MEMORY:END -->
