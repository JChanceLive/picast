# Z1 Twitch Streaming: What Worked, What Didn't

**Purpose:** Reference guide for future sessions fixing Twitch playback on picast-z1
**Hardware:** Raspberry Pi Zero 2 W (BCM2710A1, 4x A53 @ 1GHz, VideoCore IV, 416MB usable)
**Created:** 2026-03-30 (compiled from 3+ sessions on 2026-03-29)

---

## Quick Facts

| Fact | Value |
|------|-------|
| VideoCore IV H.264 decode limit | **1080p30** (~62.2M pixels/sec) |
| Twitch "720p60__source_" actual resolution | **1920x1080** (confirmed via mpv IPC track-list) |
| 1080p60 pixel throughput | **124.4M pixels/sec** (2x hardware limit) |
| YouTube 720p24 H.264 CPU usage | **28%** (works perfectly) |
| Twitch 1080p60 CPU usage | **78-106%** (impossible) |
| Available Twitch formats (non-partner) | `audio_only` + `720p60__source_` only |
| RAM available | 416MB physical + 416MB zram swap |
| mpv + yt-dlp Twitch VM usage | **2.2GB** (causes OOM kills) |
| OOM kills per day (Twitch) | **3+** |
| Monitor orientation | Physically upside-down, `panel_orientation=upside_down` in kernel cmdline |

---

## WHY YOUTUBE WORKS BUT TWITCH DOESN'T (THE KEY QUESTION)

**YouTube works perfectly. DO NOT TOUCH the YouTube path.** The difference is simple:

| Factor | YouTube | Twitch |
|--------|---------|--------|
| **Resolution control** | yt-dlp selects exact 720p (1280x720) | Only "source" available — often 1920x1080 |
| **Framerate** | 24-30fps (within hw limits) | 60fps (doubles decode work) |
| **Codec selection** | `vcodec^=avc` forces H.264 | Source is always H.264, but at 1080p60 |
| **Macroblocks/sec** | ~140K (720p24) or ~216K (720p30) | ~490K (1080p60) — **2x hardware limit** |
| **Format options** | Many (144p to 4K, any codec) | 2 options: `audio_only` + `source` |
| **Pre-roll ads** | Separate (don't affect playback) | Inline HLS (causes timestamp discontinuity) |
| **Memory** | yt-dlp resolves then exits | yt-dlp stays running via ytdl-hook |

**In short:** YouTube lets us pick the right resolution. Twitch doesn't. And the "720p60"
Twitch label is a LIE — the actual stream is 1080p60, which is 2x the decoder's capacity.

---

## H.264 MACROBLOCK MATH (from deep research)

The VideoCore IV supports H.264 Level 4.1 = **245,760 macroblocks/sec** max.

| Stream | Macroblocks/sec | vs HW Limit | Verdict |
|--------|----------------|-------------|---------|
| YouTube 720p24 | 140,400 | 57% | EASY |
| YouTube 720p30 | 216,000 | 88% | Works |
| **True** 720p60 | 216,000 | 88% | Should work (if stream IS actually 720p) |
| 1080p30 | 244,800 | 99.6% | At limit |
| **Twitch "720p60" (actual 1080p60)** | **489,600** | **199%** | **IMPOSSIBLE** |
| 480p30 (our target) | 47,700 | 19% | TRIVIAL |

**Key insight:** If a Twitch streamer actually broadcasts at true 720p60 (not 1080p), it
SHOULD work on this hardware (216K < 245K limit). The problem is only when the source is 1080p.

---

## WHAT WORKS

### YouTube Playback (SOLVED in v0.4.2)

| Setting | Value | Why |
|---------|-------|-----|
| `--hwdec=v4l2m2m-copy` | Hardware decode + CPU copy | Only working decode path (zero-copy breaks due to panel_orientation) |
| `--ytdl-format=bestvideo[height<=720][vcodec^=avc]+bestaudio/...` | Force 720p H.264 | YouTube defaults to AV1 which has no hw decode on VC4 |
| `--video-sync=display-desync` | Decouple A/V sync from display | Prevents frame drops under Wayland compositor |
| `--framedrop=decoder+vo` | Drop frames when behind | Graceful degradation |
| `--log-file=/tmp/mpv.log` | Log to file | Avoids fd leak from `open()` on stderr |

**YouTube root causes found and fixed:**
1. `--hwdec=auto` doesn't find v4l2m2m on Wayland -> explicit `v4l2m2m-copy`
2. YouTube serves AV1 by default -> `vcodec^=avc` forces H.264
3. `--hwdec=v4l2m2m` (zero-copy) -> EINVAL on DRM overlay atomic commit due to panel_orientation -> use `-copy` variant
4. stderr fd leak -> `subprocess.DEVNULL` + `--log-file`

### Watchdog Thread (WORKS, v0.7.0)

| Feature | Status |
|---------|--------|
| Stall detection (time-pos not advancing) | WORKS (3 checks = 30s threshold) |
| Process death detection | WORKS |
| Auto-reconnect with backoff | WORKS (5s, 15s, 30s, 60s, 120s) |
| Intentional stop immunity | WORKS (_intentional_stop flag) |
| Stability reset (5 min stable = reset retries) | WORKS |
| `GET/POST /api/watchdog` endpoints | WORKS |
| `z1 watchdog` / `z1 watchdog on/off` CLI | WORKS |

### General Infrastructure

| Feature | Status |
|---------|--------|
| Flask REST API (play, stop, pause, volume, status) | WORKS |
| mpv IPC socket commands | WORKS (with 2s startup delay) |
| Wayland environment passthrough | WORKS (_WAYLAND_ENV dict) |
| OSD title display | WORKS |

---

## WHAT DOESN'T WORK (Twitch-specific)

### Version History of Failed Approaches

| Version | Change | Result | CPU | Why It Failed |
|---------|--------|--------|-----|---------------|
| v0.5.0 | `--no-correct-pts` for Twitch | Black screen after ad | ~78% | Timestamp deadlock: audio 3965s vs video 90s |
| v0.5.1 | + `--vf=fps=30` | Still slow/laggy frames | ~80% | GPU still decodes all 60fps; filter only drops output |
| v0.5.2 | + `--vf=lavfi=[fps=30,scale=854:480]` | Even worse | ~90% | Software scale filter adds CPU overhead on top of copy |
| v0.5.3 | Software decode (`--hwdec=no --vd-lavc-threads=4`) | No audio, way too slow | **105%** | 4-core A53 can't software decode 1080p60 |
| v0.6.0 | v4l2m2m-copy + `--initial-audio-sync=no` + `--profile=fast` | Audio works! Video stays black | ~40% | Black screen at ad-to-stream transition specifically |
| v0.6.1 | + Stall detection watchdog | Watchdog never ran | ~40% | Python bug: `_intentional_stop` not in `global` declaration |
| v0.7.0 | Fixed watchdog bug + `live_start_index=-1` | Watchdog works, video still lags | ~40-80% | Fundamental: 1080p60 > hardware decode capacity |

### Things That Will NEVER Work on This Hardware

| Approach | Why |
|----------|-----|
| Software decode of 720p60+ | 4x A53 @ 1GHz can't do it (105% CPU = max 1 core) |
| 1080p60 hardware decode | VideoCore IV rated for 1080p30 max (2x over capacity) |
| `--vf=fps=30` to reduce framerate | GPU hardware decoder processes ALL frames; filter only drops display frames (decode is the bottleneck, not display) |
| `--vf=scale=854:480` | Software scaling adds CPU overhead; doesn't reduce decode work |
| `--vd-lavc-skipframe=nonref` | Doesn't apply to v4l2m2m hardware decode (GPU decodes everything) |
| ffmpeg real-time transcode | Can't decode + re-encode on this CPU |
| DRM overlay zero-copy (`--hwdec=v4l2m2m`) | `panel_orientation=upside_down` causes EINVAL on DRM atomic commit |
| Remove panel_orientation + mpv `--video-rotate=180` | Overlay plane video would still be upside-down (transform doesn't affect overlay) |
| yt-dlp format selection for lower quality | Non-partner Twitch streams only have `audio_only` + source |

### Partially Working (May Be Useful)

| Approach | What Happens | Potential |
|----------|-------------|-----------|
| `--initial-audio-sync=no` | Fixes audio deadlock from ad timestamp jump | KEEP — needed for any Twitch path |
| `--profile=fast` | Strips expensive rendering options | KEEP — reduces CPU overhead |
| `live_start_index=-1` | Starts at live edge, may skip part of ad | KEEP — complementary to other fixes |
| `--demuxer-max-bytes=100M` | Caps demuxer memory | KEEP — prevents unbounded RAM growth |
| Stall watchdog | Auto-restarts on frozen playback | KEEP — safety net for any approach |

---

## KEY DIAGNOSTIC FINDINGS

### 1. Twitch "720p60" Is Actually 1080p (THE ROOT CAUSE)

**Session 1 Discovery:** mpv IPC `track-list` query showed actual decode resolution of **1920x1080** for a stream that yt-dlp labeled `720p60__source_`. This means:

- The streamer is broadcasting at 1080p60
- Twitch labels it as "source quality" → shows as "720p60" in some API contexts
- The hardware decoder is trying to process 124.4M pixels/sec (2x its 62.2M limit)
- **This is a physics problem, not a software bug**

### 2. Twitch Ad Timestamp Discontinuity

Pre-roll ads are inline in the HLS manifest. When the ad ends:
- Ad audio timestamps: ~0-90s
- Live stream audio jumps to: ~3965s
- Gap: ~3875 seconds
- mpv's default A/V sync tries to wait for video to catch up to audio timestamp (never will)
- Fix: `--initial-audio-sync=no` prevents this deadlock

### 3. OOM Kill Pattern

```
oom-kill: task=mpv, pid=2089 — total-vm:2221088kB (2.2GB)
oom-kill: task=mpv, pid=1522 — total-vm:2303304kB (2.3GB)
oom-kill: task=mpv, pid=1286 — total-vm:2368944kB (2.4GB)
```

3 OOM kills in one day. mpv + yt-dlp subprocess consume 2.2-2.4GB VM on a 416MB system.
Contributing factors: yt-dlp memory, HLS manifest accumulation, GPU shared memory.

### 4. MMC Controller Errors

```
mmc1: Controller never released inhibit bit(s).
```
SD card controller stress from heavy swap thrashing. Secondary symptom, not root cause.

### 5. Position Freeze at 90.234s

mpv reports "starting video playback" and "starting audio playback" but:
- `time-pos` stuck at 90.234 seconds (never advances)
- HLS segments continue to be fetched (network active)
- OSD text renders fine (GPU compositor works)
- Video output is black (decoder stalled or producing blank frames)
- This 90s matches the ad duration before the stream transition

---

## DIAGNOSTIC COMMANDS (for future sessions)

```bash
# System state
ssh picast-z1 "sudo reboot"
ssh picast-z1 "free -m"
ssh picast-z1 "top -bn1 | head -20"
ssh picast-z1 "dmesg | grep -i oom"
ssh picast-z1 "dmesg | grep -i mmc"
ssh picast-z1 "journalctl -u picast-receiver -f"

# mpv state via IPC
ssh picast-z1 "python3 -c \"
import socket,json
s=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
s.settimeout(2)
s.connect('/tmp/picast-receiver-socket')
for prop in ['time-pos','hwdec-current','video-params','track-list']:
    s.sendall(json.dumps({'command':['get_property',prop]}).encode()+b'\n')
    print(f'{prop}: {s.recv(4096).decode().strip()}')
s.close()
\""

# Test playback manually
ssh picast-z1 "curl -X POST http://localhost:5050/api/play \
  -H 'Content-Type: application/json' \
  -d '{\"url\": \"https://www.twitch.tv/CHANNEL\", \"title\": \"test\"}'"

# Check yt-dlp formats
ssh picast-z1 "yt-dlp -F https://www.twitch.tv/CHANNEL"

# Check streamlink qualities (after install)
ssh picast-z1 "streamlink --json https://www.twitch.tv/CHANNEL"

# Watchdog state
z1 watchdog
z1 status

# mpv log
ssh picast-z1 "tail -50 /tmp/mpv.log"
```

---

## RECOMMENDED SOLUTION (Not Yet Implemented)

**Use streamlink instead of yt-dlp for Twitch streams.**

Streamlink can:
1. Select lower quality tiers (480p, 360p) for partner channels
2. Handle Twitch ad injection natively
3. Pipe stream via stdout (no yt-dlp memory overhead)
4. List available qualities before playing

480p30 on a small TV = **17.3M pixels/sec** = **28% of hardware capacity** = smooth playback.

Full architecture: `ARCH-Z1-TWITCH-STREAMING.md`

---

## FILE LOCATIONS

| File | Purpose |
|------|---------|
| `receiver/picast_receiver.py` | Source code on Mac (deploy to Pi) |
| `/home/jopi/picast-receiver/picast_receiver.py` | Deployed code on Pi |
| `docs/ARCH-Z1-WATCHDOG.md` | Watchdog architecture + Session Logs #1-#2 |
| `docs/ARCH-Z1-TWITCH-PLAYBACK.md` | Previous approach (Phase 1+2 deployed) |
| `docs/ARCH-Z1-TWITCH-STREAMING.md` | New comprehensive solution architecture |
| `docs/Z1-TWITCH-SESSION-HISTORY.md` | THIS FILE |
| `~/.local/bin/z1` | Mac CLI for picast-z1 |
| `~/.claude/savepoints/SESSION-SAVEPOINT-2026-03-29-z1-watchdog.md` | Session 1 savepoint |
| `~/.claude/savepoints/PICAST-Z1-TWITCH-FIX-2026-03-29.md` | Session 2 savepoint |
