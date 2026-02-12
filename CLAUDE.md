# PiCast - CLAUDE.md

Project-specific guidance for Claude Code when working on PiCast.

## Project Overview

PiCast is a YouTube queue player for Raspberry Pi. Mac runs the TUI client, Pi runs the server + mpv.

## Pi Device

| Key | Value |
|-----|-------|
| IP | `10.0.0.25` |
| SSH User | `jopi` |
| Hostname | `JoPi` |
| SSH | `ssh jopi@10.0.0.25` |
| Service | `sudo systemctl restart picast` |
| Logs | `journalctl -u picast -f` |
| Config | `~/.config/picast/picast.toml` |
| Data | `~/.picast/picast.db` |
| Port | `5050` (changed from 5000 in Session 11) |

## Architecture

- **Pi (server):** Flask REST API + mpv player + SQLite. Runs as systemd service.
- **Mac (client):** Textual TUI connects to Pi's API. Also used for development.
- **Web UI:** Served by Flask at `http://10.0.0.25:5050`

## Key Files

| File | Purpose |
|------|---------|
| `src/picast/server/app.py` | Flask routes and app wiring |
| `src/picast/server/database.py` | SQLite schema (v2) + migrations |
| `src/picast/server/queue_manager.py` | Queue persistence (SQLite) |
| `src/picast/server/player.py` | mpv playback loop |
| `src/picast/config.py` | Config loading from picast.toml |
| `src/picast/cli.py` | CLI entry points |
| `src/picast/tui/app.py` | Textual TUI |
| `install-pi.sh` | One-command Pi setup |

## Development Workflow

1. Edit code on Mac
2. Run tests: `cd picast && source .venv/bin/activate && pytest tests/ -v`
3. Test web UI locally: `./run.sh` (starts server with --no-player --quiet)
4. Deploy to Pi: TBD (need SSH access confirmed)

## Naming Convention (Session 11)

| Old Name | New Name |
|----------|----------|
| Player tab | Queue |
| Library | History |
| Playlists | Collections |

## Port

Default port changed from 5000 to 5050 in Session 11. Pi config needs updating after deploy.
