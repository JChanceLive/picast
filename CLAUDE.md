# PiCast - CLAUDE.md

Project-specific guidance for Claude Code when working on PiCast.

## Project Overview

PiCast is a YouTube queue player for Raspberry Pi. Mac runs the TUI client, Pi runs the server + mpv.

## BANNED Test Videos

**NEVER use these URLs for testing playback:**
- `https://www.youtube.com/watch?v=dQw4w9WgXcQ` (Rick Astley - Never Gonna Give You Up)

Use a short Creative Commons or public domain video instead.

## Pi Device

| Key | Value |
|-----|-------|
| IP | DHCP (use `picast.local` to avoid stale IPs) |
| SSH | `ssh picast` (uses ~/.ssh/config Host entry with key auth) |
| Service | `sudo systemctl restart picast` |
| Logs | `journalctl -u picast -f` |
| Config | `~/.config/picast/picast.toml` |
| Data | `~/.picast/picast.db` |
| Port | `5050` |

## Architecture

- **Pi (server):** Flask REST API + mpv player + SQLite. Runs as systemd service.
- **Mac (client):** Textual TUI connects to Pi's API. Also used for development.
- **Web UI:** Served by Flask at `http://picast.local:5050`
- **Chrome Extension:** Separate repo at `~/Documents/Projects/Claude/terminal/picast-extension/`. Has its own git remote.

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

## Development & Deploy

1. Edit code on Mac
2. Run tests: `cd picast && source .venv/bin/activate && pytest tests/ -v`
3. Test web UI locally: `./run.sh` (starts server with --no-player --quiet)
4. Deploy to Pi: `ssh picast "export PATH=\$HOME/.local/bin:\$PATH && picast-update"`

**Always bump `src/picast/__about__.py`** when making changes. The Pi auto-updater compares this against GitHub main.

### Naming Convention

| Old Name | New Name |
|----------|----------|
| Player tab | Queue |
| Library | History |
| Playlists | Collections |

## Chrome Extension (picast-extension)

Separate repo: `~/Documents/Projects/Claude/terminal/picast-extension/`
Remote: `git@github.com:JChanceLive/picast-extension.git`

After editing popup.js: user must reload extension in `chrome://extensions`. Extension calls `POST /api/play` or `POST /api/queue/add`, shows error from response body.

## Pi Hardware & Display

| Component | Detail |
|-----------|--------|
| Kernel | 6.12.62 |
| Compositor | labwc (Wayland) |
| GPU driver | vc4-kms-v3d, 256MB VRAM |
| Monitor | Sceptre E20, 1600x900@60Hz, HDMI-A-1, physically mounted upside-down |
| mpv | v0.40.0, uses `--video-sync=display-desync` (zero frame drops) |

### Display Rotation

Monitor is physically upside-down. Kernel-level rotation via `/boot/firmware/cmdline.txt`:
```
video=HDMI-A-1:panel_orientation=upside_down
```

**Does NOT work:** `display_hdmi_rotate=2` (incompatible with vc4-kms-v3d), `wlr-randr --transform 180` (frame drops), `--vo=drm` (labwc holds DRM master). Rotation changes require reboot.

## mpv Configuration

| Setting | Value | Why |
|---------|-------|-----|
| `--video-sync` | `display-desync` | Prevents frame drops (default `audio` marks frames "late" under Wayland) |
| `--osd-level` | `3` | Persistent title overlay |
| `--osd-status-msg` | `${media-title}` | Shows video title bottom-left |

**Frame drop history:** `display-desync` decouples video from audio sync = 0 drops. Pi GPU handles 720p24 fine.

## Autoplay System (v0.24.4)

Block-based autoplay triggered by PiPulse on TIM block transitions. Weighted-random selection with self-learning (thumbs, skips, completions). See `docs/autoplay-system.md` for full endpoints, weight formula, cross-block learning, state tracking, and testing commands.

## Known Pi Issues

### Transient SD Card I/O Errors

Pi's SD card occasionally has transient `disk I/O error` on SQLite operations. Database layer retries with backoff (0.5s, 2s). If recurring: `ssh picast "sudo dmesg | grep -i mmc"` — may need SD card replacement.

### Error Handling

- Global `@app.errorhandler(Exception)` converts unhandled exceptions to JSON `{"error": "..."}` responses
- Extension parses error response body to show actual error text

<!-- MEMORY:START -->
# picast

_Last updated: 2026-03-20 | 47 active memories, 611 total_

