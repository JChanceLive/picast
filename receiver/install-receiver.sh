#!/bin/bash
# PiCast Receiver — one-command install for Pi Zero 2 W fleet displays.
#
# Usage (on the Pi):
#   curl -sSL https://raw.githubusercontent.com/JChanceLive/picast/main/receiver/install-receiver.sh | bash
#
# Or copy manually:
#   scp -r receiver/ picast-z1:~/picast-receiver/
#   ssh picast-z1 "bash ~/picast-receiver/install-receiver.sh"

set -euo pipefail

echo "=== PiCast Receiver Install ==="

# Check we're on a Pi
if ! grep -q 'Raspberry Pi' /proc/cpuinfo 2>/dev/null; then
    echo "Warning: Not detected as Raspberry Pi (continuing anyway)"
fi

# Install dependencies
echo "Installing dependencies..."
sudo apt-get update -qq
sudo apt-get install -y -qq mpv python3-flask yt-dlp

# Create receiver directory
INSTALL_DIR="$HOME/picast-receiver"
mkdir -p "$INSTALL_DIR"

# Copy files if running from the receiver directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "$SCRIPT_DIR/picast_receiver.py" ]; then
    cp "$SCRIPT_DIR/picast_receiver.py" "$INSTALL_DIR/"
    echo "Copied picast_receiver.py to $INSTALL_DIR"
else
    echo "Error: picast_receiver.py not found in $SCRIPT_DIR"
    echo "Copy it manually: scp picast_receiver.py $(hostname):$INSTALL_DIR/"
    exit 1
fi

# Install systemd service
echo "Installing systemd service..."
sudo cp "$SCRIPT_DIR/picast-receiver.service" /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable picast-receiver
sudo systemctl start picast-receiver

echo ""
echo "=== Install Complete ==="
echo "Service: sudo systemctl status picast-receiver"
echo "Logs:    journalctl -u picast-receiver -f"
echo "Health:  curl http://$(hostname).local:5050/api/health"
