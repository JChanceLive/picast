#!/usr/bin/env python3
"""Generate PiCast desktop wallpaper with system info."""

from PIL import Image, ImageDraw, ImageFont
import socket

WIDTH, HEIGHT = 1920, 1080

# Colors
BG = (18, 18, 24)
CARD_BG = (28, 28, 38)
ACCENT = (99, 179, 237)    # Blue
ACCENT2 = (129, 230, 169)  # Green
WHITE = (240, 240, 245)
DIM = (140, 140, 160)
ORANGE = (251, 191, 36)

def get_hostname():
    try:
        return socket.gethostname()
    except Exception:
        return "picast"

def get_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "10.0.0.25"

def load_font(size):
    """Try to load a good font, fall back to default."""
    paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ]
    for p in paths:
        try:
            return ImageFont.truetype(p, size)
        except (IOError, OSError):
            continue
    return ImageFont.load_default()

def load_bold_font(size):
    paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    ]
    for p in paths:
        try:
            return ImageFont.truetype(p, size)
        except (IOError, OSError):
            continue
    return load_font(size)

def load_mono_font(size):
    paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansMono-Regular.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
    ]
    for p in paths:
        try:
            return ImageFont.truetype(p, size)
        except (IOError, OSError):
            continue
    return load_font(size)

def rounded_rect(draw, xy, radius, fill):
    x0, y0, x1, y1 = xy
    draw.rounded_rectangle(xy, radius=radius, fill=fill)

def draw_section(draw, x, y, w, title, lines, fonts):
    """Draw a card section with title and content lines."""
    title_font, body_font, mono_font = fonts
    padding = 24
    line_height = 32

    # Calculate card height
    card_h = padding + 40 + (len(lines) * line_height) + padding

    # Card background
    rounded_rect(draw, (x, y, x + w, y + card_h), 16, CARD_BG)

    # Title
    draw.text((x + padding, y + padding), title, fill=ACCENT, font=title_font)

    # Lines
    cy = y + padding + 44
    for line in lines:
        if isinstance(line, tuple):
            # (label, value) pair
            label, value = line
            draw.text((x + padding, cy), label, fill=DIM, font=body_font)
            draw.text((x + padding + 220, cy), value, fill=WHITE, font=mono_font)
        else:
            draw.text((x + padding, cy), line, fill=WHITE, font=body_font)
        cy += line_height

    return card_h