## Architecture
- PiCast database access pattern: `self.queue._db` provides database access from player via queue_manager reference, en... [picast, database, player, architecture]
- PiCast AI Autopilot uses tiered selection architecture: (1) TasteProfile rates candidate videos from block pool, (2) ... [picast, autopilot, architecture, taste-profile, discovery, api-design, selection-algorithm, fleet, feedback-loop, multi-tv, mute, starscreen]
- PiCast Multi-TV Remote Control architecture (ARCH-MULTI-TV-REMOTE.md) defines 4-tier device control: (1) Local device... [picast, multi-tv, remote-control, architecture, s1-backend, fleet, api-design, web-ui, s2-implementation]

## Key Decisions
- Catalog uses Archive.org public domain shows (Space 1999, Twilight Zone) instead of copyrighted content (Stargate SG-... [picast, catalog, archive-org]
- Discovery Agent implemented as separate class in new `src/picast/server/sources/discovery.py` (not integrated into Yo... [picast, autoplay, discovery, design, separation-of-concerns]
- Kernel-level `panel_orientation=upside_down` in /boot/firmware/cmdline.txt chosen for display rotation over firmware ... [picast, display, rotation, kms, performance]
- PiCast v1.0.0 release marked 'Hand it to anyone release' in git tag message — represents production-ready feature com... [picast, v1.0.0, release, decision]
- AutoPlay and Autopilot are two separate features: AutoPlay assigns videos to time blocks (block = playlist), while Au... [picast, autopilot, architecture, design-philosophy]
- 30-day trial guard added to refresh-taste-profile.sh: creates ~/.picast/trial-start on first run, stores expiry date ... [picast, autopilot, cost-control, trial-system]
- Autoplay pool defaults to disabled on boot regardless of TOML config `[autoplay] enabled = true`. User toggles it on ... [picast, autoplay, boot-default, decision]
- Per-device skip tracking via QueueItem.skip_user_device dict ({device_id: timestamp}) chosen over per-device queue co... [picast, multi-tv, database-design, queue-architecture]
- PiCast Multi-TV architecture and fleet integration decisions: (1) Queue distribution prioritizes visual simplicity ov... [picast, multi-tv, fleet, architecture, starscreen, pushover, notifications, ipv6, grayout-recovery, pipulse, pihub, migration]

## Patterns & Conventions
- Autoplay trigger validation pattern: extract video_id from QueueItem.url using extract_video_id() utility before savi... [picast, autoplay, queue, pattern]
- PiCast CLI command aliases via pyproject.toml [project.scripts]: `pycast export` (replaces `picast autoplay export`) ... [picast, cli, entry-points, pattern]
- URL validation pattern in PiCast: autoplay_pool_add and queue_add endpoints both normalize_url() then validate_url(ur... [picast, url-validation, api-pattern, error-handling]
- Effectiveness tracking in refresh log captures baseline pool snapshot (total_videos, liked_count, skip_count, complet... [picast, autopilot, metrics, logging, effectiveness]
- Block-to-mood mapping in refresh-taste-profile.sh uses static bash associative array (morning-foundation→chill, creat... [picast, autopilot, taste-profile, block-mapping]
- iOS Safari PWA double-tap confirm pattern: instead of confirm() dialogs (which silently return false in PWA mode), us... [picast, ios-safari, pwa, ui-pattern, mobile]
- PiCast receiver (picast-z1) deployment pattern: Source is at /home/jopi/picast-receiver/picast_receiver.py on z1. Edi... [picast, receiver, deployment, picast-z1, pattern]
- Multi-TV on_queue_changed() runs distribute() in a background thread (name="multi-tv-queue-changed") so HTTP endpoint... [picast, multi-tv, async, threading, testing]
- Blind sed used for TOML config updates containing tokens: `ssh jopi@pihub 'sed -i "s/OLD_IP/NEW_IP/g" ~/.config/pipul... [pipulse, configuration, deployment, security]
- Project notes session save pattern: Update PROJECT-PICAST.md status/next-action/last-touch + history row, update brai... [session-save, workflow, documentation]
- PiCast improvement plan multi-session orchestration uses context-aware stage checkpoints: Session 1 reads codebase → ... [picast, improvement-system, multi-session-planning, orchestration]
- PiCast Multi-TV notification integration uses MultiTVConfig dataclass with optional notify_fn: Optional[Callable[[str... [picast, multi-tv, config, notifications, architecture, pattern]
- PiPulse /api/pitim/blocks endpoint response includes optional schedule data structure: {block_name, display_name, emo... [pipulse, picast, api-design, error-handling, pattern]
- PiCast Multi-TV failure recovery pattern: _device_failures dict stores (count, timestamp) tuples where count incremen... [picast, multi-tv, failure-tracking, grayout-recovery, pattern]
- PiCast Pi deployment pattern when GitHub SSH keys unavailable: rsync -av --exclude '.venv' --exclude '__pycache__' --... [picast, deployment, rsync, pip, systemd, pattern]
- PiCast queue refresh and loop operations: /api/queue/loop-reset endpoint calls queue.loop_reset() to reset all played... [picast, queue, api, ui, multi-tv, pattern]

## Gotchas & Pitfalls
- iOS Safari PWA mode silently returns `false` from `confirm()` dialogs without displaying them; PiCast settings page r... [picast, web-ui, ios-safari, mobile, debugging]
- Autopilot engine test flakiness from weighted shuffle: test_video_skip_removes_from_queue assumes skipped video will ... [picast, testing, autopilot, queue, randomness, flaky-test]
- Mock patches in pytest must target the module where import occurs: @patch('picast.server.youtube_discovery.shutil.whi... [testing, mocking, pytest]
- TOML table scoping: keys appended after a `[table.subtable]` header are parsed as belonging to that table, not the pa... [picast, toml, config, deployment]
- Bash return codes don't propagate through stderr capture when using pipe redirection (e.g., `cmd 2>&1 | cat` loses ex... [bash, error-handling, return-codes, debugging]
- iPhone 5 (320px viewport width) presents extreme mobile constraint: 44px minimum touch target + 8px margins per contr... [picast, mobile-ui, responsive-design, ios]
- StarScreen Flask port is 5072 (confirmed in app.py line 672), NOT 5001. The savepoint SESSION-SAVEPOINT-2026-03-12-pi... [starscreen, port, gotcha, fleet]
- sqlite3 CLI tool not installed on Pi 4B; WAL checkpoint for zero-data-loss DB migration requires using Python venv `s... [pipulse, database, migration, sqlite, gotcha]
- Watcher on_video_finished(dev_id) doesn't validate item_id before calling queue.on_video_finished() — safe today beca... [picast, multi-tv, watcher, race-condition, callback-safety]
- MagicMock comparisons in pytest fail with 'not supported between instances' error (e.g., `mock_grace_period > 0` rais... [picast, testing, mocking, pytest]
- PiCast deployment gotchas on Pi: (1) __about__.py version changes require pip reinstall with --break-system-packages ... [picast, deployment, python-import-caching, pip, gotcha, ipv6, networking, mdns, safari, latent-bug]
- validate-profile.py returns (errors, warnings) tuple with both lists populated independently: errors are hard failure... [picast, autopilot, validation, testing, taste-profile, gotcha]

## Current Progress
- PiCast Multi-TV feature S4-S9 COMPLETE (2026-03-12 to 2026-03-13): S4 - MultiTVManager with distribute_queue(), web U... [picast, multi-tv, s4-complete, s5-complete, s6-complete, s7-complete, s9-complete, remote-control, s1-complete, s2-complete, fleet, starscreen, deployment, chrome-extension, progress, improvements, notifications, grayout-recovery, v1.1.0a40]
- PiCast AI Autopilot Phases 1-5 COMPLETE (2026-03-09 to 2026-03-12): Phase 1 (S1.3) - 5 API endpoints for engine lifec... [picast, ai-autopilot, phase-1-complete, phase-3-complete, phase-4-complete, phase-5-complete, deployment, progress]
- PiCast Mobile UI Overhaul S1-S3 COMPLETE (2026-03-12): S1 (v1.1.0a18) - CSS design system (--accent: #00D9FF, --succe... [picast, mobile-ui, s1-complete, s2-complete, s3-complete, deployment, css-design-system, responsive-design, progress]

## Context
- PiPulse migration from Pi 4B (10.0.0.103) to PiHub (10.0.0.110) completed across 4 sessions: S1 hardware validation, ... [pipulse, pihub, migration, deployment, fleet-infrastructure, picast, ai-autopilot, phase-1-complete, phase-3-complete, phase-4-complete, phase-5-complete, multi-tv, fleet, starscreen]
- Taste profile learning feedback sources: (1) explicit thumbs up/down via queue UI (rating ±1), (2) skip button (skip_... [picast, autopilot, taste-profile, learning-loop, feedback]
- User's actual PiCast viewing preferences (for taste profile seeding): PRIMARY is Boston and Maine Live webcam (always... [picast, autopilot, taste-profile, user-preferences]

_For deeper context, use memory_search, memory_related, or memory_ask tools._
<!-- MEMORY:END -->
