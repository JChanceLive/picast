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

## Autoplay System (v0.24.4)

### How It Works

PiPulse sends `POST /api/autoplay/trigger` with `{"block_name": "...", "display_name": "..."}` on TIM block transitions. PiCast selects a weighted-random video from the pool for that block and plays it via mpv.

### Key Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/autoplay` | GET | Config, mappings, pool summaries |
| `/api/autoplay/trigger` | POST | Trigger playback for a block (PiPulse calls this) |
| `/api/autoplay/rate` | POST | Thumbs up/down from queue page UI |
| `/api/autoplay/pool/<block>` | GET | List videos in a block's pool |
| `/api/autoplay/pool/<block>` | POST | Add video to block pool |
| `/api/autoplay/suggestions/<block>` | GET | Cross-block suggestions |
| `/api/autoplay/export` | GET | Export all pools as JSON |
| `/api/autoplay/import` | POST | Import pools (merge mode) |
| `/api/status` | GET | Includes `autoplay_current` for UI rating buttons |

### Self-Learning Weight Formula

```
weight = base * skip_penalty * completion_boost
```

| Component | Formula | Notes |
|-----------|---------|-------|
| base | liked=3.0, neutral=1.0, disliked=0.1 | From explicit thumbs up/down |
| skip_penalty | 0.7^skip_count | Every skip penalizes, no time threshold |
| completion_boost | min(1 + completions*0.2, 2.0) | Natural playback to end or >80% duration |

- **Skip button** = always penalizes (user intent is clear)
- **Block transition / play new / manual override** = no penalty
- **Auto-shelve** at 5 skips (video effectively removed from rotation)

### Cross-Block Learning

Signals emitted on: thumbs-up (strength 1.5), 5th completion (strength 1.0). Suggestions appear for other blocks where the video doesn't already exist.

### Autoplay State Tracking (app.py)

Module-level dicts in `create_app()`:
- `_autoplay_current` — `{video_id, block_name, title}` for UI rating buttons
- `_autoplay_start_time` — monotonic timestamp
- `_autoplay_completing` — snapshot for deferred completion processing

**Critical:** `_handle_item_complete()` has a video_id guard (v0.24.2 fix) — verifies the completing item matches `_autoplay_current` before clearing it. This prevents a race condition where `play_now()` skip causes the old video's callback to wipe the new autoplay state.

### Testing Autoplay Manually

```bash
# Fire a trigger (simulates PiPulse block transition)
curl -X POST http://picast.local:5050/api/autoplay/trigger \
  -H "Content-Type: application/json" \
  -d '{"block_name": "morning-foundation", "display_name": "Morning Foundation"}'

# Check autoplay_current is set (needed for UI thumbs)
curl -s http://picast.local:5050/api/status | python3 -m json.tool

# Check pool data (ratings, skips, plays)
curl -s http://picast.local:5050/api/autoplay/pool/morning-foundation | python3 -m json.tool

# Check cross-block suggestions
curl -s http://picast.local:5050/api/autoplay/suggestions/creation-stack | python3 -m json.tool

# Export/import round-trip
curl -s http://picast.local:5050/api/autoplay/export -o /tmp/export.json
curl -X POST http://picast.local:5050/api/autoplay/import \
  -H "Content-Type: application/json" -d @/tmp/export.json
```

### play_duration Gotcha

`play_duration` from mpv includes buffering/loading time (measured from mpv process start, not playback start). A video that buffers for 30s then plays for 27s reports as 58s. This is why the skip penalty has no time threshold — time-based checks are unreliable with YouTube buffering.

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

_Last updated: 2026-02-26 | 45 active memories, 247 total_

