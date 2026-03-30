# ARCH-Z1-TWITCH-STREAMING: Solving Twitch Lag on Pi Zero 2 W

**Status:** RESEARCH COMPLETE, AWAITING IMPLEMENTATION
**Created:** 2026-03-30
**Supersedes:** ARCH-Z1-TWITCH-PLAYBACK.md (Phase 1+2 deployed but insufficient)
**Hardware:** Raspberry Pi Zero 2 W (BCM2710A1, 4x A53 @ 1GHz, VideoCore IV, 512MB/416MB usable)
**Current Version:** picast-receiver v0.7.0

---

## 1. Executive Summary

**Can the Pi Zero 2 W handle smooth Twitch live stream playback?**

**YES** — but only if we control the stream quality. The current approach fails because
Twitch streams labeled "720p60" are frequently **actually 1920x1080@60fps**, which is
2x the hardware decoder's rated capacity. The fix is using **streamlink** instead of
yt-dlp for Twitch to select appropriate quality tiers (480p/360p), which the hardware
handles trivially.

---

## 2. Root Cause Analysis (Definitive)

### 2.1 The Smoking Gun

Session Log #1 (2026-03-29) discovered via mpv IPC `track-list` query that a Twitch
stream labeled `720p60__source_` had an **actual decode resolution of 1920x1080**. This
is the root cause of all lag:

| Metric | 720p30 (works) | 720p60 (theoretical) | 1080p60 (actual Twitch) |
|--------|---------------|----------------------|------------------------|
| Pixels/sec | 27.6M | 55.3M | **124.4M** |
| vs HW limit | 44% | 89% | **200%** |
| CPU (v4l2m2m-copy) | ~28% | ~40% (est.) | **78-106%** |
| RAM | ~137MB | ~200MB (est.) | **2.2GB VM (OOM kills)** |
| Result | Perfect | Tight but possible | **Impossible** |

### 2.2 Hardware Decoder Limits

The VideoCore IV H.264 hardware decoder (bcm2835-codec via v4l2m2m) is rated for:
- **Maximum: 1080p30 H.264 decode** (~62.2M pixels/sec)
- This is a hard silicon limit — no software tuning can exceed it
- Source: Broadcom BCM2710A1 datasheet, confirmed by Raspberry Pi Foundation forums

### 2.3 Why YouTube Works but Twitch Doesn't

| Factor | YouTube | Twitch |
|--------|---------|--------|
| Format selection | yt-dlp selects exact 720p24/30 H.264 | Only `720p60__source_` available (non-partner) |
| Actual resolution | Matches label (720p = 1280x720) | Often lies (720p label = 1920x1080 actual) |
| Framerate | 24-30fps | 60fps |
| Pixels/sec | ~27.6M | ~124.4M |
| HLS ads | None (pre-roll is separate) | Inline in manifest (causes timestamp discontinuities) |
| Codec | avc1.64001F (Level 3.1) | avc1.4D0420 (Level 3.2 or higher) |

### 2.4 The v4l2m2m-copy Overhead

The monitor's `panel_orientation=upside_down` kernel flag breaks DRM overlay planes
(EINVAL on atomic commit). This forces `v4l2m2m-copy` instead of zero-copy
`v4l2m2m`, adding a GPU→CPU frame copy for every decoded frame. At 60fps, this is
significant overhead on a 4-core 1GHz CPU.

### 2.5 Memory Pressure

mpv + yt-dlp subprocess consume ~2.2GB virtual memory for Twitch streams, causing
3+ OOM kills per day on the 416MB system (+ 416MB zram swap). Contributing factors:
- yt-dlp spawned by mpv's ytdl-hook consumes ~100-150MB
- HLS manifest segments accumulate in demuxer buffers
- GPU shares the 512MB with system (416MB usable)

---

## 3. Solution Architecture

### 3.1 Overview: Streamlink + Adaptive Quality Selection

Replace yt-dlp with streamlink for Twitch URLs only. Streamlink:
1. Lists available quality tiers for any Twitch stream
2. Selects a quality within the hardware's decode capability
3. Handles Twitch ad injection natively
4. Pipes the stream via stdout (no yt-dlp subprocess memory overhead)
5. Is a pure Python package installable via pip

**YouTube continues using yt-dlp** — no change to the working VOD pipeline.

### 3.2 Quality Selection Strategy

```
Partner channel (has transcoding):
  Available: 160p, 360p, 480p, 720p30, 720p60, 1080p60
  Select: 480p (best quality within hardware capability)
  Fallback: 360p

Non-partner with Enhanced Broadcasting:
  Available: 160p, 360p, 480p, source
  Select: 480p
  Fallback: 360p

Non-partner without transcoding:
  Available: audio_only, source (720p60 or 1080p60)
  Select: source + aggressive frame dropping
  Fallback: audio_only (last resort)
```

**Quality preference order for Pi Zero 2 W:**
`480p > 360p > 720p30 > 720p > 160p > best > worst`

