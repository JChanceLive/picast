# PiCast - CLAUDE.md

Project-specific guidance for Claude Code when working on PiCast.

## Project Overview

PiCast is a YouTube queue player for Raspberry Pi. Mac runs the TUI client, Pi runs the server + mpv.

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
| `src/picast/server/app.py` | Flask routes and app wiring |
| `src/picast/server/database.py` | SQLite schema (v3) + migrations |
| `src/picast/server/queue_manager.py` | Queue persistence (SQLite) |
| `src/picast/server/player.py` | mpv playback loop |
| `src/picast/config.py` | Config loading from picast.toml |
| `src/picast/cli.py` | CLI entry points |
| `src/picast/tui/app.py` | Textual TUI |
| `install-pi.sh` | One-command Pi setup |
| `src/picast/server/database.py` | SQLite schema (v4) + migrations + retry with backoff |

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

_Last updated: 2026-02-25 | 28 active memories, 39 total_

## Architecture
- PiCast v0.14.0 NotificationManager requires `notification_chat_id` configuration for SD card health alerts and daily ... [picast, pipulse, notifications, telegram]
- AoE wrapper script (`~/.claude/scripts/aoe-session-wrapper.sh`) captures post-session state: reads tool-count.json, e... [ultra-claude-stack, aoe, wrapper, session-management]
- Session history integration uses 3-layer pipeline: /done command writes handoff data → wrapper script generates JSONL... [aoe, session-history, wrapper, dashboard]
- PiCast database access pattern: `self.queue._db` provides database access from player via queue_manager reference, en... [picast, database, player, architecture]
- PiCam multi-backend notification pattern: pushover_util.py exports send_pushover_alert() factory function that accept... [picam, pushover, notifications, architecture]

## Key Decisions
- Catalog uses Archive.org public domain shows (Space 1999, Twilight Zone) instead of copyrighted content (Stargate SG-... [picast, catalog, archive-org]
- PiPulse (10.0.0.103, Pi 4+) chosen as best candidate for ntfy.sh self-hosting migration over other fleet members due ... [pipulse, telegram, notifications, infrastructure]
- Pushover chosen as ntfy replacement: provides proper APNS infrastructure for reliable iOS background push, one-time $... [pushover, ntfy, notifications, ios-push, decision, trade-offs]

## Patterns & Conventions
- AoE `command` field completely replaces default `tool: "claude"` behavior when set (not run alongside). All 19 AoE se... [ultra-claude-stack, aoe, configuration]
- Empty python3 output in wrapper arithmetic expressions breaks bash ($(( - 0)) errors); all python variable assignment... [aoe, wrapper, bash, error-handling]
- User preference: single-line session logs with dense metadata (tool counts, memory diffs, key actions) for reference ... [session-history, logging, workflow]
- /done command should be used systematically to capture session metadata for dashboard visibility; both automatic savi... [workflow, session-management, dashboard, aoe]
- Session history integration prioritizes /history command for AoE layer 4 reference, with Command Center dashboard Pro... [aoe, dashboard, command-center, session-history, architecture]
- PiCast NotificationManager initialization in cli.py occurs after telegram_bot is started, injected with bot reference... [picast, notifications, initialization]
- PiCam notification refactoring maintains consistent pattern across all alert points: motion_scan.py (_send_alert + ch... [picam, pushover, notifications, pattern]
- GitHub raw CDN caches __about__.py for ~5 minutes after push; for immediate Pi deployment after version bumps, use di... [picast, deployment, git, github]

## Gotchas & Pitfalls
- Telegram bots can be frozen (not deleted) by Telegram enforcement for message volume violations — PiRelay bot was fro... [picast, pipulse, telegram, notifications]
- Telegram does NOT automatically delete bot accounts due to owner inactivity or lack of interaction with @BotFather — ... [picast, pipulse, telegram, notifications]
- Wrapper script must trap SIGINT before running claude to ensure summary card displays even if user Ctrl+C during sess... [aoe, wrapper, signal-handling, ux]
- /save and auto-save hooks serve different purposes: /save forces immediate snapshot for explicit handoff (multi-sessi... [aoe, workflow, session-management]
- picast-update compares __version__ in src/picast/__about__.py against installed version and silently skips update if ... [picast, deployment, version-management]

## Current Progress
- Pushover sound tier system live across all 3 Pis (PiCast v0.16.1, PiCam, PiPulse): SoundTier enum with CASUAL/MEDIUM/... [pushover, notifications, sound-system, picast, picam, pipulse, deployment]
- PiCam Pushover migration (Session 2): pushover_util.py factory created, motion_scan.py refactored (ntfy removed from ... [picam, pushover, migration, progress]
- PiCast ntfy→Pushover migration complete: replaced NtfyConfig with PushoverConfig, created pushover_adapter.py with as... [picast, pushover, notifications, migration, release]
- Ultra Claude Stack (3-layer automation: Memory Extractor + TUI/MCP integration + brain.md sync) is COMPLETE and live.... [ultra-claude-stack, automation, system-architecture]
- PiCast v0.14.0 released and deployed to Pi: curated catalog with Archive.org public domain shows, Telegram notificati... [picast, release, deployment]

## Context
- User preference clarified: /done is the systematic session checkpoint (replaces /save); build full integration (JSONL... [workflow, preferences, priorities, session-management]
- User preference for /done workflow: maximize automation (auto-save handles metrics/memory capture) while using explic... [workflow, preferences, session-history]

_For deeper context, use memory_search, memory_related, or memory_ask tools._
<!-- MEMORY:END -->