## Architecture
- PiCast database access pattern: `self.queue._db` provides database access from player via queue_manager reference, en... [picast, database, player, architecture]
- Multi-backend notification pattern across Pi fleet: PiCast v0.14.0 NotificationManager requires `notification_chat_id... [picast, picam, pipulse, notifications, telegram, pushover, architecture]
- PiCast Discovery Agent uses YouTube API (yt-dlp) to populate autoplay pools based on theme-based search queries confi... [picast, autoplay, discovery, youtube, architecture]
- PiCast persistent title overlay uses mpv OSD level 3 with `--osd-status-msg=${media-title}` positioned bottom-left (a... [picast, mpv, osd, overlay, ui]
- v1.0.0 block metadata flow: PiPulse exposes new `/api/block-metadata/{block_name}` endpoint returning JSON {display_n... [picast, pipulse, api-design, web-ui, metadata, v1.0.0]
- PiCast PiPulseClient lightweight design: fetch_blocks() calls PiPulse GET /api/pitim/blocks with 5s timeout + exponen... [picast, pipulse, client, api, architecture]
- PiCast /api/autoplay/blocks/{block_name} endpoints (S2): GET returns block metadata {display_name, emoji, description... [picast, api-design, block-metadata, crud, database, v1.0.0]

## Key Decisions
- Catalog uses Archive.org public domain shows (Space 1999, Twilight Zone) instead of copyrighted content (Stargate SG-... [picast, catalog, archive-org]
- Discovery Agent implemented as separate class in new `src/picast/server/sources/discovery.py` (not integrated into Yo... [picast, autoplay, discovery, design, separation-of-concerns]
- Pushover chosen as ntfy replacement: provides proper APNS infrastructure for reliable iOS background push, one-time $... [pushover, ntfy, notifications, ios-push, decision, trade-offs]
- Kernel-level `panel_orientation=upside_down` in /boot/firmware/cmdline.txt chosen for display rotation over firmware ... [picast, display, rotation, kms, performance]
- PiCast idle TV wallpaper redesign decision: User requested viewing box contents before trimming; after review, 7 boxe... [picast, wallpaper, tv-ui, design, decision]
- v1.0.0 architecture uses three-phase implementation: S1 (PiPulse API endpoint + block_metadata table), S2 (PiCast poo... [picast, v1.0.0, architecture, ux-design]

## Patterns & Conventions
- DiscoveryAgent uses same `APIClient` and `YouTubeAPI` pattern as YouTubeSource for code reuse; search_and_add() metho... [picast, autoplay, discovery, api-client, pattern]
- PiCast hamburger navigation pattern: dice icon and pool emoji (calendar 📅) remain fixed in header, all other nav lin... [picast, web-ui, navigation, mobile, responsive]
- PiCast feature flag wiring pattern: New boolean config flags in picast.toml [autoplay] section (e.g., seasonal_rotati... [picast, config, feature-flags, autoplay, pattern]
- Autoplay trigger validation pattern: extract video_id from QueueItem.url using extract_video_id() utility before savi... [picast, autoplay, queue, pattern]
- PiCast CLI command aliases via pyproject.toml [project.scripts]: `pycast export` (replaces `picast autoplay export`) ... [picast, cli, entry-points, pattern]
- PiCast pool page immediate playback pattern: `playPoolVideo(videoId)` JavaScript function sends POST to `/api/play` w... [picast, web-ui, autoplay, javascript, api-pattern]
- PiCast database access pattern: Player accesses database via `self.queue._db` shared reference from queue_manager. Vo... [picast, database, player, architecture]
- PiCast block_metadata import/export pattern: Database.get_all_block_metadata() returns flat dict {block_name: {displa... [picast, database, export, pattern]
- PiPulse /api/pitim/blocks endpoint response includes optional schedule data structure: {block_name, display_name, emo... [pipulse, picast, api-design, error-handling]
- PiCast pool page block card rendering pattern: fetchAndRenderPoolBlocks() queries /api/autoplay/pool/{block_name} end... [picast, web-ui, pool-page, block-metadata, javascript, api-pattern]
- PiCast block metadata editor (settings page) pattern: blockEditorModal shows form with fields {display_name text inpu... [picast, settings-page, block-metadata, web-ui, crud, javascript, form-handling]
- PiCast pool API enrichment pattern: /api/autoplay/pool/{block_name} response restructured to include block_meta key a... [picast, api-design, pool-endpoint, response-structure]
- PiCast pool.html grid layout pattern: 3-column responsive layout with video cards showing title, thumbnail, ratings (... [picast, web-ui, responsive-design, grid-layout]
- PiCast block metadata CRUD pattern: /api/settings/blocks GET lists all metadata entries (admin page), POST creates/up... [picast, api-design, web-ui, crud-pattern]

## Gotchas & Pitfalls
- Telegram bots persist indefinitely and are NOT automatically deleted due to owner inactivity — bots can only be remov... [picast, pipulse, telegram, notifications, bot-lifecycle]
- iOS Safari PWA mode silently returns `false` from `confirm()` dialogs without displaying them; PiCast settings page r... [picast, web-ui, ios-safari, mobile, debugging]
- Wrapper script must trap SIGINT before running claude to ensure summary card displays even if user Ctrl+C during sess... [aoe, wrapper, signal-handling, ux]
- Mock patches in pytest must target the module where import occurs: @patch('picast.server.youtube_discovery.shutil.whi... [testing, mocking, pytest]
- TOML table scoping: keys appended after a `[table.subtable]` header are parsed as belonging to that table, not the pa... [picast, toml, config, deployment]
- Autoplay rating thumbs race condition: when trigger endpoint calls play_now() to interrupt current video, the player'... [picast, autoplay, race-condition, web-ui, player, queue]
- Skip penalty threshold removed entirely (v0.24.4) — skip button now always penalizes, regardless of play_duration. Or... [picast, autoplay, self-learning, timing, buffering]
- test_autoplay_pool.py assertion checking SCHEMA_VERSION expects v10 but fixture auto-creates v9 database; when schema... [picast, testing, schema-migration, database]

## Current Progress
- PiCast v1.0.0 Session 2 (pool.html UI enrichment + web block editor) COMPLETE: /api/autoplay/pool endpoint enriched w... [picast, v1.0.0, pool-ui, block-metadata, web-ui, api-design, progress]
- PiCast v1.0.0 Session 2 (pool.html + settings block editor) COMPLETE: /api/autoplay/blocks CRUD endpoints implemented... [picast, v1.0.0, release, s2, pool-ui, settings-editor, deployment]
- PiCast v0.26.0 + PiPulse block metadata endpoint deployed and verified in production: /api/pitim/blocks endpoint live... [picast, pipulse, v0.26.0, deployment, verification]
- Session 1 (PiPulse API endpoint + PiCast block_metadata foundation) plan written and approved; ready for implementati... [picast, v1.0.0, pipulse, api-design, savepoint]
- PiCast v0.24.4 deployed with autoplay self-learning system validated in production: skip penalty (0.7^skip_count, no ... [picast, autoplay, self-learning, validation, v0.24.4]
- Ultra Claude Stack (3-layer automation: Memory Extractor + TUI/MCP integration + brain.md sync) is COMPLETE and live.... [ultra-claude-stack, automation, system-architecture]

## Context
- PiCast autoplay roadmap: Sessions 1-2 complete (pool system + web UI); Session 3 (optional) proposes YouTube discover... [picast, autoplay, roadmap, discovery-agent]
- Next phase planning: v1.0.0 + PO token auto-setup is pending, awaiting user vision/direction for implementation scope... [picast, roadmap, v1.0.0, planning]
- User preference clarified: /done is the systematic session checkpoint (replaces /save); build full integration (JSONL... [workflow, preferences, priorities, session-management]
- User preference for /done workflow: maximize automation (auto-save handles metrics/memory capture) while using explic... [workflow, preferences, session-history]

_For deeper context, use memory_search, memory_related, or memory_ask tools._
<!-- MEMORY:END -->
