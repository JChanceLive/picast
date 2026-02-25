# PiCast Power Diagnostics

## Hardware

- **Model:** Raspberry Pi 3 Model B Plus Rev 1.3
- **IP:** 10.0.0.25 (DHCP)

## 2026-02-21: Undervoltage Investigation

### Symptoms
- Low voltage warnings on screen
- YouTube video playback failing
- Started after attaching a small GPIO fan

### Findings

**Throttle register:** `0x50005`

| Bit | Meaning | Active |
|-----|---------|--------|
| 0 | Undervoltage now | YES |
| 2 | Currently throttled | YES |
| 16 | Undervoltage since boot | YES |
| 18 | Throttling since boot | YES |

**Readings (fan removed, idle with YouTube stream):**
- Voltage: 1.2000V (core)
- Temperature: 51.0C
- CPU frequency: 600MHz (throttled from 1400MHz max)
- Load average: 1.45, 1.53, 1.54

**Kernel log (dmesg):**
```
hwmon hwmon1: Undervoltage detected!
hwmon hwmon1: Voltage normalised
(flickering every ~2-4 seconds)
```

### Root Cause

The power supply is **borderline even without the fan**. The Pi 3B+ draws ~700-800mA idle, more under GPU load (YouTube decoding). The PSU/cable combination cannot maintain stable 5V under load.

Adding the fan (~100-200mA on GPIO) pushed it firmly into undervoltage, causing:
- GPU throttling -> YouTube decode failures
- CPU throttled to 600MHz (should be 1400MHz)

### Fix Required

Replace the power supply. Pi 3B+ needs:
- **5.1V / 2.5A minimum** (official RPi PSU recommended)
- Short, thick micro-USB cable (long/thin cables cause voltage drop)
- The extra 0.1V (5.1V vs 5.0V) compensates for cable resistance

Once PSU is upgraded, the fan can safely be reconnected.

### SSH Note

SSH key auth was broken on this Pi (and focusboard) due to wrong permissions on `~/.ssh/authorized_keys`. Fixed with:
```bash
chmod 700 ~/.ssh && chmod 600 ~/.ssh/authorized_keys
```
This was unrelated to the power issue - likely happened during initial setup.