The `480p` target is ideal for:
- Small TV (< 24") — visually identical to 720p at viewing distance
- ~17.3M pixels/sec at 30fps — only 28% of hardware capacity
- Leaves ample CPU headroom for HLS demuxing, compositor, Flask server

### 3.3 Architecture Diagram

```
Current (BROKEN for Twitch):
  Chrome Extension → PiPulse → Receiver /api/play
    → mpv --ytdl-hook → yt-dlp (spawns subprocess, resolves HLS URL)
    → mpv fetches HLS → v4l2m2m-copy decode → compositor → display
  Problem: yt-dlp can't select quality, actual stream is 1080p60

Proposed (Streamlink pipe):
  Chrome Extension → PiPulse → Receiver /api/play
    → Receiver detects twitch.tv URL
    → streamlink --stdout URL 480p,360p,best
      (resolves stream, selects quality, handles ads, outputs raw stream)
    → mpv --hwdec=v4l2m2m-copy - (reads from stdin pipe)
    → v4l2m2m-copy decode → compositor → display
  Fix: 480p30 is 28% of hardware capacity = smooth playback
```

### 3.4 Pipe Architecture

```python
# Conceptual implementation
if "twitch.tv/" in url:
    # Streamlink pipe: resolve + quality select + ad handling
    streamlink_proc = subprocess.Popen(
        ["streamlink", "--stdout", "--twitch-disable-ads", url,
         "480p,360p,720p30,best"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    # mpv reads from streamlink's stdout
    mpv_proc = subprocess.Popen(
        ["mpv", "--hwdec=v4l2m2m-copy", "--no-terminal", ...opts, "-"],
        stdin=streamlink_proc.stdout,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    streamlink_proc.stdout.close()  # allow SIGPIPE propagation
else:
    # YouTube: existing yt-dlp path (works perfectly)
    mpv_proc = subprocess.Popen(
        ["mpv", "--hwdec=v4l2m2m-copy", ...opts, url],
        ...
    )
```

---

## 4. Detailed Implementation Plan

### Phase 1: Install Streamlink on picast-z1

```bash
# On Pi Zero 2 W (Bookworm)
pip install --break-system-packages streamlink
# Verify
streamlink --version
# Test quality listing
streamlink --json twitch.tv/CHANNEL_NAME
```

**Risk:** Pi Zero 2 W pip install may be slow (ARM compilation of lxml/pycryptodome).
**Mitigation:** Pre-build on Mac and rsync, or use `--only-binary :all:`.

**Estimated effort:** 15-30 minutes

### Phase 2: Receiver Code Changes

Modify `picast_receiver.py` to:

1. **Detect Twitch URLs** (already done: `_is_live = "twitch.tv/" in url`)
2. **Launch streamlink + mpv pipe** for Twitch URLs
3. **Keep yt-dlp path** for YouTube URLs
4. **Handle process lifecycle** for the two-process pipe (streamlink + mpv)
5. **Update watchdog** to monitor both processes

Key changes:
- `_play_url()`: Branch on Twitch vs YouTube
- New `_play_twitch()`: Streamlink pipe implementation
- `_stop_playback()`: Kill both streamlink and mpv processes
- `_watchdog_loop()`: Monitor pipe health (streamlink stdout → mpv stdin)
- `_is_idle()`: Check both processes

**Estimated effort:** 1-2 hours

### Phase 3: Quality Detection + Logging

1. Before playing, run `streamlink --json URL` to list available qualities
2. Log which quality was selected (helps debug future issues)
3. If no transcoding available and source is >720p, log warning
4. Expose quality info via `/api/status` response

**Estimated effort:** 30 minutes

### Phase 4: Source-Only Fallback (Non-Partner Streams)

For non-partner streams with only source quality (720p60 or 1080p60):

1. **If actual resolution <= 720p:** Play with aggressive frame dropping
   ```
   --framedrop=decoder+vo
   --vf=fps=30           # halve framerate to reduce decode pressure
   --video-sync=display-desync
   --profile=fast
   ```

2. **If actual resolution > 720p:** Still play, but with maximum degradation
   ```
   --framedrop=decoder+vo
   --vf=fps=24,scale=854:480  # scale down + reduce framerate
   --video-sync=display-desync
   --profile=fast
   ```
   Note: `scale` filter runs on CPU. At 480p output this is ~17.3M pixels/sec
   of CPU-side scaling but the decoder still processes all 1080p frames.
   This may or may not be viable — needs testing.

3. **Last resort:** Audio-only mode with OSD message "Stream quality too high for
   this device. Playing audio only."

**Estimated effort:** 1 hour (mostly testing)

### Phase 5: Memory Optimization

Streamlink pipe eliminates yt-dlp subprocess memory overhead (~100-150MB savings).
Additional mpv optimizations for pipe mode:

```
--demuxer-max-bytes=30M      # reduce from 100M (pipe is real-time)
--demuxer-max-back-bytes=10M # reduce from 30M
--cache=no                   # pipe is live, no cache needed
```

**Estimated RAM target:** <200MB total (streamlink ~40MB + mpv ~100MB + Flask ~20MB)

**Estimated effort:** 15 minutes

---

## 5. Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|-----------|
| Streamlink not installable on Pi (ARM deps) | Low | High | Pre-build wheels on Mac, or use pipx with --system-site-packages |
| Pipe deadlock (streamlink blocks, mpv starves) | Medium | Medium | Watchdog monitors both processes, kills both on stall |
| Streamlink Twitch plugin breaks (API changes) | Medium | Medium | Pin version, monitor streamlink releases, fallback to yt-dlp |
| Non-partner 1080p60 still lags | High | Low | Expected — log warning, offer audio-only fallback |
| Two-process lifecycle complexity | Medium | Medium | Clean process group management, SIGPIPE propagation |
| Memory pressure from streamlink deps (lxml, pycryptodome) | Low | Low | Monitor RSS after install, compare to yt-dlp baseline |

---

## 6. What Won't Work (Eliminated Approaches)

| Approach | Why It Fails |
|----------|-------------|
| Software decode (`--hwdec=no`) | 105% CPU for 720p60 — physically impossible |
| `--vf=fps=30` alone (without quality reduction) | Decoder still processes all 60fps; filter only drops display frames |
| DRM overlay fix (remove panel_orientation) | Video would display upside-down; `--video-rotate=180` may not work with drmprime-overlay |
| ffmpeg real-time transcode on Pi | Decode 1080p60 + re-encode = impossible on 4x A53 @ 1GHz |
| `--vd-lavc-skipframe=nonref` | Doesn't apply to v4l2m2m hardware decode (GPU decodes all frames) |
| yt-dlp format selection for Twitch | yt-dlp only sees `audio_only` + `720p60__source_` for non-partner streams |
| Pre-resolve URL + delayed start | Doesn't fix the resolution problem, just delays it |
| Lower `--demuxer-max-bytes` alone | Reduces memory but doesn't fix decode throughput |

---

## 7. Success Criteria

| Metric | Current (v0.7.0) | Target |
|--------|-------------------|--------|
| Partner stream playback | Black screen / lag | Smooth 480p, <30% CPU |
| Non-partner 720p stream | Lag, eventual OOM | Playable with frame drops |
| Non-partner 1080p stream | OOM crash in minutes | Graceful degradation or audio-only |
| A/V sync | Audio sketches after minutes | Stable sync for duration |
| Memory usage | 2.2GB VM, OOM kills | <200MB, zero OOM kills |
| Ad handling | Timestamp discontinuity crash | Streamlink handles natively |
| Recovery from issues | 30s watchdog restart | <15s watchdog + pipe restart |

---

## 8. Open Questions for Implementation

1. **Is streamlink in Debian Bookworm repos?** Check `apt list streamlink` on Pi.
   If not, pip install works but may need pre-built ARM wheels.

2. **Does `streamlink --twitch-disable-ads` actually skip ads?** Or does it just
   suppress the ad segments? Need to test with a live stream.

3. **Can we detect actual stream resolution before playing?** `streamlink --json`
   may include resolution metadata. If so, we can make smarter quality decisions.

4. **What happens to the pipe when the Twitch stream goes offline?** Does
   streamlink exit cleanly, or does it hang? Watchdog needs to handle this.

5. **Does `--initial-audio-sync=no` still needed with streamlink pipe?** The ad
   timestamp discontinuity that required this flag may not exist in the streamlink
   output.

6. **What is streamlink's RAM footprint on ARM?** Need to measure after install.

---

## 9. Implementation Session Plan

**Session 1 (30-45 min): Install + Validate**
- SSH to picast-z1
- Install streamlink
- Test `streamlink --json twitch.tv/CHANNEL` (list qualities)
- Test `streamlink --stdout twitch.tv/CHANNEL 480p | mpv -` manually
- Measure CPU and RAM

**Session 2 (1-2 hours): Code Changes**
- Implement streamlink pipe in `picast_receiver.py`
- Update watchdog for two-process pipe
- Update `/api/status` to show quality info
- Deploy and test via Chrome extension cast

**Session 3 (30 min): Testing + Polish**
- Test partner channel (quality selection)
- Test non-partner channel (source-only fallback)
- Test stream going offline (watchdog recovery)
- Test YouTube still works (no regression)
- Bump version to v0.8.0

---

## 10. References

- ARCH-Z1-WATCHDOG.md — Watchdog infrastructure (v0.7.0, deployed)
- ARCH-Z1-TWITCH-PLAYBACK.md — Previous approach (stall watchdog + live_start_index)
- pi-fleet.md memory — picast-z1 hardware specs and deployment pattern
- Streamlink docs: https://streamlink.github.io/latest/
- Streamlink Twitch plugin: https://streamlink.github.io/latest/plugins/twitch.html
- VideoCore IV H.264 decode limit: 1080p30 (Broadcom BCM2710A1 spec)
- H.264 Level 4.0: 245,760 macroblocks/sec = ~1080p30 or ~720p60 theoretical max
