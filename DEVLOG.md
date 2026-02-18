# PiCast Development Log

Technical learnings and debugging notes for future sessions.

---

## 2026-02-18 (Session 3): DRM Detection + Archive.org (v0.11.2 -> v0.12.0)

### v0.11.2: Graceful YouTube Movie DRM Detection

**Problem:** v0.11.1 added a "deferred seek" for YouTube movies — detect trailer/movie mismatch, load normally, then seek via IPC after playback starts. This can never work because yt-dlp only resolves the trailer (Widevine DRM prevents access to the full film). The deferred seek silently failed every time.

**Fix:** Replaced deferred seek with explicit DRM detection + user notification:
- `is_protected_movie` flag replaces `deferred_seek` variable
- When mismatch detected: OSD notice on TV (8s) + SSE `"protected"` event to web UI
- Web UI reuses existing `showErrorBanner()` — no new components
- Trailer still plays (graceful degradation)

**Key learning:** YouTube movies (both paid AND free/ad-supported) use Widevine DRM. No open-source tool can decrypt Widevine. This is not an auth/cookie problem — it's encryption. yt-dlp maintainers confirm this is unfixable. The only path for movies is licensed players (Chrome, YouTube app, smart TV apps).

### v0.12.0: Archive.org Source Handler

**Motivation:** YouTube movies are DRM-locked, but Archive.org has thousands of full-length public domain films with zero DRM. yt-dlp already supports archive.org natively.

**Changes:**
- New `sources/archive.py` — `ArchiveSource` handler with `matches()`, `validate()`, `get_metadata()` via yt-dlp
- Registered in `app.py` alongside YouTube/Twitch/local
- Chrome extension v1.5.0: `isSupportedUrl()` now matches `archive.org`, title cleaning strips Archive.org suffixes
- Source detection: archive.org URLs properly detected as `source_type="archive"` instead of falling through to YouTube handler

**Archive.org tips:**
- Browse `https://archive.org/details/feature_films` for full movies
- URL format: `https://archive.org/details/{ITEM_ID}` — IDs are case-sensitive
- yt-dlp `--print duration` works to verify full-length before playing
- Multi-file items: yt-dlp may return multiple lines, handler takes first

**Tested full-length movies (confirmed working):**
- His Girl Friday (1940) — 1h 32m: `archive.org/details/HisGirlFriday1940_201505`
- Frankenstein (1931) — 1h 10m: `archive.org/details/frankenstein-1931-restored-movie-720p-hd`
- Woman on the Run (1950) — 1h 18m: `archive.org/details/woman-on-the-run-1950_202406`

### Key Technical Learnings (New)

13. **YouTube DRM is absolute**: Both paid movies AND free/ad-supported movies use Widevine DRM. yt-dlp resolves only the trailer (~141s) for all of them. No cookie/auth workaround exists.

14. **Archive.org URL sensitivity**: Item IDs are case-sensitive. `Night_of_the_Living_Dead` != `night_of_the_living_dead`. Always use the exact ID from the URL bar.

15. **Source handler registration order**: `SourceRegistry.detect()` iterates handlers in registration order. First match wins. archive.org URLs don't match YouTube/local/Twitch handlers, so ArchiveSource must be registered to prevent them falling through to the `"youtube"` default.

16. **Player source_type branching**: The CDN seek path in `_play_item()` only fires for `source_type == "youtube"`. Archive.org items go through the normal `loadfile` path, which is correct — mpv's yt-dlp hook handles archive.org natively without needing direct URL resolution.

---

## 2026-02-17 (Session 2): IPC Debugging (v0.9.1 -> v0.9.5)

### Root Cause Found

**The 1-second sleep before IPC connect is too short on Pi.** mpv takes 2+ seconds to create its IPC socket. All subsequent IPC commands (show_text, loadfile) silently fail because the socket connection was never established.

Debug logging (v0.9.5) proved it:
```
mpv IPC connect: False       <-- connect() failed after 1s wait
mpv version via IPC: None    <-- no connection
show_text response: False    <-- silently dropped
loadfile (normal) response: None  <-- silently dropped
Connected to mpv at ...      <-- connects 1s LATER (auto-reconnect from get_property poll)
```

### Bugs Found and Fixed (v0.9.2-v0.9.4)

| Version | Fix | Details |
|---------|-----|---------|
| v0.9.2 | loadfile index parameter | mpv v0.40 signature: `loadfile url flags index options`. Our code omitted `index`, putting options string in index position. Fix: add `0` as 4th arg. |
| v0.9.3 | Two-phase playback polling | Old code's single poll loop exited immediately because mpv starts idle (`idle-active=True`) and 5-second guard expired during setup. Fix: Phase 1 waits for `idle-active=False` (150s timeout), Phase 2 waits for `idle-active=True` or `eof-reached`. |
| v0.9.4 | ytdl-raw-options comma bug | `ytdl-raw-options=js-runtimes=deno,remote-components=ejs:github` in loadfile options breaks mpv's comma-separated parser. Fix: moved `--ytdl-format` and `--ytdl-raw-options` to mpv CLI args. Normal loadfile is now just `loadfile url replace`. |

### What Still Needs Fixing (v0.9.5)

1. **IPC connect retry** — Replace `sleep(1); connect()` with a retry loop (try every 0.5s for up to 10s). This is the ROOT CAUSE of all loadfile failures.
2. **Validate loadfile after connect** — After successful connect, send loadfile and check response is `{"error": "success"}`. If not, log and retry.
3. **Test normal playback** — Once IPC connect is reliable, verify normal YouTube playback works.
4. **Test timestamp seek** — After normal playback works, test with `start_time` parameter.
5. **Remove debug logging** — Strip verbose IPC logging after all tests pass, keep only error-level logs.

