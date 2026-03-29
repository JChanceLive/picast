# ARCH-Z1-WATCHDOG: Auto-Reconnect for picast-z1

**Status:** IN PROGRESS — watchdog code deployed, playback broken (black screen)
**Created:** 2026-03-29
**Hardware:** Raspberry Pi Zero 2 W (416MB RAM, zram swap)

## SESSION LOG (2026-03-29) — READ THIS FIRST

### What Was Done
- Watchdog thread IMPLEMENTED and DEPLOYED (v0.4.0) — code is solid, endpoints work
- `z1 watchdog` / `z1 watchdog on` / `z1 watchdog off` Mac CLI added
- Demuxer caps added: `--demuxer-max-bytes=100M --demuxer-max-back-bytes=30M`
- `--framedrop=decoder+vo` added for graceful degradation
- stderr now logged to `/tmp/mpv-stderr.log` (was /dev/null — hid all errors)

### BLOCKER: Black Screen on All Streams
After deploying, ALL playback shows black screen with OSD title text but no video/audio.
mpv process runs (78-106% CPU) but video never renders. Tested with Twitch stream `thirdsbetter`.

### Format Strings Tried (ALL failed with black screen)
| Format | Result |
|--------|--------|
| `best[height<=480]/best` | Black screen — Twitch has no 480p, fell through to `best` = 1080p source |
| `best[height<=480]/best[height<=720]/best` | Black screen — same, all fall through to 1080p |
| `best[height<=720]/best` | Black screen — yt-dlp says 720p but actual decode is 1920x1080 |
| `best[height<=720][fps<=30]/best[height<=720]/best` + `--vf=fps=30` | Black screen — fps filter runs after decode, doesn't help CPU bottleneck |
| `bestvideo[height<=720]+bestaudio/best[height<=720]/best` (ORIGINAL) | **ALSO black screen** — reverted to exact original format, still broken |

### Critical Diagnostic Findings
1. **yt-dlp -F** shows only 2 formats for this Twitch stream: `audio_only` and `720p60__source_` (1280x720)
2. **mpv track-list via IPC** reveals actual decode resolution is **1920x1080** — Twitch source quality lies about resolution
3. **mpv CPU**: 78-106% trying to decode 1080p60 H.264 — Pi Zero 2 W VideoCore IV maxes at 1080p30 hw decode
4. **mpv stderr** (`/tmp/mpv-stderr.log`): Only `Cannot load libcuda.so.1` + PipeWire config warnings — NO video decode errors
5. **mpv position via IPC**: Stuck at 90.234 seconds — never advances (frozen, not buffering)
6. **`--cache=yes --cache-secs=30`**: Caused HLS live stream deadlock — REMOVED

### What MIGHT Be Wrong (investigate next session)
1. **hwdec not engaging**: `--hwdec=auto` might not find v4l2m2m on Wayland/labwc. Try `--hwdec=v4l2m2m` explicitly
2. **Wayland compositor issue**: Multiple restarts during this session may have broken labwc display state. Try `ssh picast-z1 "sudo systemctl restart labwc"` or full reboot
3. **This specific Twitch stream**: thirdsbetter source is genuinely 1080p60 despite 720p label — try a YouTube video or different Twitch stream to isolate
4. **stderr=open() file handle leak**: Changed from DEVNULL to `open("/tmp/mpv-stderr.log", "w")` — file handle stays open, may cause issues. Consider using subprocess.DEVNULL for stderr and a separate mpv `--log-file` flag instead
5. **Multiple rapid restarts corrupted mpv IPC socket**: Restarted service ~6 times in 30 minutes — `/tmp/picast-receiver-socket` may be stale

### What's Currently Deployed (v0.4.0)
```
--ytdl-format=bestvideo[height<=720]+bestaudio/best[height<=720]/best  (original)
--demuxer-max-bytes=100M
--demuxer-max-back-bytes=30M
--framedrop=decoder+vo
stderr -> /tmp/mpv-stderr.log
watchdog thread running (10s interval, 5 retries, linear backoff)
```

### Next Steps for Fresh Session
1. **Reboot the Pi** — `ssh picast-z1 "sudo reboot"` — clean slate for display/compositor
2. **Test with a known-good YouTube video** (not Twitch) to isolate stream vs system issue
3. **Check hwdec**: `ssh picast-z1 "python3 -c \"...\"` query mpv `hwdec-current` property via IPC
4. **If hwdec is `no`**: Try `--hwdec=v4l2m2m` or `--hwdec=drm` explicitly
5. **Fix stderr**: Use `--log-file=/tmp/mpv.log` instead of `stderr=open(...)` to avoid fd leak
6. **If YouTube works but Twitch doesn't**: The Twitch source stream is genuinely too heavy for this hardware — need to investigate `--vd-lavc-threads=2` or accept limitation

