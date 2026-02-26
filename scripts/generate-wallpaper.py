#!/usr/bin/env python3
"""Generate PiCast desktop wallpaper with system info.

Runs on the Pi at boot via picast-wallpaper.service to keep the
desktop wallpaper current (IP address, version, etc).
"""

from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
import os
import socket
import subprocess

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
        return "check router"


def get_version():
    try:
        from picast.__about__ import __version__
        return __version__
    except Exception:
        return "0.7.0"


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


def load_icon():
    """Try to load icon.png from known locations."""
    candidates = [
        Path(__file__).parent.parent / "assets" / "icon.png",
        Path.home() / ".picast" / "icon.png",
    ]
    for p in candidates:
        if p.exists():
            try:
                return Image.open(p)
            except Exception:
                continue
    return None


def draw_section(draw, x, y, w, title, lines, title_font, body_font, mono_font,
                 label_w=140, line_h=28, pad=20, title_h=34):
    """Draw a card with title and content lines. Returns card height."""
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


def refresh_desktop(wallpaper_path):
    """Tell pcmanfm to reload the wallpaper."""
    display = os.environ.get("DISPLAY", ":0")
    env = {**os.environ, "DISPLAY": display}
    try:
        subprocess.run(
            ["pcmanfm", "--set-wallpaper", wallpaper_path],
            env=env, timeout=5, capture_output=True,
        )
    except Exception:
        pass  # Desktop may not be running (headless/SSH)


def main():
    ip = get_ip()
    hostname = get_hostname()
    version = get_version()

    img = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(img)

    # Fonts - bigger sizes for fewer cards
    title_huge = load_bold_font(52)
    section_font = load_bold_font(18)
    body_font = load_font(17)
    mono_font = load_mono_font(16)
    subtitle_font = load_font(18)
    small_font = load_font(13)
    ver_font = load_bold_font(20)

    line_h = 28
    pad = 20
    title_h = 34

    # --- Header with icon ---
    draw.rectangle((0, 0, WIDTH, 3), fill=ACCENT)

    icon = load_icon()
    icon_offset = 0
    if icon:
        icon_size = 64
        icon_resized = icon.resize((icon_size, icon_size), Image.LANCZOS)
        img.paste(icon_resized, (60, 25), icon_resized if icon_resized.mode == "RGBA" else None)
        icon_offset = icon_size + 16

    draw.text((60 + icon_offset, 28), "PiCast", fill=WHITE, font=title_huge)

    # Version badge (position relative to title)
    ver_text = f"v{version}"
    badge_x = 60 + icon_offset + 250
    draw.rounded_rectangle((badge_x, 38, badge_x + 110, 70), radius=10, fill=ACCENT)
    draw.text((badge_x + 14, 40), ver_text, fill=BG, font=ver_font)

    draw.text((60 + icon_offset, 88), "YouTube Queue Player for Raspberry Pi", fill=DIM, font=subtitle_font)

    # Status indicator
    draw.ellipse((WIDTH - 180, 48, WIDTH - 164, 64), fill=ACCENT2)
    draw.text((WIDTH - 158, 42), "RUNNING", fill=ACCENT2, font=ver_font)

    # --- Column layout: 3 columns ---
    gap = 20
    margin = 60
    col_w = (WIDTH - margin * 2 - gap * 2) // 3
    col1_x = margin
    col2_x = col1_x + col_w + gap
    col3_x = col2_x + col_w + gap
    top_y = 125

    # ============ COLUMN 1: About + Access ============
    cy = top_y

    h = draw_section(draw, col1_x, cy, col_w,
        "WHAT IS PICAST?", [
            "A media queue player that turns your",
            "Raspberry Pi into a dedicated YouTube",
            "and Twitch streaming device.",
            "",
            "Queue videos from any device on your",
            "network. Plays continuously on your TV.",
        ], section_font, body_font, mono_font,
        line_h=line_h, pad=pad, title_h=title_h)
    cy += h + 16

    h = draw_section(draw, col1_x, cy, col_w,
        "WEB UI", [
            ("URL", f"http://{ip}:5050"),
            ("Local", f"http://{hostname}.local:5050"),
            "",
            "Open from any phone, tablet, or",
            "computer on your local network.",
        ], section_font, body_font, mono_font, label_w=90,
        line_h=line_h, pad=pad, title_h=title_h)

    # ============ COLUMN 2: Usage ============
    cy = top_y

    h = draw_section(draw, col2_x, cy, col_w,
        "HOW IT WORKS", [
            "1. Open the Web UI from any device",
            "2. Paste a YouTube or Twitch URL",
            "3. Videos queue up and auto-play on TV",
            "4. Control playback from the Web UI",
            "",
            "No account needed. No app to install.",
            "Works on any browser on your network.",
        ], section_font, body_font, mono_font,
        line_h=line_h, pad=pad, title_h=title_h)
    cy += h + 16

    h = draw_section(draw, col2_x, cy, col_w,
        "FEATURES", [
            "Queue management (add, reorder, replay)",
            "YouTube, Twitch, and local file support",
            "Playback history and library tracking",
            "Collections (saved playlists)",
            "AutoPlay pools (scheduled playback)",
            "Sleep timer and stop-after-current",
            "Archive.org movie discovery",
            "Chrome extension for quick queueing",
        ], section_font, body_font, mono_font,
        line_h=line_h, pad=pad, title_h=title_h)

    # ============ COLUMN 3: Admin ============
    cy = top_y

    h = draw_section(draw, col3_x, cy, col_w,
        "NETWORK", [
            ("IP", ip),
            ("Hostname", f"{hostname}.local"),
            ("Port", "5050"),
            ("SSH", f"ssh {os.getlogin()}@{ip}"),
        ], section_font, body_font, mono_font, label_w=110,
        line_h=line_h, pad=pad, title_h=title_h)
    cy += h + 16

    h = draw_section(draw, col3_x, cy, col_w,
        "TROUBLESHOOTING", [
            "No video?  Check journalctl -u picast -f",
            "No audio?  Verify HDMI audio in mpv.conf",
            "Blocked?   yt-dlp auto-generates PO tokens",
            "Stale?     Run: picast-update",
            "Crashed?   sudo systemctl restart picast",
        ], section_font, body_font, mono_font,
        line_h=line_h, pad=pad, title_h=title_h)
    cy += h + 16

    h = draw_section(draw, col3_x, cy, col_w,
        "AUTO-UPDATE", [
            "Checks GitHub daily at 4 AM (+jitter).",
            "Also upgrades yt-dlp automatically.",
            ("Manual", "picast-update"),
            ("Log", "~/.picast/update.log"),
        ], section_font, body_font, mono_font, label_w=90,
        line_h=line_h, pad=pad, title_h=title_h)

    # --- Footer ---
    draw.rectangle((0, HEIGHT - 32, WIDTH, HEIGHT), fill=CARD_BG)
    draw.text((60, HEIGHT - 26), "github.com/JChanceLive/picast", fill=DIM, font=small_font)
    draw.text((WIDTH - 320, HEIGHT - 26),
              f"{hostname}  |  {ip}  |  port 5050", fill=DIM, font=small_font)

    # Save
    output_dir = Path.home() / ".picast"
    output_dir.mkdir(exist_ok=True)
    output = str(output_dir / "wallpaper.png")
    img.save(output, "PNG", optimize=True)
    print(f"Wallpaper saved to {output}")

    refresh_desktop(output)


if __name__ == "__main__":
    main()
