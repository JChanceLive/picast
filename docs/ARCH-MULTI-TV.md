# PiCast Multi-TV Queue Distribution

**Status:** Architecture Draft
**Date:** 2026-03-12
**Author:** Claude (Opus 4.6)
**Feature:** Distribute queue items across all connected TVs, one video per screen

---

## Overview

Multi-TV mode takes the PiCast queue and fans it out across all connected displays — the main PiCast unit plus all fleet devices. Each TV gets one video. When a video finishes on any TV, that TV pulls the next unplayed item from the queue. If a video is offline or a live stream that's down, it's skipped during a pre-check before distribution begins.

This mode **replaces** autopilot when active. A toggle icon on the queue page enables/disables it.

## User Decisions (from Q&A)

| Question | Answer |
|----------|--------|
| When video finishes on a TV? | Pull next from queue |
| Main PiCast counts as a TV? | Yes — queue item #1 goes to main |
| More TVs than queue items? | Extra TVs stay idle/screensaver |
| Relationship to autopilot? | Replaces autopilot while active |
| Offline/live stream detection? | Pre-check all URLs before distributing |
| Web UI toggle? | Icon button on queue page controls bar |
| New items added while active? | Auto-distribute to idle TVs |

## Architecture

### Conceptual Model

```
Queue:  [V1, V2, V3, V4, V5, V6, ...]
         │    │    │
         ▼    ▼    ▼
TVs:   main  z1  (future devices)
         │    │
         ▼    ▼
      finish  finish
         │    │
         ▼    ▼
       V4    V5   (next from queue)
```

The queue is a shared work pool. TVs are workers. Each TV claims the next available item. This is a **work-stealing** pattern — simple, fair, and handles any TV count dynamically.

### New Module: `multi_tv.py`

A single new module at `src/picast/server/multi_tv.py` containing the `MultiTVManager` class.

```python
class MultiTVManager:
    """Distributes queue items across all connected displays."""

    def __init__(self, queue: QueueManager, fleet: FleetManager, player, sources):
        self._queue = queue           # access to queue items
        self._fleet = fleet           # fleet device push + status
        self._player = player         # local mpv player
        self._sources = sources       # URL validation/metadata
        self._enabled = False         # toggle state
        self._assignments = {}        # device_id -> queue_item_id
        self._lock = threading.Lock()
        self._watcher = None          # background thread
```

### Key Methods

| Method | Purpose |
|--------|---------|
| `enable()` | Pause autopilot, pre-check queue, distribute initial batch |
| `disable()` | Stop watching, clear assignments, resume autopilot |
| `distribute()` | Assign pending queue items to idle TVs |
| `on_video_finished(device_id)` | Called when a TV finishes — triggers next assignment |
| `on_queue_changed()` | Called when queue is modified — auto-distribute to idle TVs |
| `pre_check(items)` | Validate URLs with yt-dlp `--simulate` (5s timeout each) |
| `get_status()` | Current state for API + UI |

### Pre-Check Flow

Before distributing, Multi-TV validates each queue item:

```
1. For each pending queue item:
   a. Run yt-dlp --simulate --socket-timeout 5 <url>
   b. If success → mark as "checked_ok"
   c. If fail (offline, private, removed) → mark as "checked_skip"
   d. If timeout → mark as "checked_skip" (treat as offline)
2. Only distribute checked_ok items
3. Skipped items stay in queue but are bypassed
4. Re-check skipped items on next distribute() cycle
```

**Threading:** Pre-check runs in a background thread to avoid blocking the API. The UI shows a "Checking N videos..." spinner during this phase.

**Caching:** A URL that was checked_ok in the last 5 minutes doesn't need re-checking. Avoids hammering yt-dlp on every distribute cycle.

### Device Registry

Multi-TV sees all "TVs" as a flat list: `[main, picast-z1, ...]`

The main PiCast unit is a special device — it uses the local `player.play_now()` instead of HTTP push. Fleet devices use `fleet.push_content()`.

```python
def _get_all_devices(self) -> list[str]:
    """Returns device IDs including 'main' for local player."""
    return ["main"] + self._fleet.device_ids

def _push_to_device(self, device_id: str, url: str, title: str) -> bool:
    if device_id == "main":
        return self._player.play_now(url, title)
    else:
        return self._fleet.push_content(device_id, {"url": url, "title": title})
```

### Assignment Tracking

```python
_assignments: dict[str, int | None]  # device_id -> queue_item_id or None

# Example state:
{
    "main": 42,        # playing queue item #42
    "picast-z1": 43,   # playing queue item #43
}
```

When a video finishes on a device, the assignment is cleared and `distribute()` is called to fill the gap.

### Video Completion Detection

**Main device:** Hook into the existing `_handle_item_complete()` callback in app.py. When multi-TV is active, call `multi_tv.on_video_finished("main")` instead of normal queue advance.

**Fleet devices:** The fleet manager already polls device status. Multi-TV adds a polling watcher thread:

```python
def _watch_loop(self):
    """Background thread: poll fleet devices every 10s, detect completions."""
    while self._enabled:
        self._fleet.poll_devices()
        for dev_id in self._fleet.device_ids:
            if dev_id in self._assignments and self._fleet.is_device_idle(dev_id):
                # Video finished on this device
                self.on_video_finished(dev_id)
        time.sleep(10)
```

### Queue Interaction

Multi-TV **consumes** queue items in order but doesn't remove them — it advances their status from `pending` → `playing` → `played`, same as normal single-TV playback. The queue page still shows all items with their status.

When the user adds a new item to the queue while multi-TV is active:
1. `queue_add()` in app.py detects multi-TV is enabled
2. Calls `multi_tv.on_queue_changed()`
3. Multi-TV checks for idle devices
4. If idle device available → pre-check new item → distribute