---

## 1. Root Cause Analysis

Kernel logs from today reveal the **primary failure mode** — the OOM killer:

```
oom-kill: task=mpv, pid=2089 — Out of memory: Killed process 2089 (mpv) total-vm:2221088kB
oom-kill: task=mpv, pid=1522 — Out of memory: Killed process 1522 (mpv) total-vm:2303304kB
oom-kill: task=mpv, pid=1286 — Out of memory: Killed process 1286 (mpv) total-vm:2368944kB
```

Three OOM kills in a single day. mpv is consuming ~2.2GB virtual memory on a Pi with 416MB physical + 416MB zram swap. The kernel has no choice but to kill it.

**Secondary issue:** MMC (SD card) controller errors appearing alongside OOM events:
```
mmc1: Controller never released inhibit bit(s).
```
These are likely caused by heavy swap thrashing stressing the SD card controller.

### Why mpv Uses So Much Memory

- **yt-dlp subprocess** spawned by mpv to resolve stream URLs consumes significant memory
- **Twitch/YouTube live streams** use HLS/DASH manifests that grow over time
- **Pi Zero 2 W has no dedicated video memory** — GPU shares the 416MB with system
- **Current format string** `bestvideo[height<=720]+bestaudio` forces separate streams that mpv must demux in RAM

---

## 2. Two-Pronged Solution

### Prong A: Reduce Memory Pressure (Prevent OOM)

Reduce mpv/yt-dlp memory consumption so OOM kills happen less frequently.

| Change | Impact | Effort |
|--------|--------|--------|
| Use muxed format `best[height<=480]` instead of separate video+audio | Eliminates demuxing buffer (~100MB saving) | Trivial |
| Add `--demuxer-max-bytes=50M --demuxer-max-back-bytes=20M` | Caps demuxer memory at 70MB instead of unbounded | Trivial |
| Add `--cache=yes --cache-secs=30` | Limits stream cache to 30s instead of default | Trivial |
| Add `--oom-score-adjust=-100` to mpv (or set in systemd) | Tells kernel to prefer killing other processes | Trivial |
| Lower `--ytdl-format` to 480p for live, 720p for VODs | Significant bandwidth + memory reduction | Minor |

**Estimated total RAM savings:** 100-200MB, which may eliminate OOM entirely on 416MB.

### Prong B: Watchdog Thread (Auto-Reconnect When It Does Die)

Even with memory reduction, network drops and stream endings will still kill mpv. A watchdog ensures it restarts automatically.

---

## 3. Watchdog Architecture

### Design: In-Process Watchdog Thread

A background thread inside `picast_receiver.py` that monitors mpv process health and restarts it when it dies unexpectedly.

**Why a thread, not a cron job:**
- The receiver already holds `_player_proc` and `_current_video` state
- A cron job would need to duplicate state tracking (what was playing, at what volume)
- Thread has sub-second detection; cron minimum is 1 minute
- No extra systemd units or crontab entries to manage

### State Machine

```
                    ┌─────────────┐
         play()───▶│   PLAYING    │
                    │  mpv alive   │
                    └──────┬──────┘
                           │ mpv dies (poll() != None)
                           ▼
                    ┌─────────────┐
                    │  DETECTED   │
                    │  drop logged│
                    └──────┬──────┘
                           │ wait RETRY_DELAY
                           ▼
                    ┌─────────────┐
                    │  RETRYING   │──── fail ────┐
                    │  _play_url()│               │
                    └──────┬──────┘               ▼
                           │ success      ┌──────────────┐
                           ▼              │  BACKOFF      │
                    ┌─────────────┐       │  wait longer  │
                    │   PLAYING   │       └──────┬────────┘
                    └─────────────┘              │ retry
                                                 └──▶ RETRYING
```

### Key Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Check interval | **10 seconds** | Fast enough to notice drops quickly, light enough for Pi Zero |
| Retry delay | **5 seconds** after detection | Give network a moment to recover |
| Max retries | **5 consecutive** then stop | Avoid infinite restart loops for dead streams |
| Backoff | **Linear: 5s, 15s, 30s, 60s, 120s** | Escalating delay prevents hammering |
| Cooldown reset | After **5 minutes** of stable playback | Resets retry counter when stream is healthy |
| State tracking | `_last_known_url`, `_last_known_title`, `_last_known_volume` | Remembers what was playing + volume level |
| Manual stop immunity | Watchdog ignores drops after `/api/stop` call | Don't reconnect when user intentionally stopped |
| Logging | Every drop + retry logged with timestamp | Diagnostic visibility via `journalctl` |

### New API Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `GET /api/watchdog` | GET | Returns watchdog state: enabled, retry count, last drop time, last URL |
| `POST /api/watchdog` | POST | Enable/disable watchdog: `{"enabled": true/false}` |

