# ARCH-Z1-TWITCH-PLAYBACK: Fixing Twitch Live Streams on Pi Zero 2 W

**Status:** DEPLOYED v0.7.0 (Phase 1+2 complete, testing)
**Created:** 2026-03-29
**Depends on:** ARCH-Z1-WATCHDOG (watchdog infrastructure)
**Hardware:** Raspberry Pi Zero 2 W (416MB RAM, 4x A53 @ 1GHz, VideoCore IV)

## 1. Problem Statement

Twitch live streams show black screen after the pre-roll ad ends on picast-z1. YouTube VODs work perfectly with the same hardware decode setup (v4l2m2m-copy + H.264).

## 2. Root Cause Analysis

Twitch injects pre-roll ads directly into the HLS manifest. When the ad ends:

1. **Timestamp discontinuity**: Ad audio at ~0-90s, live stream jumps to ~3965s
2. **Possible codec parameter reset**: New SPS/PPS at the stream boundary may confuse v4l2m2m
3. **mpv's initial A/V sync deadlock**: Audio waits for video timestamp to catch up (never will)

The video decoder (v4l2m2m-copy via bcm2835-codec) starts successfully and mpv reports "starting video playback" — but the actual rendered output is black. HLS segments continue to be fetched. This suggests the decoder stalls or produces blank frames after the discontinuity.

## 3. Hardware Constraints

| Path | Speed | Works? | Why not? |
|------|-------|--------|----------|
| v4l2m2m (zero-copy, drmprime-overlay) | Best | NO | DRM overlay plane fails with panel_orientation=upside_down (EINVAL on atomic commit) |
| v4l2m2m-copy (hw decode, copy to CPU) | Good | YES for YouTube, NO for Twitch ad transition | Decoder stalls at HLS discontinuity |
| Software decode (--hwdec=no) | Bad | TOO SLOW | 105% CPU on single core for 720p60 |

**Additional constraints:**
- Twitch non-partner streams have ONLY `720p60__source_` — no lower quality option
- No AV1 hardware decode on Pi Zero 2 W
- Monitor is physically upside-down, rotated via kernel `panel_orientation=upside_down`

## 4. Candidate Solutions

### Option A: Fix the Stall Watchdog (Quick Win)
**Approach:** Fix the Python bug in v0.6.1's stall detection, let the watchdog auto-restart mpv when playback stalls after the ad. The second start picks up clean stream segments (ad is over).

**Pros:** Simple, leverages existing code, handles ALL future stream interruptions
**Cons:** 30-40s black screen while ad plays + stall detection triggers (user sees: ad -> black -> stream)
**Effort:** 15 minutes (fix one line + test)

### Option B: Use streamlink Instead of yt-dlp for Twitch
**Approach:** Install streamlink on Pi, detect Twitch URLs, use `streamlink --stdout URL best | mpv -` to pipe the stream. Streamlink has Twitch-specific ad handling.

**Pros:** Purpose-built for Twitch, may skip/handle ads cleanly
**Cons:** New dependency, pipe architecture change, may not be in Pi repos
**Effort:** 1-2 hours

### Option C: Pre-resolve URL + Delayed Start
**Approach:** Call yt-dlp to resolve the HLS URL, wait ~30s for the ad to finish, then start mpv with the raw HLS URL (no ytdl-hook). By then the manifest should have clean segments.

**Pros:** No new dependencies, avoids ad entirely
**Cons:** Hacky, ad length varies (15-60s), delay UX is poor
**Effort:** 45 minutes

### Option D: Fix DRM Overlay Plane (Remove panel_orientation)
**Approach:** Remove `panel_orientation=upside_down` from kernel cmdline. Use `wlr-randr --transform 180` for compositor rotation. This allows v4l2m2m zero-copy (drmprime-overlay) which is fast enough for 720p60.

**Pros:** Best performance (zero-copy), fixes root cause of copy overhead
**Cons:** Overlay plane video would appear upside-down (compositor transform doesn't affect overlay), needs testing whether overlay inherits rotation
**Risk:** HIGH — may result in upside-down video, would need `--video-rotate=180` which may not work with drmprime-overlay
**Effort:** 1-2 hours (includes reboot, testing rotation combinations)

### Option E: Hybrid — Stall Watchdog + Live Start Index
**Approach:** Fix stall watchdog (Option A) + add `--demuxer-lavf-o=live_start_index=-1` to start from latest HLS segment. Combined effect: skip as much ad as possible, auto-restart if it still stalls.

**Pros:** Best of A with potential ad skip
**Cons:** live_start_index may not skip inline Twitch ads
**Effort:** 30 minutes

## 5. Questions to Resolve Before Implementing

1. **Is streamlink available in Pi OS Bookworm repos?** (`apt list streamlink`)
2. **Does `--profile=fast` disable something the display pipeline needs?** (It may change the VO config — need to test Twitch WITHOUT `--profile=fast`)
3. **Does `--demuxer-lavf-o=live_start_index=-1` skip past Twitch inline ads?**
4. **Is the black screen from the v4l2m2m decoder stalling, or from the display/compositor?** (Can check by querying `hwdec-current` and `video-out-params` via IPC during the black screen)
5. **Would removing `panel_orientation` and using `--video-rotate=180` in mpv work with drmprime-overlay?**

## 6. Implementation Status

**Phase 1 (DONE):** Option A — Fixed stall watchdog `global _intentional_stop` bug. Watchdog now runs cleanly.

**Phase 2 (DONE):** Option E — Added `--demuxer-lavf-o=live_start_index=-1` to start at live edge. Combined with stall detection (3 checks = 30s) for auto-restart.

**Phase 3 (If needed):** Option B — streamlink if watchdog-based approach has poor UX.

**Phase 4 (Ideal, high risk):** Option D — Fix the DRM overlay to enable zero-copy. This is the real long-term fix but needs careful testing.

**Deployed as v0.7.0 on 2026-03-29.** Awaiting Twitch live stream test to verify end-to-end recovery.