def main():
    ip = get_ip()
    hostname = get_hostname()

    img = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(img)

    # Fonts
    title_huge = load_bold_font(64)
    title_font = load_bold_font(24)
    body_font = load_font(20)
    mono_font = load_mono_font(20)
    subtitle_font = load_font(22)
    small_font = load_font(16)
    fonts = (title_font, body_font, mono_font)

    # --- Header ---
    # Decorative top bar
    draw.rectangle((0, 0, WIDTH, 4), fill=ACCENT)

    # Title
    draw.text((80, 50), "PiCast", fill=WHITE, font=title_huge)
    draw.text((80, 125), "YouTube Queue Player for Raspberry Pi", fill=DIM, font=subtitle_font)

    # Version badge
    ver_text = "v0.6.0"
    draw.rounded_rectangle((340, 62, 460, 98), radius=12, fill=ACCENT)
    draw.text((358, 66), ver_text, fill=BG, font=title_font)

    # Status indicator
    draw.ellipse((WIDTH - 200, 70, WIDTH - 184, 86), fill=ACCENT2)
    draw.text((WIDTH - 175, 65), "RUNNING", fill=ACCENT2, font=title_font)

    # --- Left Column ---
    left_x = 80
    col_w = 520
    cy = 190

    # What is PiCast?
    h = draw_section(draw, left_x, cy, col_w, "WHAT IS PICAST?", [
        "A media queue player that turns",
        "your Raspberry Pi into a dedicated",
        "YouTube/Twitch streaming device.",
        "",
        "Queue videos from any device,",
        "plays continuously on your TV.",
    ], fonts)
    cy += h + 24

    # Web UI
    h = draw_section(draw, left_x, cy, col_w, "WEB UI", [
        ("URL", f"http://{ip}:5050"),
        ("Local", f"http://{hostname}.local:5050"),
        "",
        "Open from any phone, tablet,",
        "or computer on your network.",
    ], fonts)
    cy += h + 24

    # Quick Commands
    h = draw_section(draw, left_x, cy, col_w, "QUICK COMMANDS (SSH)", [
        ("Status", "sudo systemctl status picast"),
        ("Restart", "sudo systemctl restart picast"),
        ("Logs", "journalctl -u picast -f"),
        ("Update", "picast-update"),
        ("Config", "~/.config/picast/picast.toml"),
    ], fonts)

    # --- Right Column ---
    right_x = 660
    col_w = 520
    cy = 190

    # Tech Stack
    h = draw_section(draw, right_x, cy, col_w, "TECH STACK", [
        ("Server", "Python + Flask"),
        ("Player", "mpv (hardware-accelerated)"),
        ("Database", "SQLite (queue + library)"),
        ("YouTube", "yt-dlp + deno (auto PO token)"),
        ("Service", "systemd (auto-start on boot)"),
        ("Updates", "Daily auto-update from GitHub"),
    ], fonts)
    cy += h + 24

    # How It Works
    h = draw_section(draw, right_x, cy, col_w, "HOW IT WORKS", [
        "1. Open Web UI from any device",
        "2. Paste YouTube/Twitch URLs",
        "3. Videos queue and auto-play",
        "4. Control playback from Web UI",
        "",
        "No account needed. No app install.",
        "Works on any browser.",
    ], fonts)
    cy += h + 24

    # Network Info
    h = draw_section(draw, right_x, cy, col_w, "NETWORK", [
        ("IP Address", ip),
        ("Hostname", f"{hostname}.local"),
        ("API Port", "5050"),
        ("SSH", f"ssh jopi@{ip}"),
    ], fonts)

    # --- Far Right Column ---
    far_right_x = 1240
    col_w = 600
    cy = 190

    # API Endpoints
    h = draw_section(draw, far_right_x, cy, col_w, "API ENDPOINTS", [
        ("Health", f"GET  /api/health"),
        ("Status", f"GET  /api/status"),
        ("Queue", f"GET  /api/queue"),
        ("Add", f"POST /api/queue  {'{'}\"url\": \"...\"{'}'}"),
        ("Play/Pause", f"POST /api/player/toggle"),
        ("Skip", f"POST /api/player/skip"),
        ("Volume", f"POST /api/player/volume"),
    ], fonts)
    cy += h + 24

    # Features
    h = draw_section(draw, far_right_x, cy, col_w, "FEATURES", [
        "Queue management (add, reorder, replay)",
        "YouTube, Twitch, and local files",
        "Playback history + library",
        "Collections (saved playlists)",
        "Sleep timer (stop after current/mins)",
        "Telegram bot (optional)",
        "TUI client for Mac terminal",
    ], fonts)
    cy += h + 24

    # Auto-Update
    h = draw_section(draw, far_right_x, cy, col_w, "AUTO-UPDATE", [
        "Checks GitHub daily at 4 AM.",
        "Also upgrades yt-dlp automatically.",
        ("Manual", "picast-update"),
        ("Log", "~/.picast/update.log"),
    ], fonts)

    # --- Footer ---
    draw.rectangle((0, HEIGHT - 40, WIDTH, HEIGHT), fill=CARD_BG)
    draw.text((80, HEIGHT - 34), f"github.com/JChanceLive/picast", fill=DIM, font=small_font)
    draw.text((WIDTH - 400, HEIGHT - 34), f"Generated by PiCast install  |  {hostname}  |  {ip}", fill=DIM, font=small_font)

    # Save
    output = "/home/jopi/.picast/wallpaper.png"
    img.save(output, "PNG")
    print(f"Wallpaper saved to {output}")

if __name__ == "__main__":
    main()