### Mac CLI Addition

```bash
# New z1 commands
z1 watchdog          # Show watchdog status
z1 watchdog on       # Enable auto-reconnect
z1 watchdog off      # Disable auto-reconnect
```

---

## 4. Implementation Plan

### Phase 1: Memory Reduction (no code changes to receiver)

1. Update `_play_url()` mpv arguments:
   - Change format to `best[height<=480]/best` for all content
   - Add `--demuxer-max-bytes=50M --demuxer-max-back-bytes=20M`
   - Add `--cache=yes --cache-secs=30`
2. Deploy and monitor for 24h — if OOM stops, Phase 2 is still valuable but less urgent

### Phase 2: Watchdog Thread

1. Add watchdog state variables to module globals
2. Add `_watchdog_loop()` function (the thread body)
3. Start watchdog thread in `main()`
4. Modify `_stop_playback()` to set `_intentional_stop = True`
5. Modify `_play_url()` to update `_last_known_*` state and reset `_intentional_stop`
6. Add `/api/watchdog` GET/POST endpoints
7. Update `z1` Mac CLI with watchdog subcommand
8. Bump version to v0.4.0
9. Deploy to Pi, verify with `journalctl -f`

### Phase 3: Observability (Optional)

1. Track drop count, uptime between drops in memory
2. Expose via `/api/watchdog` stats
3. Consider PiPulse integration for fleet-level drop alerts

---

## 5. Code Sketch

### Watchdog Thread (receiver side)

```python
_watchdog_enabled = True
_intentional_stop = False
_last_known_url = ""
_last_known_title = ""
_last_known_volume = 100
_retry_count = 0
_last_stable_since = 0.0
_MAX_RETRIES = 5
_BACKOFF = [5, 15, 30, 60, 120]
_CHECK_INTERVAL = 10
_STABLE_RESET = 300  # 5 min of stable playback resets retries

def _watchdog_loop():
    global _retry_count, _last_stable_since
    while True:
        time.sleep(_CHECK_INTERVAL)

        if not _watchdog_enabled:
            continue
        if not _last_known_url:
            continue  # nothing was playing
        if _intentional_stop:
            continue  # user stopped it

        # Check if mpv is still alive
        if not _is_idle():
            # Playing fine — track stability
            if _last_stable_since == 0:
                _last_stable_since = time.time()
            elif time.time() - _last_stable_since > _STABLE_RESET:
                _retry_count = 0  # reset after sustained playback
            continue

        # mpv died unexpectedly
        if _retry_count >= _MAX_RETRIES:
            logger.warning("Watchdog: max retries (%d) reached, giving up on %s",
                          _MAX_RETRIES, _last_known_url)
            continue

        delay = _BACKOFF[min(_retry_count, len(_BACKOFF) - 1)]
        logger.warning("Watchdog: playback dropped! Retry %d/%d in %ds — %s",
                       _retry_count + 1, _MAX_RETRIES, delay, _last_known_title)
        time.sleep(delay)

        success = _play_url(_last_known_url, _last_known_title)
        if success and _last_known_volume != 100:
            time.sleep(2)  # wait for mpv IPC socket
            _mpv_command(["set_property", "volume", _last_known_volume])

        _retry_count += 1
        _last_stable_since = 0.0
```

### Modified _play_url (state tracking additions)

```python
def _play_url(url, title="", mute=False):
    global _last_known_url, _last_known_title, _intentional_stop
    # ... existing code ...
    _last_known_url = url
    _last_known_title = title
    _intentional_stop = False
    # ...
```

### Modified _stop_playback (intentional stop flag)

```python
def _stop_playback():
    global _intentional_stop
    _intentional_stop = True
    # ... existing code ...
```

---

## 6. Risk Assessment

| Risk | Mitigation |
|------|-----------|
| Watchdog restarts a stream user wanted dead | `_intentional_stop` flag set by `/api/stop` |
| Infinite restart loop hammers YouTube/Twitch | Max 5 retries with escalating backoff (5s to 120s) |
| Watchdog thread crashes | `try/except` wrapper in loop, log errors, continue |
| Memory reduction breaks playback quality | 480p on a small TV is fine; can tune per-content-type later |
| yt-dlp itself OOMs during URL resolution | Can't easily fix — but memory reduction makes it less likely |
| Race condition: watchdog restarts while user sends new play | `_player_lock` already exists, watchdog uses `_play_url()` which acquires it |

---

## 7. Success Criteria

- Zero unrecovered drops over a 24-hour period
- `z1 watchdog` shows uptime and retry history
- OOM kills reduced from ~3/day to ~0/day (Phase 1)
- If OOM still occurs, watchdog recovers within 15 seconds (Phase 2)
