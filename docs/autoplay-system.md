# PiCast Autoplay System (v0.24.4)

Extracted from CLAUDE.md. Full autoplay endpoints, weight formula, cross-block learning, and state tracking.

---

## How It Works

PiPulse sends `POST /api/autoplay/trigger` with `{"block_name": "...", "display_name": "..."}` on TIM block transitions. PiCast selects a weighted-random video from the pool for that block and plays it via mpv.

## Key Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/autoplay` | GET | Config, mappings, pool summaries |
| `/api/autoplay/trigger` | POST | Trigger playback for a block (PiPulse calls this) |
| `/api/autoplay/rate` | POST | Thumbs up/down from queue page UI |
| `/api/autoplay/pool/<block>` | GET | List videos in a block's pool |
| `/api/autoplay/pool/<block>` | POST | Add video to block pool |
| `/api/autoplay/suggestions/<block>` | GET | Cross-block suggestions |
| `/api/autoplay/export` | GET | Export all pools as JSON |
| `/api/autoplay/import` | POST | Import pools (merge mode) |
| `/api/status` | GET | Includes `autoplay_current` for UI rating buttons |

## Self-Learning Weight Formula

```
weight = base * skip_penalty * completion_boost
```

| Component | Formula | Notes |
|-----------|---------|-------|
| base | liked=3.0, neutral=1.0, disliked=0.1 | From explicit thumbs up/down |
| skip_penalty | 0.7^skip_count | Every skip penalizes, no time threshold |
| completion_boost | min(1 + completions*0.2, 2.0) | Natural playback to end or >80% duration |

- **Skip button** = always penalizes (user intent is clear)
- **Block transition / play new / manual override** = no penalty
- **Auto-shelve** at 5 skips (video effectively removed from rotation)

## Cross-Block Learning

Signals emitted on: thumbs-up (strength 1.5), 5th completion (strength 1.0). Suggestions appear for other blocks where the video doesn't already exist.

## Autoplay State Tracking (app.py)

Module-level dicts in `create_app()`:
- `_autoplay_current` -- `{video_id, block_name, title}` for UI rating buttons
- `_autoplay_start_time` -- monotonic timestamp
- `_autoplay_completing` -- snapshot for deferred completion processing

**Critical:** `_handle_item_complete()` has a video_id guard (v0.24.2 fix) -- verifies the completing item matches `_autoplay_current` before clearing it. This prevents a race condition where `play_now()` skip causes the old video's callback to wipe the new autoplay state.

## Testing Autoplay Manually

```bash
# Fire a trigger (simulates PiPulse block transition)
curl -X POST http://picast.local:5050/api/autoplay/trigger \
  -H "Content-Type: application/json" \
  -d '{"block_name": "morning-foundation", "display_name": "Morning Foundation"}'

# Check autoplay_current is set (needed for UI thumbs)
curl -s http://picast.local:5050/api/status | python3 -m json.tool

# Check pool data (ratings, skips, plays)
curl -s http://picast.local:5050/api/autoplay/pool/morning-foundation | python3 -m json.tool

# Check cross-block suggestions
curl -s http://picast.local:5050/api/autoplay/suggestions/creation-stack | python3 -m json.tool

# Export/import round-trip
curl -s http://picast.local:5050/api/autoplay/export -o /tmp/export.json
curl -X POST http://picast.local:5050/api/autoplay/import \
  -H "Content-Type: application/json" -d @/tmp/export.json
```

## play_duration Gotcha

`play_duration` from mpv includes buffering/loading time (measured from mpv process start, not playback start). A video that buffers for 30s then plays for 27s reports as 58s. This is why the skip penalty has no time threshold -- time-based checks are unreliable with YouTube buffering.