### Key Technical Learnings (New)

8. **mpv v0.40 loadfile IPC signature**: `["loadfile", url, flags, index, options]`. The `index` parameter (integer, 4th arg) was added in v0.38+. For `replace` mode, use `0`. Without it, mpv interprets the options string as index and fails.

9. **mpv loadfile comma parsing**: ALL loadfile options are comma-separated. Any option value containing commas (CDN URLs, ytdl-raw-options) breaks the parser. Solution: pass comma-containing options on the CLI instead.

10. **mpv IPC socket creation timing**: On Pi, mpv takes 2-4 seconds to create the IPC socket after process start. A 1-second sleep is NOT enough. Must poll for socket availability.

11. **mpv IPC silent failures**: When `connect()` fails, `command()` tries auto-reconnect but returns `None` on failure. No exception raised. MUST check return values.

12. **Two-phase idle polling**: mpv with `--idle=yes` reports `idle-active=True` both before AND after playback. Must wait for `idle-active=False` first (playback started) before monitoring for it to become True again (playback ended).

### mpv IPC Test Commands (socat)

```bash
# Check if idle
echo '{"command": ["get_property", "idle-active"]}' | socat - /tmp/mpv-socket

# Load a video
echo '{"command": ["loadfile", "https://www.youtube.com/watch?v=VIDEO_ID", "replace"]}' | socat - /tmp/mpv-socket

# Load with start position (direct CDN URL)
echo '{"command": ["loadfile", "CDN_URL", "replace", 0, "start=1983"]}' | socat - /tmp/mpv-socket

# Check playback position
echo '{"command": ["get_property", "time-pos"]}' | socat - /tmp/mpv-socket
```

---

## 2026-02-17 (Session 1): Timestamp Seek Feature (v0.8.1 -> v0.9.1)

### Goal
Play Now from Chrome extension should start video at the current timestamp the user is watching.

### Architecture Changes
- **Extension (picast-extension/)**: Uses `chrome.scripting.executeScript()` to grab `video.currentTime` from YouTube page. Fallback: parses `t=` URL param. Sends `start_time` in POST body.
- **Server (app.py)**: `/api/play` accepts optional `start_time` float, passes to `player.play_now()`.
- **Player (player.py)**: Major rewrite — mpv now starts in `--idle=yes` mode, files loaded via IPC `loadfile` command.

### What We Tried and Why It Failed

| Approach | Result | Why |
|----------|--------|-----|
| `--start=1502` flag on mpv CLI | Instant EOF (0.1s after first frame) | yt-dlp pipes an unseekable stream; mpv can't seek past buffered content |
| IPC `seek(1504, "absolute")` after playback starts | `ok=False` (30s poll timeout) | Video takes 60-90s to load on Pi; 30s polling wasn't enough |
| IPC seek with 150-iteration poll | `ok=True` but mpv exits 4s later | DASH stream via yt-dlp pipe doesn't support arbitrary seeking |
| `loadfile url replace start=1983,audio-file=<cdn_url>` | Parse error | YouTube CDN URLs contain commas that break mpv's option parser |
| **WORKING: `yt-dlp -g` to resolve direct URLs + `loadfile` with `start=` + separate `audio-add`** | v0.9.1 (testing) | Direct CDN URLs support HTTP range requests = seekable |

### Key Technical Learnings

1. **mpv + yt-dlp pipe is NOT seekable**: When mpv uses its ytdl_hook, yt-dlp pipes the stream. This pipe doesn't support seeking. `--start=` and IPC `seek` both fail because there's nothing to seek in.

2. **Direct CDN URLs ARE seekable**: `yt-dlp -g -f <format> <url>` returns direct googlevideo.com URLs that support HTTP range requests. mpv can seek freely in these.

3. **mpv loadfile option parsing**: Options are comma-separated `key=value`. YouTube CDN URLs contain commas (even URL-encoded `%2C` gets decoded). Solution: use separate `audio-add` command for audio track.

4. **mpv idle mode + OSC**: `--idle=yes` shows "Drop files or URLs to play here" from the OSC overlay. Fix: `--osc=no` to suppress, then use `show_text` for custom loading message.

5. **Pi load times**: YouTube videos take 30-90 seconds to start playing on Pi (yt-dlp resolution + stream buffering). Any polling/timeout must account for this.

6. **Chrome extension `scripting` permission**: Works with `activeTab` on Manifest V3. `chrome.scripting.executeScript()` can inject into YouTube pages to get `video.currentTime`.

7. **Deployment pipeline**: Local edits -> git push -> `picast-update` on Pi (pulls from GitHub, pip install, systemctl restart). MUST push to GitHub before running picast-update.

### Extension Changes (picast-extension/)
- `manifest.json`: Added `scripting` permission, bumped to v1.4.0
- `popup.js`: Play Now captures `video.currentTime` via scripting injection, fallback parses URL `t=` param, sends `start_time` in POST body

### Server Changes (picast/src/picast/server/)
- `app.py`: `/api/play` accepts `start_time`, logs it, passes to player
- `player.py`:
  - New `_resolve_direct_urls()` method: calls `yt-dlp -g` to get CDN URLs
  - `_play_item()` rewritten: starts mpv in `--idle=yes --force-window=immediate --osc=no` mode
  - Files loaded via IPC `loadfile` command (not CLI args)
  - For timestamped plays: resolves direct URLs, loads with `start=<seconds>`, adds audio via `audio-add`
  - Playback end detected by polling `idle-active` / `eof-reached`
  - `play_now()` accepts `start_time` parameter
