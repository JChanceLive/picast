# ARCH-Z1-WATCHDOG: Auto-Reconnect for picast-z1

**Status:** DEPLOYED v0.7.0 — YouTube working, Twitch watchdog auto-recovery deployed (testing)
**Created:** 2026-03-29
**Hardware:** Raspberry Pi Zero 2 W (416MB RAM, zram swap)

## SESSION LOG #2 (2026-03-29, second session) — READ THIS FIRST

### YouTube: SOLVED (v0.4.2)
YouTube playback fully working. Three root causes found and fixed:

1. **`--hwdec=auto` doesn't find v4l2m2m on Wayland** → Fixed: `--hwdec=v4l2m2m-copy`
2. **YouTube serves AV1 by default** — Pi Zero 2 W has NO AV1 hw decode → Fixed: `vcodec^=avc` in format string forces H.264
3. **`--hwdec=v4l2m2m` (zero-copy) causes black screen** — DRM overlay plane atomic commits fail (`Error 22 EINVAL`) due to `panel_orientation=upside_down` in kernel cmdline → Fixed: use `v4l2m2m-copy` instead (copies frames to CPU, renders through compositor which handles rotation)
4. **stderr fd leak** — `open("/tmp/mpv-stderr.log", "w")` leaked fd per play → Fixed: `subprocess.DEVNULL` + `--log-file=/tmp/mpv.log`

**YouTube performance (v0.4.2, Big Buck Bunny 720p24 H.264):** 28% CPU, 137MB RAM. User confirmed video + audio working.

### Twitch: UNSOLVED — Black screen after ad

**Core problem:** Twitch injects pre-roll ads into HLS manifest. When ad ends and live stream starts, there's a timestamp discontinuity AND possibly a codec reset that breaks playback.

**What was tried (all failed for Twitch):**

| Version | Approach | Result |
|---------|----------|--------|
| v0.5.0 | `--no-correct-pts` for Twitch | Black screen after ad; timestamp deadlock (audio 3965s vs video 90s) |
| v0.5.1 | + `--vf=fps=30` | Still slow/laggy frames |
| v0.5.2 | + `--vf=lavfi=[fps=30,scale=854:480]` | Even worse — software scale adds CPU overhead on top of copy |
| v0.5.3 | Software decode (`--hwdec=no --vd-lavc-threads=4`) | 105% CPU, no audio, way too slow |
| v0.6.0 | Back to v4l2m2m-copy + `--initial-audio-sync=no` + `--profile=fast` | Black screen after ad (but audio starts!) |
| v0.6.1 | + Stall detection watchdog | **Bug:** `UnboundLocalError` on `_intentional_stop` — watchdog never ran |

**Key diagnostic findings (Twitch):**
- Twitch HLS has only 2 formats: `audio_only` + `720p60__source_` (avc1.4D0420) — no lower quality option for non-partners
- Ad segments have timestamps ~0-90s, live stream audio jumps to ~3965s — 3875s gap
- `--initial-audio-sync=no` fixes the audio deadlock (audio starts immediately)
- v4l2m2m-copy at 720p60 uses ~40% CPU — technically manageable
- The black screen occurs specifically at the ad-to-stream TRANSITION, not from CPU overload
- mpv reports "starting video playback" and "starting audio playback" but screen stays black
- HLS segments continue to be fetched successfully after the transition

### What's Currently Deployed (v0.7.0)
```python
# YouTube (non-Twitch):
--hwdec=v4l2m2m-copy  # WORKING

# Twitch:
--hwdec=v4l2m2m-copy
--profile=fast
--initial-audio-sync=no
--demuxer-lavf-o=live_start_index=-1  # start at live edge, may skip ad
# Watchdog stall detection: 3 consecutive stalls (30s) → auto-restart mpv
```

### Fixes Applied in v0.7.0 (Session #3)
1. **Watchdog `global` bug fixed** — `_intentional_stop` added to `global` declaration in `_watchdog_loop()`. Was creating a local variable, causing `UnboundLocalError` every 10s.
2. **`live_start_index=-1`** — Tells ffmpeg/lavf to start from the latest HLS segment rather than the beginning. May skip part of the pre-roll ad, reducing black screen duration.
3. **Stall detection** — Watchdog queries `time-pos` via IPC every 10s. If position doesn't advance for 3 consecutive checks (30s), kills and restarts mpv.

### Remaining Questions
1. **Does `live_start_index=-1` fully skip the ad?** Or does it start mid-ad and still hit the transition?
2. **Would streamlink instead of yt-dlp fix Twitch ads?** Streamlink has Twitch-specific ad handling
3. **Can we detect "ad playing" state and only start mpv AFTER ad ends?**

---

## SESSION LOG #1 (2026-03-29, first session)

### What Was Done
- Watchdog thread IMPLEMENTED and DEPLOYED (v0.4.0) — code is solid, endpoints work
- `z1 watchdog` / `z1 watchdog on` / `z1 watchdog off` Mac CLI added
- Demuxer caps added: `--demuxer-max-bytes=100M --demuxer-max-back-bytes=30M`
- `--framedrop=decoder+vo` added for graceful degradation
- stderr now logged to `/tmp/mpv-stderr.log` (was /dev/null — hid all errors)

### Previous Diagnostic Findings
1. **yt-dlp -F** shows only 2 formats for Twitch streams: `audio_only` and `720p60__source_` (1280x720)
2. **mpv track-list via IPC** reveals actual decode resolution is **1920x1080** — Twitch source quality lies about resolution
3. **mpv CPU**: 78-106% trying to decode 1080p60 H.264 — Pi Zero 2 W VideoCore IV maxes at 1080p30 hw decode
4. **mpv position via IPC**: Stuck at 90.234 seconds — never advances (frozen, not buffering)
5. **`--cache=yes --cache-secs=30`**: Caused HLS live stream deadlock — REMOVED

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
