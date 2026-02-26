"""Desktop wallpaper generator for PiCast.

Thin wrapper around scripts/generate-wallpaper.py logic.
Called on server startup to keep the version badge current.
Pillow is required but failures are silently ignored (wallpaper is cosmetic).
"""

import logging
import os
import socket
from pathlib import Path

logger = logging.getLogger(__name__)


def generate_wallpaper():
    """Generate the PiCast desktop wallpaper and apply it."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        logger.debug("Pillow not installed, skipping wallpaper generation")
        return

    WIDTH, HEIGHT = 1920, 1080

    # Colors
    BG = (18, 18, 24)
    CARD_BG = (28, 28, 38)
    ACCENT = (99, 179, 237)
    ACCENT2 = (129, 230, 169)
    WHITE = (240, 240, 245)
    DIM = (140, 140, 160)

    def _get_ip():
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except Exception:
            return "check router"

    def _get_version():
        try:
            from picast.__about__ import __version__
            return __version__
        except Exception:
            return "?.?.?"

    def _load_font(size, bold=False, mono=False):
        if mono:
            names = ["DejaVuSansMono", "NotoSansMono-Regular", "LiberationMono-Regular"]
        elif bold:
            names = ["DejaVuSans-Bold", "NotoSans-Bold", "LiberationSans-Bold"]
        else:
            names = ["DejaVuSans", "NotoSans-Regular", "LiberationSans-Regular"]
        dirs = ["/usr/share/fonts/truetype/dejavu", "/usr/share/fonts/truetype/noto",
                "/usr/share/fonts/truetype/liberation"]
        for d, n in zip(dirs, names):
            try:
                return ImageFont.truetype(f"{d}/{n}.ttf", size)
            except (IOError, OSError):
                continue
        return ImageFont.load_default()

    def _load_icon():
        for p in [Path(__file__).parent.parent.parent / "assets" / "icon.png",
                   Path.home() / ".picast" / "icon.png"]:
            if p.exists():
                try:
                    return Image.open(p)
                except Exception:
                    continue
        return None

    def _draw_section(draw, x, y, w, title, lines, title_font, body_font, mono_font,
                      label_w=140, line_h=28, pad=20, title_h=34):
        card_h = pad + title_h + 8 + (len(lines) * line_h) + pad
        draw.rounded_rectangle((x, y, x + w, y + card_h), radius=12, fill=CARD_BG)
        draw.text((x + pad, y + pad), title, fill=ACCENT, font=title_font)
        cy = y + pad + title_h + 8
        for line in lines:
            if isinstance(line, tuple):
                label, value = line
                draw.text((x + pad, cy), label, fill=DIM, font=body_font)
                draw.text((x + pad + label_w, cy), value, fill=WHITE, font=mono_font)
            elif line == "":
                pass
            else:
                draw.text((x + pad, cy), line, fill=WHITE, font=body_font)
            cy += line_h
        return card_h

    ip = _get_ip()
    hostname = socket.gethostname()
    version = _get_version()

    img = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(img)

    section_font = _load_font(18, bold=True)
    body_font = _load_font(17)
    mono_font = _load_font(16, mono=True)
    title_huge = _load_font(52, bold=True)
    subtitle_font = _load_font(18)
    small_font = _load_font(13)
    ver_font = _load_font(20, bold=True)

    line_h, pad, title_h = 28, 20, 34

    # Header
    draw.rectangle((0, 0, WIDTH, 3), fill=ACCENT)
    icon = _load_icon()
    icon_offset = 0
    if icon:
        icon_resized = icon.resize((64, 64), Image.LANCZOS)
        img.paste(icon_resized, (60, 25), icon_resized if icon_resized.mode == "RGBA" else None)
        icon_offset = 80

    draw.text((60 + icon_offset, 28), "PiCast", fill=WHITE, font=title_huge)
    badge_x = 60 + icon_offset + 250
    draw.rounded_rectangle((badge_x, 38, badge_x + 110, 70), radius=10, fill=ACCENT)
    draw.text((badge_x + 14, 40), f"v{version}", fill=BG, font=ver_font)
    draw.text((60 + icon_offset, 88), "YouTube Queue Player for Raspberry Pi", fill=DIM, font=subtitle_font)
    draw.ellipse((WIDTH - 180, 48, WIDTH - 164, 64), fill=ACCENT2)
    draw.text((WIDTH - 158, 42), "RUNNING", fill=ACCENT2, font=ver_font)

    # 3-column layout
    gap, margin = 20, 60
    col_w = (WIDTH - margin * 2 - gap * 2) // 3
    col1_x = margin
    col2_x = col1_x + col_w + gap
    col3_x = col2_x + col_w + gap
    top_y = 125
    kw = dict(line_h=line_h, pad=pad, title_h=title_h)

    # Column 1
    cy = top_y
    h = _draw_section(draw, col1_x, cy, col_w, "WHAT IS PICAST?", [
        "A media queue player that turns your",
        "Raspberry Pi into a dedicated YouTube",
        "and Twitch streaming device.", "",
        "Queue videos from any device on your",
        "network. Plays continuously on your TV.",
    ], section_font, body_font, mono_font, **kw)
    cy += h + 16
    _draw_section(draw, col1_x, cy, col_w, "WEB UI", [
        ("URL", f"http://{ip}:5050"),
        ("Local", f"http://{hostname}.local:5050"), "",
        "Open from any phone, tablet, or",
        "computer on your local network.",
    ], section_font, body_font, mono_font, label_w=90, **kw)

    # Column 2
    cy = top_y
    h = _draw_section(draw, col2_x, cy, col_w, "HOW IT WORKS", [
        "1. Open the Web UI from any device",
        "2. Paste a YouTube or Twitch URL",
        "3. Videos queue up and auto-play on TV",
        "4. Control playback from the Web UI", "",
        "No account needed. No app to install.",
        "Works on any browser on your network.",
    ], section_font, body_font, mono_font, **kw)
    cy += h + 16
    _draw_section(draw, col2_x, cy, col_w, "FEATURES", [
        "Queue management (add, reorder, replay)",
        "YouTube, Twitch, and local file support",
        "Playback history and library tracking",
        "Collections (saved playlists)",
        "AutoPlay pools (scheduled playback)",
        "Sleep timer and stop-after-current",
        "Archive.org movie discovery",
        "Chrome extension for quick queueing",
    ], section_font, body_font, mono_font, **kw)

    # Column 3
    cy = top_y
    h = _draw_section(draw, col3_x, cy, col_w, "NETWORK", [
        ("IP", ip),
        ("Hostname", f"{hostname}.local"),
        ("Port", "5050"),
        ("SSH", f"ssh {os.getlogin()}@{ip}"),
    ], section_font, body_font, mono_font, label_w=110, **kw)
    cy += h + 16
    h = _draw_section(draw, col3_x, cy, col_w, "TROUBLESHOOTING", [
        "No video?  Check journalctl -u picast -f",
        "No audio?  Verify HDMI audio in mpv.conf",
        "Blocked?   yt-dlp auto-generates PO tokens",
        "Stale?     Run: picast-update",
        "Crashed?   sudo systemctl restart picast",
    ], section_font, body_font, mono_font, **kw)
    cy += h + 16
    _draw_section(draw, col3_x, cy, col_w, "AUTO-UPDATE", [
        "Checks GitHub daily at 4 AM (+jitter).",
        "Also upgrades yt-dlp automatically.",
        ("Manual", "picast-update"),
        ("Log", "~/.picast/update.log"),
    ], section_font, body_font, mono_font, label_w=90, **kw)

    # Footer
    draw.rectangle((0, HEIGHT - 32, WIDTH, HEIGHT), fill=CARD_BG)
    draw.text((60, HEIGHT - 26), "github.com/JChanceLive/picast", fill=DIM, font=small_font)
    draw.text((WIDTH - 320, HEIGHT - 26),
              f"{hostname}  |  {ip}  |  port 5050", fill=DIM, font=small_font)

    # Save and apply
    output_dir = Path.home() / ".picast"
    output_dir.mkdir(exist_ok=True)
    output = str(output_dir / "wallpaper.png")
    img.save(output, "PNG", optimize=True)
    logger.info("Wallpaper saved to %s", output)

    # Apply via pcmanfm (if desktop is running)
    import subprocess
    display = os.environ.get("DISPLAY", ":0")
    env = {**os.environ, "DISPLAY": display}
    try:
        subprocess.run(["pcmanfm", "--set-wallpaper", output],
                       env=env, timeout=5, capture_output=True)
    except Exception:
        pass