### Autopilot Interaction

Multi-TV and autopilot are **mutually exclusive**:

```python
def enable(self):
    if self._autopilot_engine:
        self._autopilot_engine.stop()  # pause autopilot
    self._enabled = True
    self.distribute()

def disable(self):
    self._enabled = False
    if self._autopilot_engine and self._autopilot_config.enabled:
        self._autopilot_engine.start()  # resume autopilot
```

The autopilot config (`enabled = true`) stays in picast.toml — multi-TV just temporarily pauses it.

## API Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/multi-tv` | GET | Current multi-TV status (enabled, assignments, checked URLs) |
| `/api/multi-tv/enable` | POST | Enable multi-TV mode, start distributing |
| `/api/multi-tv/disable` | POST | Disable multi-TV mode, resume autopilot |
| `/api/multi-tv/redistribute` | POST | Force re-check + redistribute (manual refresh) |

### GET /api/multi-tv Response

```json
{
    "enabled": true,
    "devices": [
        {"device_id": "main", "online": true, "queue_item_id": 42, "title": "Video A", "status": "playing"},
        {"device_id": "picast-z1", "online": true, "queue_item_id": 43, "title": "Video B", "status": "playing"}
    ],
    "queue_remaining": 4,
    "skipped_urls": ["https://youtube.com/watch?v=offline123"],
    "checking": false
}
```

## Web UI

### Toggle Button

Added to the controls bar in `player.html` (line 61-71), alongside the existing Pause/Skip/Stop/Loop buttons:

```html
<button onclick="toggleMultiTV()" class="btn-icon" id="btn-multi-tv"
        aria-label="Multi-TV" title="Distribute queue across all TVs">
    <span class="bi-ico">📺</span>
    <span class="bi-lbl">Multi</span>
</button>
```

**Active state:** When multi-TV is on, the button gets the `active` class (highlighted), same pattern as the Loop button.

### Status Display

When multi-TV is enabled, a small status bar appears below the controls:

```
📺 Multi-TV: 2/2 TVs active | 4 remaining in queue
   main: "Video A" ▶  |  picast-z1: "Video B" ▶
```

This reuses the existing `timer-status` pattern (show/hide div below controls).

### Pre-Check Spinner

While checking URLs:
```
📺 Checking 6 videos... (3/6)
```

## Config

No new config needed. Multi-TV uses:
- Existing `[autopilot.fleet.devices.*]` for device list
- Existing queue system for video list
- Runtime toggle only (not persisted across restarts — starts disabled)

**Future config option** (not in v1):
```toml
[multi_tv]
pre_check_timeout = 5    # seconds per URL
poll_interval = 10       # seconds between fleet polls
```

## Files Modified

| File | Change |
|------|--------|
| `src/picast/server/multi_tv.py` | **NEW** — MultiTVManager class |
| `src/picast/server/app.py` | Wire up multi-TV endpoints, hook into queue_add + item_complete |
| `src/picast/server/templates/player.html` | Add multi-TV toggle button + status display |
| `src/picast/server/static/app.js` | Add toggleMultiTV(), multi-TV status polling, UI updates |
| `src/picast/server/static/style.css` | Active state for multi-TV button, status bar styles |
| `tests/test_multi_tv.py` | **NEW** — Unit tests for MultiTVManager |

## Edge Cases

| Scenario | Behavior |
|----------|----------|
| 0 items in queue, enable multi-TV | Enable succeeds but nothing plays. Status shows "0 remaining". |
| 1 item, 3 TVs | Item #1 goes to main. Other TVs stay idle. |
| All URLs offline | All skipped. Status shows "0 playable". TVs stay idle. |
| Fleet device goes offline mid-play | Assignment stays. Watcher detects offline. Item returns to "pending" for next idle TV. |
| User manually plays on a TV | Overrides multi-TV assignment for that device. Multi-TV skips that TV. |
| Queue cleared while multi-TV active | Playing videos continue. No new assignments. Status shows "0 remaining". |
| User disables multi-TV while videos play | Videos keep playing (don't interrupt). Autopilot resumes on next block transition. |

## Testing Strategy

1. **Unit tests** for MultiTVManager (mock queue, fleet, player)
2. **Pre-check tests** (mock yt-dlp: success, fail, timeout)
3. **Distribution tests** (N items, M devices, various combinations)
4. **Integration tests** with real FleetManager (mocked HTTP)
5. **Edge case tests** for each scenario above

## Sequence: Enable Multi-TV

```
User clicks 📺 Multi on queue page
  → POST /api/multi-tv/enable
  → MultiTVManager.enable()
    → autopilot_engine.stop()
    → pre_check(queue.get_pending())
      → yt-dlp --simulate for each URL (background thread)
    → distribute()
      → assign item #1 → main (player.play_now)
      → assign item #2 → picast-z1 (fleet.push_content)
    → start _watch_loop thread
  → Return status JSON
  → UI shows active state + status bar
```

## Sequence: Video Finishes

```
mpv on main finishes video
  → _handle_item_complete()
  → detects multi-TV active
  → multi_tv.on_video_finished("main")
    → clear assignment for "main"
    → queue.mark_played(item_id)
    → get next pending item
    → pre_check (if not recently checked)
    → push to main
    → update assignment
```

## Open Questions for Implementation

1. Should the pre-check run yt-dlp as a subprocess or use the Python yt-dlp library? (Subprocess is simpler but slower to spawn.)
2. Should the multi-TV status be included in the regular `/api/status` poll, or require a separate poll from the UI?
3. Should there be a maximum number of concurrent pre-checks (e.g., 3 at a time) to avoid yt-dlp rate limiting?
