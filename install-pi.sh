#!/bin/bash
# PiCast - One-command Pi setup
# Usage: curl -sSL https://raw.githubusercontent.com/JChanceLive/picast/main/install-pi.sh | bash
#
# Options (set as env vars before running):
#   PICAST_VERSION=0.2.0   Install a specific version
#   PICAST_EXTRAS=telegram  Install optional extras (comma-separated: telegram,discovery)
#   PICAST_SKIP_SERVICE=1  Skip systemd service setup

set -euo pipefail

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

log() { echo -e "  ${GREEN}✓${NC} $1"; }
warn() { echo -e "  ${YELLOW}⚠${NC} $1"; }
err() { echo -e "  ${RED}✗${NC} $1"; }
step() { echo -e "\n${YELLOW}[$1/$TOTAL_STEPS] $2${NC}"; }

TOTAL_STEPS=7
PICAST_VERSION="${PICAST_VERSION:-}"
PICAST_EXTRAS="${PICAST_EXTRAS:-}"
PICAST_SKIP_SERVICE="${PICAST_SKIP_SERVICE:-0}"

echo -e "${BLUE}"
echo "  ╔═══════════════════════════════════╗"
echo "  ║          PiCast Installer          ║"
echo "  ║    Media Center for Raspberry Pi   ║"
echo "  ╚═══════════════════════════════════╝"
echo -e "${NC}"

# --- Step 1: Check system ---
step 1 "Checking system..."

# Check for Python 3.9+
if ! command -v python3 &> /dev/null; then
    err "Python 3 not found. Install with: sudo apt install python3"
    exit 1
fi

PYTHON_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PYTHON_MAJOR=$(echo "$PYTHON_VERSION" | cut -d. -f1)
PYTHON_MINOR=$(echo "$PYTHON_VERSION" | cut -d. -f2)

if [ "$PYTHON_MAJOR" -lt 3 ] || ([ "$PYTHON_MAJOR" -eq 3 ] && [ "$PYTHON_MINOR" -lt 9 ]); then
    err "Python 3.9+ required (found $PYTHON_VERSION)"
    exit 1
fi
log "Python $PYTHON_VERSION"

if [ -f /sys/firmware/devicetree/base/model ]; then
    MODEL=$(cat /sys/firmware/devicetree/base/model | tr -d '\0')
    log "Detected: $MODEL"
else
    warn "Not a Raspberry Pi (or can't detect model)"
    echo "    Continuing anyway - PiCast works on any Linux with mpv."
fi

INSTALL_USER=$(whoami)
INSTALL_UID=$(id -u)
log "Installing as: $INSTALL_USER (uid $INSTALL_UID)"

# --- Step 2: Install system dependencies ---
step 2 "Installing system dependencies..."

if command -v apt &> /dev/null; then
    sudo apt update -qq
    sudo apt install -y -qq mpv socat python3-pip python3-venv
    log "System packages installed"
elif command -v dnf &> /dev/null; then
    sudo dnf install -y mpv socat python3-pip
    log "System packages installed"
else
    warn "Package manager not detected. Ensure mpv is installed manually."
fi

# Install/upgrade yt-dlp
if pip3 install --user --upgrade yt-dlp 2>/dev/null; then
    log "yt-dlp installed"
elif pip3 install --user --break-system-packages --upgrade yt-dlp 2>/dev/null; then
    log "yt-dlp installed (break-system-packages)"
else
    warn "Could not install yt-dlp via pip. Install manually if needed."
fi

# --- Step 3: Install PiCast ---
step 3 "Installing PiCast..."

PICAST_PKG="picast"
if [ -n "$PICAST_VERSION" ]; then
    PICAST_PKG="picast==$PICAST_VERSION"
fi

# Build extras string
EXTRAS=""
if [ -n "$PICAST_EXTRAS" ]; then
    EXTRAS="[$PICAST_EXTRAS]"
fi

if pip3 install --user "${PICAST_PKG}${EXTRAS}" 2>/dev/null; then
    log "PiCast installed"
elif pip3 install --user --break-system-packages "${PICAST_PKG}${EXTRAS}" 2>/dev/null; then
    log "PiCast installed (break-system-packages)"
