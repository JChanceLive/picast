# PiCast Multi-TV Per-Device Remote Control

**Status:** Architecture Draft
**Date:** 2026-03-16
**Author:** Claude (Opus 4.6)
**Feature:** Click any TV in Multi-TV panel to open a full remote control modal
**Depends on:** ARCH-MULTI-TV.md (implemented S4-S9)

---

## Overview

When Multi-TV is active, the fleet panel shows device cards for each TV (main, picast-z1, starscreen). Currently these cards are display-only — status indicator + playing title.

This feature upgrades the device cards to show live "now playing" previews (title + progress) and makes them clickable to open a full remote control modal per device. The modal provides skip, pause/resume, and volume control that routes commands to the specific TV.

**Skip behavior:** The skipped video is moved to the end of the shared queue. The TV then receives the next unassigned item from the shared pool.

## User Decisions (from Q&A)

| Question | Answer |
|----------|--------|
| Skip source? | Shared queue — next unassigned item from main pool |
| Skipped video fate? | Move to end of queue (may play later on another TV) |
| UI scope? | Full remote modal per device (skip, pause, volume, now playing) |
| Chrome extension? | Web UI only for now |
| Remote controls? | Skip + Pause/Resume + Volume + Now Playing info + Progress |
| Card detail level? | Now playing preview on cards (title + progress) |

## Architecture

### Conceptual Model

```
Fleet Panel (existing)
  ┌──────────────────────────┐
  │ ● picast       Playing   │  ← clickable card
  │   "Ski Webcam"  12:34    │  ← now playing preview (NEW)
  ├──────────────────────────┤
  │ ● picast-z1    Playing   │  ← clickable card
  │   "Clouds 4K"   0:45    │
  ├──────────────────────────┤
  │ ● starscreen   Manual    │  ← clickable card (greyed controls)
  │   "Vaporwave"   3:21    │
  └──────────────────────────┘
          │ click
          ▼
  ┌──────────────────────────┐
  │    picast-z1 Remote      │  ← modal overlay
  │                          │
  │  Now Playing:            │
  │  "Clouds Timelapse 4K"   │
  │  ▓▓▓▓▓░░░░░  0:45/1:00  │  ← progress bar
  │                          │
  │  [ ⏮ Skip ] [ ⏸ Pause ] │  ← playback controls
  │                          │
  │  🔊 ─────●─── 80%       │  ← volume slider
  │                          │
  │         [ Close ]        │
  └──────────────────────────┘
```

### Command Routing

All remote actions proxy through the main PiCast server, which forwards to the correct device:

```
Web UI  →  POST /api/multi-tv/device/{device_id}/skip
        →  Main PiCast server
        →  Determines target:
              "main" → local player.skip() + multi_tv.on_video_finished("main")
              fleet  → fleet HTTP call + multi_tv re-distribute
```

This avoids the web UI needing to know device IPs — the server handles routing.

### New API Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/multi-tv/device/<id>/status` | GET | Full status for one device (title, position, duration, volume, paused) |
| `/api/multi-tv/device/<id>/skip` | POST | Skip current video, move to queue end, assign next |
| `/api/multi-tv/device/<id>/pause` | POST | Pause playback on device |
| `/api/multi-tv/device/<id>/resume` | POST | Resume playback on device |
| `/api/multi-tv/device/<id>/volume` | POST | Set volume `{"level": 0-100}` |

### GET /api/multi-tv/device/{id}/status Response

```json
{
    "device_id": "picast-z1",
    "online": true,
    "idle": false,
    "paused": false,
    "title": "Clouds Timelapse 1 Extended",
    "url": "https://www.youtube.com/watch?v=jNEjw1fMk-8",
    "position": 45.2,
    "duration": 3600.0,
    "volume": 80,
    "queue_item_id": 574,
    "room": "living room",
    "mood": "chill"
}
```

### Skip Flow (Key Feature)

```
User clicks Skip on picast-z1 remote modal
  → POST /api/multi-tv/device/picast-z1/skip
  → Server:
    1. Get current assignment: picast-z1 → queue_item_id 574
    2. Clear assignment for picast-z1
    3. Move item 574 to end of queue (queue.move_to_end(574))
    4. Call distribute() to assign next item to picast-z1
    5. Push new video to z1 via fleet.play_immediately()
    6. Return new assignment info
```

### Device Capability Matrix

Not all devices support the same controls. The API must handle this gracefully:

| Device | Skip | Pause/Resume | Volume | Progress |
|--------|------|-------------|--------|----------|
| **main** (PiCast) | queue skip + play next | mpv.pause()/resume() | mpv.set_volume() | mpv position/duration |
| **picast-z1** (receiver) | POST /api/stop + POST /api/play (next) | Not supported (no mpv IPC) | POST /api/volume | Not available (no mpv socket query) |
| **starscreen** | POST /api/stop + POST /api/play (next) | Depends on StarScreen API | POST /api/volume | Depends on StarScreen API |

