# Resume: PiCast

Last updated: 2026-02-17

## State
v0.9.5 deployed to Pi. ROOT CAUSE FOUND: mpv IPC socket takes 2-4s to create on Pi, but code only waits 1s. All loadfile commands silently fail. Three other bugs fixed (v0.9.2-v0.9.4). Debug logging confirmed the issue. One straightforward fix remaining.

## Next Action
1. **FIX IPC connect**: In `player.py:_play_item()`, replace `time.sleep(1); self.mpv.connect()` with a retry loop:
   ```python
   # Wait for mpv IPC socket (Pi needs 2-4s)
   for _ in range(20):  # 10 seconds max
       if self.mpv.connect():
           break
       time.sleep(0.5)
   else:
       logger.error("Failed to connect to mpv IPC after 10s")
   ```
2. **TEST normal playback**: After fix, `curl -X POST http://picast.local:5050/api/play -H 'Content-Type: application/json' -d '{"url": "https://www.youtube.com/watch?v=jNQXAC9IVRw"}'`. Check journalctl for `loadfile (normal) response: {"error": "success"}` and `Playback started`.
3. **TEST timestamp seek**: `curl -X POST http://picast.local:5050/api/play -H 'Content-Type: application/json' -d '{"url": "https://www.youtube.com/watch?v=nS0jvdng6NU&t=1502s", "start_time": 1983}'`. Verify video starts at ~33:03.
4. **CLEANUP**: Remove debug logging (IPC connect, version check, response logs). Keep only error-level logging.
5. **UPDATE version**: Bump to v0.10.0 (all idle-mode IPC issues resolved).

## Key Files
- `~/Documents/Projects/Claude/terminal/picast/CLAUDE.md` - Project guidance
- `~/Documents/Projects/Claude/terminal/picast/DEVLOG.md` - Technical learnings (12 entries)
- `~/Documents/Projects/Claude/terminal/picast-extension/popup.js` - Extension (timestamp capture)
- `src/picast/server/player.py` - Player loop (THE FILE TO FIX — line ~400)
- `src/picast/server/mpv_client.py` - mpv IPC client
- `src/picast/server/app.py` - Flask routes
- `src/picast/__about__.py` - Version (0.9.5)

## Critical Knowledge (from debugging)
- **mpv IPC socket creation**: Takes 2-4s on Pi (not 1s). Must poll.
- **mpv v0.40 loadfile**: `["loadfile", url, flags, index, options]` — index (int) is required before options
- **Comma-separated options**: ytdl-raw-options and CDN URLs break loadfile option parser. Use CLI args.
- **Two-phase idle polling**: Wait for idle=False (start), then idle=True (end). 150s timeout for Phase 1.
- **IPC silent failures**: `command()` returns None on failure, no exception. Check return values.
- **socat testing**: `echo '{"command": ["loadfile", "URL", "replace"]}' | socat - /tmp/mpv-socket`

## Bugs Fixed This Session
| Version | Bug | Root Cause |
|---------|-----|-----------|
| v0.9.2 | loadfile parse error | mpv v0.40 expects index (int) as 4th arg |
| v0.9.3 | Premature idle exit | Single-phase poll exits during load wait |
| v0.9.4 | loadfile silently ignored | ytdl-raw-options commas break option parser |
| v0.9.5 | **All IPC commands fail** | **connect() fails — 1s sleep too short** |

## Decisions Made
- Python + Flask REST API, mpv subprocess per video
- SQLite for library/playlists/collections
- Web UI primary (PWA for phone)
- Multi-Pi via mDNS discovery
- Idle-mode mpv with IPC loadfile (v0.9.0+)
- Direct URL resolution for timestamp seeking
- ytdl options on CLI, not in loadfile IPC options

## Session History
- Feb 17 (session 2): IPC debugging. Found 4 bugs (v0.9.2-v0.9.5). Root cause: mpv socket not ready after 1s. Fix is straightforward.
- Feb 17 (session 1): Timestamp seek feature. 6 iterations (v0.8.1-v0.9.1). Extension captures currentTime, server resolves direct CDN URLs.
- Feb 14: v0.8.0 done (queue loop, video ID input, watch counter, flicker fix). Deploy fixed.
- Feb 12: Project tracker rewritten
- Feb 10: Sessions 16-19. PWA, mDNS, sleep timer, idle screen, collections. 282 tests, v0.4.0.
- Feb 09: Sessions 9-15. Wayland/HDMI, codec fixes, Pi deploy, UI overhaul, mobile polish.
- Feb 08: Sessions 1-8. Foundation through PyPI publish. 169 tests.
