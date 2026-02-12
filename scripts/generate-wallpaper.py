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
    paths = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
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


def draw_section(draw, x, y, w, title, lines, title_font, body_font, mono_font,
                 label_w=140):
    """Draw a card with title and content lines. Returns card height."""
    pad = 16
    line_h = 22
    title_h = 28

    card_h = pad + title_h + 8 + (len(lines) * line_h) + pad

    # Card background
    draw.rounded_rectangle((x, y, x + w, y + card_h), radius=12, fill=CARD_BG)

    # Title
    draw.text((x + pad, y + pad), title, fill=ACCENT, font=title_font)

    # Lines
    cy = y + pad + title_h + 8
    for line in lines:
        if isinstance(line, tuple):
            label, value = line
            draw.text((x + pad, cy), label, fill=DIM, font=body_font)
            draw.text((x + pad + label_w, cy), value, fill=WHITE, font=mono_font)
        elif line == "":
            pass  # blank spacer
        else:
            draw.text((x + pad, cy), line, fill=WHITE, font=body_font)
        cy += line_h

    return card_h


def main():
    ip = get_ip()
    hostname = get_hostname()

    img = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(img)

    # Fonts - smaller sizes to fit everything
    title_huge = load_bold_font(48)
    section_font = load_bold_font(15)
    body_font = load_font(14)
    mono_font = load_mono_font(13)
    subtitle_font = load_font(16)
    small_font = load_font(12)
    ver_font = load_bold_font(18)

    # --- Header ---
    draw.rectangle((0, 0, WIDTH, 3), fill=ACCENT)

    draw.text((60, 30), "PiCast", fill=WHITE, font=title_huge)
    draw.text((60, 85), "YouTube Queue Player for Raspberry Pi", fill=DIM, font=subtitle_font)

    # Version badge
    draw.rounded_rectangle((260, 40, 365, 68), radius=10, fill=ACCENT)
    draw.text((275, 43), "v0.6.0", fill=BG, font=ver_font)

    # Status indicator
    draw.ellipse((WIDTH - 160, 48, WIDTH - 146, 62), fill=ACCENT2)
    draw.text((WIDTH - 140, 43), "RUNNING", fill=ACCENT2, font=ver_font)

    # --- Column layout: 3 columns ---
    gap = 20
    col_w = (WIDTH - 60 - 60 - gap * 2) // 3  # ~580 each
    col1_x = 60
    col2_x = col1_x + col_w + gap
    col3_x = col2_x + col_w + gap
    top_y = 120

    # ============ COLUMN 1 ============
    cy = top_y

    h = draw_section(draw, col1_x, cy, col_w,
        "WHAT IS PICAST?", [
            "A media queue player that turns your",
            "Raspberry Pi into a dedicated YouTube",
            "and Twitch streaming device.",
            "",
            "Queue videos from any device on your",
            "network. Plays continuously on your TV.",
        ], section_font, body_font, mono_font)
    cy += h + 14

    h = draw_section(draw, col1_x, cy, col_w,
        "WEB UI", [
            ("URL", f"http://{ip}:5050"),
            ("Local", f"http://{hostname}.local:5050"),
            "",
            "Open from any phone, tablet, or",
            "computer on your local network.",
        ], section_font, body_font, mono_font, label_w=80)
    cy += h + 14

    h = draw_section(draw, col1_x, cy, col_w,
        "SSH COMMANDS", [
            ("Status", "sudo systemctl status picast"),
            ("Restart", "sudo systemctl restart picast"),
            ("Logs", "journalctl -u picast -f"),
            ("Update", "picast-update"),
            ("Config", "~/.config/picast/picast.toml"),
            ("Data", "~/.picast/picast.db"),
        ], section_font, body_font, mono_font, label_w=80)
    cy += h + 14

    h = draw_section(draw, col1_x, cy, col_w,
        "NETWORK", [
            ("IP", ip),
            ("Hostname", f"{hostname}.local"),
            ("Port", "5050"),
            ("SSH", f"ssh jopi@{ip}"),
        ], section_font, body_font, mono_font, label_w=100)

    # ============ COLUMN 2 ============
    cy = top_y

    h = draw_section(draw, col2_x, cy, col_w,
        "TECH STACK", [
            ("Server", "Python + Flask"),
            ("Player", "mpv (hardware-accelerated)"),
            ("Database", "SQLite (queue + library)"),
            ("YouTube", "yt-dlp + deno (auto PO token)"),
            ("Service", "systemd (auto-start on boot)"),
            ("Updates", "Daily auto-update from GitHub"),
        ], section_font, body_font, mono_font, label_w=100)
    cy += h + 14

    h = draw_section(draw, col2_x, cy, col_w,
        "HOW IT WORKS", [
            "1. Open the Web UI from any device",
            "2. Paste a YouTube or Twitch URL",
            "3. Videos queue up and auto-play on TV",
            "4. Control playback from the Web UI",
            "",
            "No account needed. No app to install.",
            "Works on any browser on your network.",
        ], section_font, body_font, mono_font)
    cy += h + 14

    h = draw_section(draw, col2_x, cy, col_w,
        "FEATURES", [
            "Queue management (add, reorder, replay)",
            "YouTube, Twitch, and local file support",
            "Playback history and library tracking",
            "Collections (saved playlists)",
            "Sleep timer (stop after current or N min)",
            "Telegram bot remote control (optional)",
            "TUI client for Mac terminal",
            "Multi-device discovery (mDNS)",
        ], section_font, body_font, mono_font)
    cy += h + 14

    h = draw_section(draw, col2_x, cy, col_w,
        "AUTO-UPDATE", [
            "Checks GitHub daily at 4 AM (+jitter).",
            "Also upgrades yt-dlp automatically.",
            ("Manual", "picast-update"),
            ("Log", "~/.picast/update.log"),
        ], section_font, body_font, mono_font, label_w=80)

    # ============ COLUMN 3 ============
    cy = top_y

    h = draw_section(draw, col3_x, cy, col_w,
        "API ENDPOINTS", [
            ("GET", "/api/health"),
            ("GET", "/api/status"),
            ("GET", "/api/queue"),
            ("POST", "/api/queue         {\"url\": \"...\"}"),
            ("POST", "/api/player/toggle"),
            ("POST", "/api/player/skip"),
            ("POST", "/api/player/volume  {\"level\": 80}"),
            ("POST", "/api/player/seek    {\"position\": 120}"),
            ("POST", "/api/player/speed   {\"speed\": 1.5}"),
            ("GET", "/api/library/browse"),
            ("GET", "/api/library/search?q=..."),
            ("GET", "/api/playlists"),
        ], section_font, body_font, mono_font, label_w=60)
    cy += h + 14

    h = draw_section(draw, col3_x, cy, col_w,
        "WEB PAGES", [
            ("Queue", f"http://{ip}:5050/"),
            ("History", f"http://{ip}:5050/history"),
            ("Collections", f"http://{ip}:5050/collections"),
        ], section_font, body_font, mono_font, label_w=110)
    cy += h + 14

    h = draw_section(draw, col3_x, cy, col_w,
        "QUICK START FROM MAC", [
            "pip install picast",
            "picast              # TUI dashboard",
            "picast status       # Check Pi status",
            f"curl http://{ip}:5050/api/health",
        ], section_font, body_font, mono_font)
    cy += h + 14

    h = draw_section(draw, col3_x, cy, col_w,
        "TROUBLESHOOTING", [
            "No video?  Check journalctl -u picast -f",
            "No audio?  Verify HDMI audio in mpv.conf",
            "Blocked?   yt-dlp auto-generates PO tokens",
            "Stale?     Run: picast-update",
            "Crashed?   sudo systemctl restart picast",
        ], section_font, body_font, mono_font)

    # --- Footer ---
    draw.rectangle((0, HEIGHT - 30, WIDTH, HEIGHT), fill=CARD_BG)
    draw.text((60, HEIGHT - 24), "github.com/JChanceLive/picast", fill=DIM, font=small_font)
    draw.text((WIDTH - 300, HEIGHT - 24),
              f"{hostname}  |  {ip}  |  port 5050", fill=DIM, font=small_font)

    # Save
    output = "/home/jopi/.picast/wallpaper.png"
    img.save(output, "PNG", optimize=True)
    print(f"Wallpaper saved to {output}")


if __name__ == "__main__":
    main()