**Capability detection:** The server probes each device's `/api/status` response to determine available controls. Missing fields = control disabled in UI.

### Receiver API Gaps

The picast-z1 receiver (`picast_receiver.py` v0.2.0) currently has:
- `/api/play` — play a URL (immediate)
- `/api/volume` — set volume
- `/api/stop` — stop playback
- `/api/status` — current state (idle, title, url)

**Missing for full remote:**
- `/api/pause` and `/api/resume` — needs mpv IPC socket command forwarding
- Position/duration in `/api/status` — needs mpv `time-pos` and `duration` property reads

These can be added to the receiver in a follow-up session, or the UI can gracefully disable controls that aren't available.

### Queue Move-to-End

New method on QueueManager:

```python
def move_to_end(self, item_id: int) -> bool:
    """Move a queue item to the end of the pending queue.

    Used by multi-TV skip: the skipped video goes to the back
    so it may play later on another TV.
    """
    # Get max position of pending items
    # Update this item's position to max + 1
    # Reset its status to 'pending'
```

### MultiTVManager Changes

New method:

```python
def skip_device(self, device_id: str) -> dict:
    """Skip the current video on a specific device.

    1. Get current assignment for device
    2. Clear assignment
    3. Move skipped item to end of queue
    4. Distribute next item to this device
    5. Return result dict

    Returns:
        {"ok": True, "skipped_item_id": 574, "new_item_id": 575}
        or {"ok": False, "error": "device not assigned"}
    """
```

### Proxy Methods for Pause/Resume/Volume

```python
def pause_device(self, device_id: str) -> bool:
    """Proxy pause to a specific device."""
    if device_id == "main":
        return self._player.mpv.pause()
    return self._fleet_proxy(device_id, "pause")

def resume_device(self, device_id: str) -> bool:
    if device_id == "main":
        return self._player.mpv.resume()
    return self._fleet_proxy(device_id, "resume")

def set_device_volume(self, device_id: str, level: int) -> bool:
    if device_id == "main":
        return self._player.mpv.set_volume(level)
    return self._fleet_proxy_json(device_id, "volume", {"level": level})

def get_device_status(self, device_id: str) -> dict:
    """Get detailed status for a single device."""
    if device_id == "main":
        return self._get_main_status()
    return self._fleet_proxy_get(device_id, "status")
```

### Fleet Proxy Helper

```python
def _fleet_proxy(self, device_id: str, action: str) -> bool:
    """Forward an action to a fleet device via HTTP POST."""
    state = self._fleet._devices.get(device_id)
    if not state or not state.online:
        return False
    base = f"http://{state.config.host}:{state.config.port}"
    try:
        req = urllib.request.Request(f"{base}/api/{action}", method="POST")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
            return data.get("ok", False)
    except Exception:
        return False
```

## Web UI Changes

### Enhanced Fleet Device Cards

The current `renderFleetDevices()` function renders static cards. Updated to:

1. **Show now playing preview** — title + progress bar on each card
2. **Make cards clickable** — opens remote modal for that device
3. **Poll status more frequently** — 3s interval when modal is open

```javascript
function renderFleetDevices(devices) {
    // ... existing state class logic ...
    html +=
        '<div class="ap-fleet-device ap-fleet-clickable" onclick="openDeviceRemote(\'' + dev.device_id + '\')">' +
        '<div class="ap-fleet-indicator ' + stateClass + '"></div>' +
        '<div class="ap-fleet-info">' +
        '<span class="ap-fleet-name">' + esc(dev.device_id) + '</span>' +
        '<span class="ap-fleet-meta">' + roomMood + '</span>' +
        (title ? '<span class="ap-fleet-title">' + title + '</span>' : '') +
        (progress ? '<div class="ap-fleet-progress"><div class="ap-fleet-progress-bar" style="width:' + pct + '%"></div></div>' : '') +
        '</div>' +
        '<span class="ap-fleet-state ' + stateClass + '">' + stateLabel + '</span>' +
        '</div>';
}
```

### Remote Control Modal

New modal overlay (same pattern as existing discover modal):

