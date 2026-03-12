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

_Last updated: 2026-03-12 | 41 active memories, 466 total_

## Architecture
- PiCast database access pattern: `self.queue._db` provides database access from player via queue_manager reference, en... [picast, database, player, architecture]
- PiCast mobile UI redesign Phase 1 (2026-03-12) implements new CSS design system: root variables updated to `--accent:... [picast, mobile-ui, css-design-system, s1-complete]
- PiCast AI Autopilot uses tiered selection architecture: (1) TasteProfile rates candidate videos from block pool, (2) ... [picast, autopilot, architecture, taste-profile, discovery, api-design, selection-algorithm, fleet, feedback-loop, multi-tv]

## Key Decisions
- Catalog uses Archive.org public domain shows (Space 1999, Twilight Zone) instead of copyrighted content (Stargate SG-... [picast, catalog, archive-org]
- Discovery Agent implemented as separate class in new `src/picast/server/sources/discovery.py` (not integrated into Yo... [picast, autoplay, discovery, design, separation-of-concerns]
- Pushover chosen as ntfy replacement: provides proper APNS infrastructure for reliable iOS background push, one-time $... [pushover, ntfy, notifications, ios-push, decision, trade-offs]
- Kernel-level `panel_orientation=upside_down` in /boot/firmware/cmdline.txt chosen for display rotation over firmware ... [picast, display, rotation, kms, performance]
- PiCast v1.0.0 release marked 'Hand it to anyone release' in git tag message — represents production-ready feature com... [picast, v1.0.0, release, decision]
- AutoPlay and Autopilot are two separate features: AutoPlay assigns videos to time blocks (block = playlist), while Au... [picast, autopilot, architecture, design-philosophy]
- 30-day trial guard added to refresh-taste-profile.sh: creates ~/.picast/trial-start on first run, stores expiry date ... [picast, autopilot, cost-control, trial-system]
- Multi-TV queue distribution prioritizes VISUAL simplicity over playback state sync: each TV plays its assigned queue ... [picast, multi-tv, design-philosophy, scope]
- PiCast mobile UI redesign prioritizes visible-by-default menu items over scrollable carousels: quick control buttons ... [picast, mobile-ui, ux-design, responsive-design, visual-hierarchy, decision]

## Patterns & Conventions
- Autoplay trigger validation pattern: extract video_id from QueueItem.url using extract_video_id() utility before savi... [picast, autoplay, queue, pattern]
- PiCast CLI command aliases via pyproject.toml [project.scripts]: `pycast export` (replaces `picast autoplay export`) ... [picast, cli, entry-points, pattern]
- PiPulse /api/pitim/blocks endpoint response includes optional schedule data structure: {block_name, display_name, emo... [pipulse, picast, api-design, error-handling]
- URL validation pattern in PiCast: autoplay_pool_add and queue_add endpoints both normalize_url() then validate_url(ur... [picast, url-validation, api-pattern, error-handling]
- Effectiveness tracking in refresh log captures baseline pool snapshot (total_videos, liked_count, skip_count, complet... [picast, autopilot, metrics, logging, effectiveness]
- Block-to-mood mapping in refresh-taste-profile.sh uses static bash associative array (morning-foundation→chill, creat... [picast, autopilot, taste-profile, block-mapping]
- Queue refresh pattern in PiCast: /api/queue/loop-reset endpoint calls queue.loop_reset() to reset all played/skipped ... [picast, queue, api, ui, multi-tv]
- PiCast JS state persistence pattern for two-tier controls: localStorage keys for floating position state (now-playing... [picast, javascript, state-persistence, ui-pattern, mobile]
- Multi-TV and fleet coordination patterns in PiCast: (1) Fleet hybrid trigger in app.py autoplay/trigger endpoint call... [picast, multi-tv, fleet, validation, pattern, concurrency]
- iOS Safari PWA double-tap confirm pattern: instead of confirm() dialogs (which silently return false in PWA mode), us... [picast, ios-safari, pwa, ui-pattern, mobile]
- PiCast CSS design system color migration pattern: all hardcoded `rgba(0,240,255,X)` neon-cyan replaced with `var(--ac... [picast, css-design-system, color-migration, pattern, oled, refactor]

## Gotchas & Pitfalls
- iOS Safari PWA mode silently returns `false` from `confirm()` dialogs without displaying them; PiCast settings page r... [picast, web-ui, ios-safari, mobile, debugging]
- Mock patches in pytest must target the module where import occurs: @patch('picast.server.youtube_discovery.shutil.whi... [testing, mocking, pytest]
- TOML table scoping: keys appended after a `[table.subtable]` header are parsed as belonging to that table, not the pa... [picast, toml, config, deployment]
- Autopilot engine test flakiness from weighted shuffle: test_video_skip_removes_from_queue assumes skipped video will ... [picast, testing, autopilot, queue, randomness, flaky-test]
- Bash return codes don't propagate through stderr capture when using pipe redirection (e.g., `cmd 2>&1 | cat` loses ex... [bash, error-handling, return-codes, debugging]
- Test helper _save_profile() in test_autopilot_engine.py has default generated_at="2026-03-10T06:00:00". When testing ... [picast, testing, gotcha, taste-profile]
- validate-profile.py returns (errors, warnings) tuple with both lists populated independently: errors are hard failure... [picast, autopilot, validation, testing, taste-profile]
- Multi-TV feature requires non-empty pending queue to distribute videos; enabling Multi with empty queue only distribu... [picast, multi-tv, queue, ux]
- iPhone 5 (320px viewport width) presents extreme mobile constraint: 44px minimum touch target + 8px margins per contr... [picast, mobile-ui, responsive-design, ios]
- PiCast autopilot engine test failures in TestScoring class (test_genre_match_boosts_score and others) are pre-existin... [picast, testing, autopilot-engine, test-isolation]
- Multi-TV distribute() method calls _get_idle_devices() which acquires lock and holds it during iteration; _next_assig... [picast, multi-tv, threading, concurrency]

## Current Progress
- PiCast Mobile UI Overhaul COMPLETE (2026-03-12): Deployment verified across all pages — nav bar (44px touch targets, ... [picast, mobile-ui, deployment, s1-s3-complete, visual-verification]
- PiCast AI Autopilot Phases 1-5 COMPLETE (2026-03-09 to 2026-03-12): Phase 1 (S1.3) - 5 API endpoints for engine lifec... [picast, ai-autopilot, phase-1-complete, phase-3-complete, phase-4-complete, phase-5-complete, deployment, progress, multi-tv, fleet]

## Context
- PiCast AI Autopilot Phases 1-5 COMPLETE (2026-03-09 to 2026-03-12): Phase 1 (S1.3) - 5 API endpoints for engine lifec... [picast, ai-autopilot, phase-1-complete, phase-3-complete, phase-4-complete, phase-5-complete, deployment, progress, multi-tv, fleet, validation]
- Taste profile learning feedback sources: (1) explicit thumbs up/down via queue UI (rating ±1), (2) skip button (skip_... [picast, autopilot, taste-profile, learning-loop, feedback]
- User's actual PiCast viewing preferences (for taste profile seeding): PRIMARY is Boston and Maine Live webcam (always... [picast, autopilot, taste-profile, user-preferences]
- User preference for /done workflow: maximize automation (auto-save handles metrics/memory capture) while using explic... [workflow, preferences, session-management, priorities]

_For deeper context, use memory_search, memory_related, or memory_ask tools._
<!-- MEMORY:END -->
