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

_Last updated: 2026-05-02 | 52 active memories, 704 total_

## Architecture
- PiCast database access pattern: `self.queue._db` provides database access from player via queue_manager reference, en... [picast, database, player, architecture]
- PiCast AI Autopilot uses tiered selection architecture: (1) TasteProfile rates candidate videos from block pool, (2) ... [picast, autopilot, architecture, multi-tv, fleet, remote-control, api-design]
- PiCast database resilience architecture: DatabaseManager._verify_integrity() runs PRAGMA integrity_check on every ope... [picast, database, resilience, crash-recovery, circuit-breaker, error-handling]
- PiCast receiver refactored from monolithic `picast_receiver.py` into modular Blueprint package structure: `player.py`... [picast, receiver, blueprint, refactoring, modularity, reusability]
- StarScreen integrates with PiCast via two bridge modules: (1) `sync_profiles.py` loads PiCast taste profiles to share... [starscreen, picast, integration, taste-profile, blueprint, architecture]
- Video casting to StarScreen uses three distinct paths — each with a different initiator and use case: (1) **StarGod M... [starscreen, casting, architecture, stargod, mcp-tools, pipulse, fleet, extension]

## Key Decisions
- Catalog uses Archive.org public domain shows (Space 1999, Twilight Zone) instead of copyrighted content (Stargate SG-... [picast, catalog, archive-org]
- Discovery Agent implemented as separate class in new `src/picast/server/sources/discovery.py` (not integrated into Yo... [picast, autoplay, discovery, design, separation-of-concerns]
- Kernel-level `panel_orientation=upside_down` in /boot/firmware/cmdline.txt chosen for display rotation over firmware ... [picast, display, rotation, kms, performance]
- PiCast v1.0.0 release marked 'Hand it to anyone release' in git tag message — represents production-ready feature com... [picast, v1.0.0, release, decision]
- AutoPlay and Autopilot are two separate features: AutoPlay assigns videos to time blocks (block = playlist), while Au... [picast, autopilot, architecture, design-philosophy]
- PiCast database backup uses sqlite3.backup API (db.backup(backup_db)) instead of shutil.copy2 to guarantee hot backup... [picast, database, backup, api-design]
- Circuit breaker for fallback screensaver uses fixed threshold of 10 consecutive failures before disabling fallback fo... [picast, fallback, resilience, circuit-breaker]
- StarScreen PiCast integration uses three key architectural decisions: (1) Integration strategy: leverage existing Vid... [starscreen, opi5plus, integration, architecture, deployment, twitch, pipulse, auth, videoManager]

## Patterns & Conventions
- Autoplay trigger validation pattern: extract video_id from QueueItem.url using extract_video_id() utility before savi... [picast, autoplay, queue, pattern]
- PiCast CLI command aliases via pyproject.toml [project.scripts]: `pycast export` (replaces `picast autoplay export`) ... [picast, cli, entry-points, pattern]
- URL validation pattern in PiCast: autoplay_pool_add and queue_add endpoints both normalize_url() then validate_url(ur... [picast, url-validation, api-pattern, error-handling]
- Effectiveness tracking in refresh log captures baseline pool snapshot (total_videos, liked_count, skip_count, complet... [picast, autopilot, metrics, logging, effectiveness]
- Block-to-mood mapping in refresh-taste-profile.sh uses static bash associative array (morning-foundation→chill, creat... [picast, autopilot, taste-profile, block-mapping]
- iOS Safari PWA double-tap confirm pattern: instead of confirm() dialogs (which silently return false in PWA mode), us... [picast, ios-safari, pwa, ui-pattern, mobile]
- PiCast Multi-TV notification integration uses MultiTVConfig dataclass with optional notify_fn: Optional[Callable[[str... [picast, multi-tv, config, notifications, architecture, pattern]
- PiPulse /api/pitim/blocks endpoint response includes optional schedule data structure: {block_name, display_name, emo... [pipulse, picast, api-design, error-handling, pattern]
- PiCast fallback test mocking pattern: subprocess-calling functions (detect_hdmi_audio, detect_wayland) must be patche... [picast, testing, mocking, fallback]
- PiCast receiver (picast-z1) deployment and sync patterns: (1) Source canonical copy at `receiver/picast_receiver.py` ... [picast-receiver, deployment, testing, verification, sync, git-workflow]
- PiCast fallback screensaver uses exponential backoff with threading.Event-based interruption: _fallback_consecutive_f... [picast, fallback, resilience, backoff, error-handling, threading, player-loop, wakeup-event, circuit-breaker]
- Twitch streamlink lifecycle management in video.py: added _kill_streamlink() method called by both stop() (public shu... [starscreen, twitch, streamlink, cleanup, process-management]
- OPi5+ mpv software decode configuration uses MPV_FORCE_SW=1 environment variable dropped into systemd unit drop-in (s... [starscreen, opi5plus, mpv, video-decode, systemd]
- PiPulse resolver multi-device casting pattern: `needs_resolution` property determines if device type (e.g., starscree... [pipulse, casting, receiver, authentication, architecture]
- PiCast main deployment workflow (rsync → install → restart → verify): (1) rsync excludes .venv, __pycache__, .git, di... [picast, deployment, workflow, verification, rsync]
- Receiver Blueprint flexibility patterns — three design choices that make the Blueprint reusable across PiCast and Sta... [receiver, blueprint, picast, starscreen, architecture, api-design, cross-platform]
- YouTube split-stream casting via mpv --audio-file argument — PiPulse resolver returns {video_url, audio_url} payload ... [pipulse, casting, youtube, mpv, split-stream, audio, format-selection]

## Gotchas & Pitfalls
- TOML table scoping: keys appended after a `[table.subtable]` header are parsed as belonging to that table, not the pa... [picast, toml, config, deployment]
- Bash return codes don't propagate through stderr capture when using pipe redirection (e.g., `cmd 2>&1 | cat` loses ex... [bash, error-handling, return-codes, debugging]
- sqlite3 CLI tool not installed on Pi 4B; WAL checkpoint for zero-data-loss DB migration requires using Python venv `s... [pipulse, database, migration, sqlite, gotcha]
- PiCast yt-dlp metadata fetch timeouts for some YouTube URLs (e.g., kJQP7kiw5Fk, RgKAFK5djSk) cause title resolution t... [picast, yt-dlp, metadata, api, timeout]
- Receiver code path conditional logic (Twitch vs YouTube) must track which flags apply to which provider: v0.8.0 accid... [picast-receiver, twitch, youtube, mpv-flags, bug-prevention]
- SQLite corruption detection in tests requires aggressive byte overwriting (512+ bytes) rather than small offset write... [picast, sqlite, testing, database, corruption]
- PiCast pytest mocking gotchas: (1) Mock patches must target the module where import occurs (@patch('picast.server.you... [picast, testing, mocking, pytest, threading]
- YouTube returns 403 Forbidden when StarScreen (and other cast devices without cookies) access video URLs directly; Pi... [starscreen, casting, youtube, authentication, pipulse, receiver]
- PiCast mobile/iOS UI gotchas: (1) iOS Safari PWA mode silently returns `false` from `confirm()` dialogs without displ... [picast, ios-safari, mobile-ui, pwa, responsive-design]
- OPi5+ StarScreen deployment blockers — three categories to resolve before playback works: (1) video.py hardware misma... [starscreen, opi5plus, deployment, video-playback, mpv, sudoers, installation, blocking]
- librockchip-mpp-dev (multimedia processing platform) is not available in Armbian's standard apt repos for Ubuntu nobl... [opi5plus, armbian, apt, rkmpp, hardware-decode, package-availability]

## Current Progress
- RKMPP hardware decode build initiated on OPi5+ StarScreen (2026-05-01) — kicked off build-mpv-rkmpp.sh via nohup in b... [starscreen, opi5plus, hardware-decode, rkmpp]
- PiPulse cast audio URL fix deployed and tested (2026-05-01) — modified cast/routes.py to populate audio_url field in ... [pipulse, casting, audio, deployment, testing, starscreen]
- StarScreen OPi5+ deployment COMPLETE (2026-05-01): Resolved installation blocker by running setup-cast-prereqs.sh — i... [starscreen, opi5plus, deployment, fleet, complete, mpv, sudoers]

## Context
- StarScreen Blueprint receiver deployment pending on OPi5+. StarScreen (10.0.0.170:5072, type='starscreen') is already... [starscreen, receiver, deployment, blueprint, opi5plus, pipulse, casting]
- PiPulse migration from Pi 4B (10.0.0.103) to PiHub (10.0.0.110) completed across 4 sessions: S1 hardware validation, ... [pipulse, picast, autopilot, multi-tv, fleet, migration, deployment, complete]

_For deeper context, use memory_search, memory_related, or memory_ask tools._
<!-- MEMORY:END -->