```html
<div class="modal-overlay" id="device-remote-overlay">
    <div class="modal device-remote-modal">
        <div class="modal-header">
            <span id="remote-device-name">picast-z1</span>
            <button class="modal-close" onclick="closeDeviceRemote()">&times;</button>
        </div>
        <div class="modal-body">
            <div class="remote-now-playing">
                <div id="remote-title" class="remote-title">Loading...</div>
                <div class="remote-progress-wrap">
                    <div class="remote-progress-bar" id="remote-progress-bar"></div>
                </div>
                <div class="remote-time">
                    <span id="remote-position">0:00</span> / <span id="remote-duration">0:00</span>
                </div>
            </div>
            <div class="remote-controls">
                <button class="btn-icon remote-btn" id="remote-skip" onclick="remoteSkip()">
                    <span class="bi-ico">⏭</span><span class="bi-lbl">Skip</span>
                </button>
                <button class="btn-icon remote-btn" id="remote-pause" onclick="remotePause()">
                    <span class="bi-ico">⏸</span><span class="bi-lbl">Pause</span>
                </button>
            </div>
            <div class="remote-volume">
                <span class="remote-vol-icon">🔊</span>
                <input type="range" min="0" max="100" value="80" id="remote-volume-slider"
                       oninput="remoteVolume(this.value)">
                <span id="remote-vol-pct">80%</span>
            </div>
        </div>
    </div>
</div>
```

### JavaScript

```javascript
let remoteDeviceId = null;
let remotePollingInterval = null;

function openDeviceRemote(deviceId) {
    remoteDeviceId = deviceId;
    document.getElementById('remote-device-name').textContent = deviceId;
    document.getElementById('device-remote-overlay').classList.add('visible');
    pollDeviceStatus();
    remotePollingInterval = setInterval(pollDeviceStatus, 2000);
}

function closeDeviceRemote() {
    document.getElementById('device-remote-overlay').classList.remove('visible');
    if (remotePollingInterval) clearInterval(remotePollingInterval);
    remoteDeviceId = null;
}

function pollDeviceStatus() {
    if (!remoteDeviceId) return;
    fetch('/api/multi-tv/device/' + remoteDeviceId + '/status')
        .then(r => r.json())
        .then(updateRemoteUI)
        .catch(() => {});
}

function updateRemoteUI(data) {
    document.getElementById('remote-title').textContent = data.title || 'Nothing playing';
    // Update progress bar, time, pause button state, volume slider...
    if (data.duration > 0) {
        const pct = (data.position / data.duration * 100).toFixed(1);
        document.getElementById('remote-progress-bar').style.width = pct + '%';
    }
    document.getElementById('remote-position').textContent = fmtTime(data.position || 0);
    document.getElementById('remote-duration').textContent = fmtTime(data.duration || 0);
    document.getElementById('remote-volume-slider').value = data.volume || 0;
    document.getElementById('remote-vol-pct').textContent = (data.volume || 0) + '%';

    // Toggle pause/resume button label
    const pauseBtn = document.getElementById('remote-pause');
    if (data.paused) {
        pauseBtn.querySelector('.bi-ico').textContent = '▶';
        pauseBtn.querySelector('.bi-lbl').textContent = 'Resume';
    } else {
        pauseBtn.querySelector('.bi-ico').textContent = '⏸';
        pauseBtn.querySelector('.bi-lbl').textContent = 'Pause';
    }

    // Disable controls if device offline
    const btns = document.querySelectorAll('.remote-btn, #remote-volume-slider');
    btns.forEach(b => b.disabled = !data.online);
}

function remoteSkip() {
    api('multi-tv/device/' + remoteDeviceId + '/skip').then(pollDeviceStatus);
}

function remotePause() {
    const isPaused = document.getElementById('remote-pause')
        .querySelector('.bi-lbl').textContent === 'Resume';
    const action = isPaused ? 'resume' : 'pause';
    api('multi-tv/device/' + remoteDeviceId + '/' + action).then(pollDeviceStatus);
}

function remoteVolume(level) {
    document.getElementById('remote-vol-pct').textContent = level + '%';
    api('multi-tv/device/' + remoteDeviceId + '/volume', {level: parseInt(level)});
}
```

## CSS Additions

```css
/* Clickable fleet cards */
.ap-fleet-clickable { cursor: pointer; }
.ap-fleet-clickable:active { transform: scale(0.98); }

/* Progress bar on fleet cards */
.ap-fleet-progress { height: 2px; background: var(--surface-2); border-radius: 1px; margin-top: 4px; }
.ap-fleet-progress-bar { height: 100%; background: var(--accent); border-radius: 1px; transition: width 1s linear; }

/* Remote modal */
.device-remote-modal { max-width: 340px; padding: 16px; }
.remote-title { font-size: 1rem; font-weight: 600; margin-bottom: 8px; word-break: break-word; }
.remote-progress-wrap { height: 4px; background: var(--surface-2); border-radius: 2px; margin: 8px 0; }
.remote-progress-bar { height: 100%; background: var(--accent); border-radius: 2px; transition: width 1s linear; }
.remote-time { font-size: 0.75rem; color: var(--text-muted); display: flex; justify-content: space-between; }
.remote-controls { display: flex; gap: 12px; justify-content: center; margin: 16px 0; }
.remote-btn { min-width: 80px; }
.remote-volume { display: flex; align-items: center; gap: 8px; }
.remote-volume input[type=range] { flex: 1; }
.remote-vol-pct { font-size: 0.75rem; width: 32px; text-align: right; }
```