else
    err "Failed to install PiCast"
    exit 1
fi

# Ensure ~/.local/bin is in PATH
if ! command -v picast-server &> /dev/null; then
    export PATH="$HOME/.local/bin:$PATH"
    if ! grep -q '.local/bin' ~/.bashrc 2>/dev/null; then
        echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.bashrc
        warn "Added ~/.local/bin to PATH in .bashrc"
    fi
fi

# Verify installation
if command -v picast-server &> /dev/null; then
    INSTALLED_VERSION=$(picast-server --version 2>/dev/null || echo "unknown")
    log "picast-server available"
else
    warn "picast-server not found in PATH. You may need to restart your shell."
fi

# --- Step 4: Create data directory ---
step 4 "Setting up data directory..."
mkdir -p ~/.picast
log "Created ~/.picast"

# --- Step 5: Create config ---
step 5 "Creating default config..."
mkdir -p ~/.config/picast

if [ ! -f ~/.config/picast/picast.toml ]; then
    cat > ~/.config/picast/picast.toml << 'TOML'
[server]
host = "0.0.0.0"
port = 5050
mpv_socket = "/tmp/mpv-socket"
ytdl_format = "bestvideo[height<=1080][fps<=30]+bestaudio/best[height<=1080]"

# Uncomment to enable Telegram bot
# [telegram]
# bot_token = "YOUR_TOKEN_HERE"
# allowed_users = []
TOML
    log "Config created at ~/.config/picast/picast.toml"
else
    log "Config already exists, skipping"
fi

# --- Step 6: Configure mpv ---
step 6 "Configuring mpv..."
mkdir -p ~/.config/mpv

if [ ! -f ~/.config/mpv/mpv.conf ]; then
    cat > ~/.config/mpv/mpv.conf << 'MPV'
cache=yes
demuxer-max-bytes=50M
demuxer-max-back-bytes=25M
MPV
    log "mpv config created"
else
    log "mpv config already exists, skipping"
fi

# Add user to video group (needed for HDMI output)
if ! groups | grep -q video 2>/dev/null; then
    sudo usermod -aG video "$INSTALL_USER" 2>/dev/null || true
    warn "Added $INSTALL_USER to video group (reboot needed for HDMI)"
fi

# --- Step 7: Install systemd service ---
step 7 "Setting up systemd service..."

if [ "$PICAST_SKIP_SERVICE" = "1" ]; then
    warn "Skipping systemd service (PICAST_SKIP_SERVICE=1)"
else
    SERVICE_FILE="/etc/systemd/system/picast.service"
    sudo tee "$SERVICE_FILE" > /dev/null << SYSTEMD
[Unit]
Description=PiCast Media Server
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$INSTALL_USER
Environment=DISPLAY=:0
Environment=WAYLAND_DISPLAY=wayland-0
Environment=XDG_RUNTIME_DIR=/run/user/$INSTALL_UID
Environment=DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$INSTALL_UID/bus
ExecStart=/home/$INSTALL_USER/.local/bin/picast-server
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
SYSTEMD

    sudo systemctl daemon-reload
    sudo systemctl enable picast
    sudo systemctl start picast
    log "picast.service enabled and started"
fi

# --- Done ---
HOSTNAME=$(hostname 2>/dev/null || echo "picast")
echo -e "\n${GREEN}"
echo "  ========================================="
echo "  PiCast Installed!"
echo "  ========================================="
echo ""
echo "  Web UI:  http://${HOSTNAME}.local:5050"
echo "  API:     http://${HOSTNAME}.local:5050/api/health"
echo ""
echo "  Quick test:"
echo "    curl http://${HOSTNAME}.local:5050/api/health"
echo ""
echo "  Service:"
echo "    sudo systemctl status picast"
echo "    sudo systemctl restart picast"
echo "    journalctl -u picast -f"
echo ""
echo "  On your Mac:"
echo "    pip install picast"
echo "    picast  # Opens TUI dashboard"
echo -e "${NC}"

# Reboot reminder
if ! groups | grep -q video 2>/dev/null; then
    echo -e "${YELLOW}Reboot recommended for HDMI output: sudo reboot${NC}"
fi
