# PiCast Network Access Guide

## Access Methods

### 1. mDNS / Bonjour (Recommended)

If avahi-daemon is running on the Pi:

```
http://picast.local:5050
```

### 2. Direct Pi IP

Connect to the Pi directly over your local network:

```
http://<PI_IP>:5050
```

**Requirements:**
- Phone/laptop on same WiFi network as Pi
- Pi firewall allows port 5050
- No VPN or security software blocking local traffic

**Setup on Pi:**
```bash
sudo apt install avahi-daemon
sudo systemctl enable avahi-daemon
sudo systemctl start avahi-daemon
```

**Note:** mDNS works on iOS/macOS natively. Android support varies by device/version.

### 3. SSH Tunnel (Fallback)

If direct access is blocked (e.g., by Bitdefender or corporate network):

**From Mac:**
```bash
ssh -L 5050:localhost:5050 youruser@picast.local
```

Then access at `http://localhost:5050` from Mac, or `http://<mac-ip>:5050` from phone.

**Persistent tunnel via LaunchAgent:** Already configured at `~/Library/LaunchAgents/com.picast.tunnel.plist`

## Pi Firewall Setup

Check current status:
```bash
sudo ufw status
sudo iptables -L -n
```

Allow PiCast port:
```bash
sudo ufw allow 5050/tcp
```

Verify Flask binds to all interfaces (should be `0.0.0.0`):
```bash
# Check picast.toml
cat ~/.config/picast/picast.toml | grep host
# Should show: host = "0.0.0.0"
```

## Bitdefender Issue

Bitdefender (Mac) may block direct connections to local network devices. Symptoms:
- `curl http://<PI_IP>:5050` works from terminal
- Browser shows connection refused or timeout

**Workarounds:**
1. Add the Pi's IP to Bitdefender exceptions (Network Protection > Exceptions)
2. Temporarily disable "Online Threat Prevention" for testing
3. Use SSH tunnel method (tunneled traffic is not inspected)

## PWA (Add to Home Screen)

PiCast includes a PWA manifest for app-like experience on mobile:

1. Open PiCast in Safari (iOS) or Chrome (Android)
2. Tap Share > "Add to Home Screen"
3. PiCast appears as a standalone app (no browser chrome)

## YouTube Authentication

YouTube auth is handled locally on the Pi. See [youtube-setup.md](youtube-setup.md) for details.

If videos stop playing, open Chromium on the Pi desktop and verify you're still signed in to YouTube, then restart PiCast:

```bash
sudo systemctl restart picast
```