## Receiver Upgrades (picast-z1)

To support full remote, the receiver needs two additions:

### 1. Pause/Resume via mpv IPC

```python
def _mpv_command(self, command: list) -> dict | None:
    """Send a command to mpv via IPC socket."""
    import socket as sock
    try:
        s = sock.socket(sock.AF_UNIX, sock.SOCK_STREAM)
        s.connect(_mpv_socket)
        payload = json.dumps({"command": command}) + "\n"
        s.sendall(payload.encode())
        resp = s.recv(4096)
        s.close()
        return json.loads(resp)
    except Exception:
        return None

@app.route("/api/pause", methods=["POST"])
def api_pause():
    result = _mpv_command(["set_property", "pause", True])
    return jsonify({"ok": result is not None})

@app.route("/api/resume", methods=["POST"])
def api_resume():
    result = _mpv_command(["set_property", "pause", False])
    return jsonify({"ok": result is not None})
```

### 2. Position/Duration in Status

```python
@app.route("/api/status", methods=["GET"])
def api_status():
    idle = _is_idle()
    result = {
        "idle": idle,
        "title": _current_video.get("title", ""),
        "url": _current_video.get("url", ""),
        "autoplay_enabled": _autoplay_enabled,
    }
    if not idle:
        pos = _mpv_command(["get_property", "time-pos"])
        dur = _mpv_command(["get_property", "duration"])
        paused = _mpv_command(["get_property", "pause"])
        vol = _mpv_command(["get_property", "volume"])
        if pos: result["position"] = pos.get("data", 0)
        if dur: result["duration"] = dur.get("data", 0)
        if paused: result["paused"] = paused.get("data", False)
        if vol: result["volume"] = vol.get("data", 100)
    return jsonify(result)
```

## Files Modified

| File | Change |
|------|--------|
| `src/picast/server/multi_tv.py` | Add `skip_device()`, `pause_device()`, `resume_device()`, `set_device_volume()`, `get_device_status()`, fleet proxy helpers |
| `src/picast/server/app.py` | Add 5 new `/api/multi-tv/device/<id>/*` endpoints |
| `src/picast/server/queue_manager.py` | Add `move_to_end()` method |
| `src/picast/server/templates/player.html` | Enhanced fleet cards (clickable, now playing preview), remote modal HTML, JS |
| `src/picast/server/static/style.css` | Fleet card hover/click styles, remote modal styles, progress bars |
| `picast-z1: picast_receiver.py` | Add `/api/pause`, `/api/resume`, expand `/api/status` with position/duration/paused/volume via mpv IPC |
| `tests/test_multi_tv.py` | Tests for skip_device, pause/resume proxy, volume proxy, move_to_end |

## Implementation Sessions

### Session 1: Backend API + Skip Logic
1. Add `move_to_end()` to QueueManager + tests
2. Add `skip_device()` to MultiTVManager + tests
3. Add proxy methods (pause, resume, volume, status) to MultiTVManager
4. Wire up 5 new endpoints in app.py
5. Test with curl against live PiCast

### Session 2: Web UI (Cards + Modal)
1. Upgrade fleet device cards with now playing preview + clickable
2. Build remote control modal (HTML + CSS)
3. Wire up JavaScript (polling, skip, pause, volume)
4. Test on mobile (iPhone) — modal must work at 320px width
5. Deploy to Pi

### Session 3: Receiver Upgrades + Polish
1. Add mpv IPC socket commands to picast_receiver.py
2. Add `/api/pause`, `/api/resume` endpoints
3. Expand `/api/status` with position/duration/paused/volume
4. Deploy receiver update to z1
5. End-to-end test: skip z1 from web UI on phone

## Edge Cases

| Scenario | Behavior |
|----------|----------|
| Skip on offline device | Return error, don't modify queue |
| Skip when no next item | Skip succeeds (video moved to end), device goes idle |
| Pause on device that doesn't support it | Return `{"ok": false}`, UI shows disabled button |
| Volume on starscreen (manual override) | Proxy works regardless — volume is always controllable |
| Two users open remote for same device | Both see same status (polling). Skip from either works (idempotent assignment) |
| Modal open when device goes offline | pollDeviceStatus fails gracefully, controls disabled |
| Skip the last item in queue | Item moved to end = it's the only item. Device stays idle (no re-assign to same item) |

## Open Questions

1. Should the progress bar on fleet cards be real-time (requires position data from every poll) or approximate (based on duration and elapsed wall-clock time)?
2. Should the remote modal include a "Play specific URL" input for ad-hoc playback on a specific TV?
3. StarScreen compatibility: Does StarScreen's `/api/status` return position/duration/paused fields, or does it need similar mpv IPC additions?
