# Resume: PiCast

Last updated: 2026-02-18

## State
v0.12.0 deployed to Pi. All core features working: YouTube playback with timestamp seek, Twitch, local files, and **Archive.org** (full-length public domain movies). YouTube DRM movies show graceful notice instead of silently failing. 412 tests pass.

## Next Action
No critical bugs. Potential enhancements:
- Add more source handlers (Tubi, direct URLs)
- Improve Archive.org browsing/search from web UI
- Add timestamp seek support for archive.org videos

## Resolved: YouTube Movie DRM

YouTube movies (paid AND free/ad-supported) use Widevine DRM. yt-dlp can only resolve the trailer (~141s). This is **unfixable** — no open-source tool can decrypt Widevine.

**v0.11.2 solution:** Detect the mismatch, show clear notice on TV (OSD) and web UI (error banner), play the trailer gracefully. No more silent failures.

**v0.12.0 alternative:** Archive.org has thousands of full-length public domain films with zero DRM. Added as a first-class source.

## Key Files
- `~/Documents/Projects/Claude/terminal/picast/CLAUDE.md` - Project guidance
- `~/Documents/Projects/Claude/terminal/picast/DEVLOG.md` - Technical learnings (16 entries)
- `~/Documents/Projects/Claude/terminal/picast-extension/popup.js` - Extension (YouTube, Twitch, Archive.org)
- `src/picast/server/player.py` - Player loop with thumbnail loading + seek + DRM detection
- `src/picast/server/sources/archive.py` - Archive.org source handler
- `src/picast/server/sources/youtube.py` - YouTube source handler
- `src/picast/server/app.py` - Flask routes
- `src/picast/__about__.py` - Version (0.12.0)

## Critical Knowledge (from debugging)
- **mpv IPC socket creation**: Takes 2-4s on Pi (not 1s). Must poll.
- **mpv v0.40 loadfile**: `["loadfile", url, flags, index, options]` — index (int) required before options
- **Comma-separated options**: ytdl-raw-options and CDN URLs break loadfile option parser. Use CLI args.
- **Two-phase idle polling**: Wait for idle=False (start), then idle=True (end). 150s timeout for Phase 1.
- **IPC silent failures**: `command()` returns None on failure, no exception. Check return values.
- **YouTube DRM**: ALL YouTube movies (paid + free) use Widevine. yt-dlp only gets trailer. Unfixable.
- **Archive.org IDs**: Case-sensitive. Use exact ID from URL bar.
- **picast-update caching**: Same version number = pip uses cached wheel.
- **Thumbnail URL pattern**: `https://i.ytimg.com/vi/{VIDEO_ID}/hqdefault.jpg` — no API call needed

## Source Handlers (v0.12.0)
| Handler | Source Type | Detection |
|---------|-----------|-----------|
| YouTubeSource | youtube | youtube.com, youtu.be |
| LocalSource | local | file://, /, media extensions |
| TwitchSource | twitch | twitch.tv |
| ArchiveSource | archive | archive.org |
| (fallback) | youtube | anything unmatched |

## Decisions Made
- Python + Flask REST API, mpv subprocess per video
- SQLite for library/playlists/collections
- Web UI primary (PWA for phone)
- Multi-Pi via mDNS discovery
- Idle-mode mpv with IPC loadfile (v0.9.0+)
- Direct URL resolution for timestamp seeking (YouTube only)
- ytdl options on CLI, not in loadfile IPC options
- Thumbnail via ytimg.com URL (no API), title from Chrome extension (no yt-dlp block)
- DRM movies: graceful notice, not silent failure
- Archive.org for free movies (no DRM alternative to YouTube)

## Session History
- Feb 18 (session 3): v0.11.2 + v0.12.0 — DRM detection with user notice, Archive.org source handler, extension v1.5.0. All tested on Pi.
- Feb 18 (session 2): v0.11.0 — Thumbnail loading, title from extension, duration validation. Movie seek still broken (yt-dlp trailer limitation).
- Feb 18 (session 1): v0.10.0 — IPC connect retry fix deployed, both playback modes tested, debug logging stripped.
- Feb 17 (session 2): IPC debugging. Found 4 bugs (v0.9.2-v0.9.5). Root cause: mpv socket not ready after 1s.
- Feb 17 (session 1): Timestamp seek feature. 6 iterations (v0.8.1-v0.9.1). Extension captures currentTime, server resolves direct CDN URLs.
- Feb 14: v0.8.0 done (queue loop, video ID input, watch counter, flicker fix). Deploy fixed.
- Feb 12: Project tracker rewritten
- Feb 10: Sessions 16-19. PWA, mDNS, sleep timer, idle screen, collections. 282 tests, v0.4.0.
- Feb 09: Sessions 9-15. Wayland/HDMI, codec fixes, Pi deploy, UI overhaul, mobile polish.
- Feb 08: Sessions 1-8. Foundation through PyPI publish. 169 tests.
