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

## 2026-03-01: HDMI Loss After Metal Frame Mount + Power Supply Swap

### Symptoms
- Monitor lost signal ("no signal") after touching the metal heatsink frame
- Power plug was also changed around the same time
- Pi was running but completely headless (SSH worked, PiCast service running)

### Investigation

**Throttle register with bad PSU:** `0x50005` (undervoltage + throttled NOW)

**Kernel log:** Wall-to-wall `Undervoltage detected!` every 2-4 seconds since boot. Voltage bouncing constantly between normal and undervoltage.

**HDMI status:** `/sys/class/drm/card0-HDMI-A-1/status` = `disconnected`. xrandr only showed `NOOP-1` (virtual display). Physical HDMI handshake was failing entirely.

**No ESD damage evidence:** Zero bus errors, no USB resets, no MMC/SD errors, no I2C faults, no kernel panics. OTP fuses normal (`3020000a`). USB bus healthy.

### Power Supply Comparison

| PSU | Throttle | HDMI | Result |
|-----|----------|------|--------|
| Plug #1 (new, suspect) | `0x50005` (active UV) | disconnected | No display |
| Plug #2 (original) | `0x50000` (UV at boot only) | disconnected | No display |
| Plug #3 (best) | `0x0` (clean) | connected | Working |

### Root Cause

**Power supply was too weak to drive the HDMI PHY.** The Pi 3B+ HDMI output requires stable voltage to maintain the physical layer handshake (DDC/I2C for EDID). When voltage sags, the HDMI controller can't negotiate with the monitor, and the kernel reports the connector as `disconnected`.

The touch on the metal heatsink frame was a red herring / coincidence. The real issue was the PSU swap. The original PSU was borderline (UV at boot only), and the new PSU was insufficient.

### Metal Heatsink Frame - ESD Concern

The Pi is mounted in a metal frame that doubles as a heatsink. This creates a direct ESD path to the SoC when touched. While no damage occurred this time (no bus errors), this is a legitimate risk:

- **Mitigation options:**
  - Electrical tape or rubber gasket on exposed edges
  - Ground pad connected to Pi's ground pin (equalizes potential before discharge)
  - Avoid touching the frame while Pi is running

### Key Learnings

1. **HDMI loss on Pi = check power first.** Insufficient voltage causes HDMI to drop entirely, not just degrade. The symptom looks like a dead port but it's pure power starvation.
2. **`throttled=0x50005` vs `0x50000` vs `0x0`:** Active undervoltage (bit 0) = current problem. Historical only (bit 16) = happened at boot, may be fine now. `0x0` = clean power.
3. **Three PSUs tested, only one delivered clean power.** Not all micro-USB chargers are equal. Short, thick cables and 5.1V/2.5A+ rated supplies are essential for Pi 3B+.
4. **ESD through metal heatsink cases is a real risk** even if no damage occurred this time. Consider physical insulation on touchable surfaces.
