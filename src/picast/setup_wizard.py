"""Interactive setup wizard for PiCast first-run configuration.

Guides the user through optional configuration steps:
1. Pushover push notifications (validates tokens via API)
2. YouTube authentication (detects Chromium cookies)
3. PiPulse integration (tests connection, imports block metadata)

Designed to be idempotent — running multiple times updates existing config.
"""

import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

# Default config location on Pi
CONFIG_DIR = Path.home() / ".config" / "picast"
CONFIG_FILE = CONFIG_DIR / "picast.toml"

# Chromium cookie locations on Raspberry Pi OS
CHROMIUM_COOKIE_PATHS = [
    Path.home() / ".config" / "chromium" / "Default" / "Cookies",
    Path.home() / ".config" / "chromium" / "Profile 1" / "Cookies",
    Path.home() / "snap" / "chromium" / "common" / "chromium" / "Default" / "Cookies",
]


def _print_header(text: str):
    """Print a section header."""
    print(f"\n{'=' * 40}")
    print(f"  {text}")
    print(f"{'=' * 40}\n")


def _print_step(num: int, title: str, description: str):
    """Print a step header with description."""
    print(f"\n--- Step {num}: {title} ---")
    print(f"    {description}\n")


def _prompt(label: str, default: str = "") -> str:
    """Prompt for user input with optional default."""
    if default:
        raw = input(f"  {label} [{default}]: ").strip()
        return raw if raw else default
    return input(f"  {label}: ").strip()


def _prompt_yn(question: str, default: bool = True) -> bool:
    """Yes/no prompt with default."""
    suffix = "[Y/n]" if default else "[y/N]"
    raw = input(f"  {question} {suffix}: ").strip().lower()
    if not raw:
        return default
    return raw in ("y", "yes")


def _load_toml(path: Path) -> dict:
    """Load existing TOML config, returning empty dict if missing."""
    if not path.exists():
        return {}
    try:
        import tomllib
    except ImportError:
        try:
            import tomli as tomllib
        except ImportError:
            # Fallback: basic TOML parsing not available, start fresh
            print("  Warning: TOML parser not available, starting with fresh config")
            return {}
    with open(path, "rb") as f:
        return tomllib.load(f)


def _write_toml(path: Path, data: dict):
    """Write config dict as TOML.

    Uses a simple serializer since tomli/tomllib are read-only.
    Preserves comments where possible by doing targeted section updates.
    """
    path.parent.mkdir(parents=True, exist_ok=True)

    lines = []
    lines.append("# PiCast Configuration")
    lines.append("# Generated/updated by picast-setup")
    lines.append("")

    for section, values in data.items():
        if isinstance(values, dict):
            # Check for nested tables (e.g., autoplay.themes.focus)
            simple_vals = {}
            nested = {}
            for k, v in values.items():
                if isinstance(v, dict):
                    nested[k] = v
                else:
                    simple_vals[k] = v

            if simple_vals:
                lines.append(f"[{section}]")
                for k, v in simple_vals.items():
                    lines.append(f"{k} = {_toml_value(v)}")
                lines.append("")

            for sub_name, sub_vals in nested.items():
                if isinstance(sub_vals, dict):
                    # Could be double-nested (autoplay.themes.focus)
                    has_sub_dict = any(isinstance(sv, dict) for sv in sub_vals.values())
                    if has_sub_dict:
                        simple_sub = {sk: sv for sk, sv in sub_vals.items()
                                      if not isinstance(sv, dict)}
                        if simple_sub:
                            lines.append(f"[{section}.{sub_name}]")
                            for sk, sv in simple_sub.items():
                                lines.append(f"{sk} = {_toml_value(sv)}")
                            lines.append("")
                        for ssn, ssv in sub_vals.items():
                            if isinstance(ssv, dict):
                                lines.append(f"[{section}.{sub_name}.{ssn}]")
                                for ssk, ssval in ssv.items():
                                    lines.append(f"{ssk} = {_toml_value(ssval)}")
                                lines.append("")
                    else:
                        lines.append(f"[{section}.{sub_name}]")
                        for sk, sv in sub_vals.items():
                            lines.append(f"{sk} = {_toml_value(sv)}")
                        lines.append("")
        else:
            # Top-level key (unusual for picast.toml but handle it)
            lines.append(f"{section} = {_toml_value(values)}")

    with open(path, "w") as f:
        f.write("\n".join(lines) + "\n")


def _toml_value(val) -> str:
    """Convert a Python value to TOML representation."""
    if isinstance(val, bool):
        return "true" if val else "false"
    if isinstance(val, int):
        return str(val)
    if isinstance(val, float):
        return str(val)
    if isinstance(val, str):
        return f'"{val}"'
    if isinstance(val, list):
        items = ", ".join(_toml_value(v) for v in val)
        return f"[{items}]"
    return f'"{val}"'


def _merge_section(config: dict, section: str, updates: dict):
    """Merge updates into a config section, preserving existing values."""
    if section not in config:
        config[section] = {}
    config[section].update(updates)


def validate_pushover(api_token: str, user_key: str) -> tuple[bool, str]:
    """Validate Pushover credentials by sending a test notification.

    Returns (success, message).
    """
    data = urllib.parse.urlencode({
        "token": api_token,
        "user": user_key,
        "message": "PiCast setup test - notifications working!",
        "title": "PiCast",
        "priority": 0,
        "sound": "pushover",
    }).encode()

    try:
        req = urllib.request.Request(
            "https://api.pushover.net/1/messages.json",
            data=data,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
            if result.get("status") == 1:
                return True, "Notification sent! Check your phone."
            return False, f"API returned status {result.get('status')}: {result}"
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        try:
            err = json.loads(body)
            return False, f"Invalid credentials: {err.get('errors', body)}"
        except json.JSONDecodeError:
            return False, f"HTTP {e.code}: {body[:200]}"
    except urllib.error.URLError as e:
        return False, f"Connection failed: {e.reason}"
    except Exception as e:
        return False, f"Error: {e}"


# Import urlencode at module level
import urllib.parse


def detect_chromium_cookies() -> str | None:
    """Check if Chromium cookies exist on this system.

    Returns the browser name suitable for yt-dlp --cookies-from-browser,
    or None if no cookies found.
    """
    for cookie_path in CHROMIUM_COOKIE_PATHS:
        if cookie_path.exists():
            return "chromium"
    return None


def check_pipulse_connection(host: str, port: int) -> tuple[bool, str]:
    """Test connection to PiPulse API.

    Returns (success, message).
    """
    url = f"http://{host}:{port}/api/health"
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
            version = data.get("version", "unknown")
            return True, f"Connected! PiPulse v{version}"
    except urllib.error.URLError as e:
        return False, f"Could not connect to {host}:{port} - {e.reason}"
    except Exception as e:
        return False, f"Error: {e}"


def fetch_pipulse_blocks(host: str, port: int) -> tuple[bool, dict | str]:
    """Fetch block metadata from PiPulse.

    Returns (success, blocks_dict_or_error_message).
    """
    url = f"http://{host}:{port}/api/pitim/blocks"
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            blocks = data.get("blocks", data)
            return True, blocks
    except urllib.error.URLError as e:
        return False, f"Could not fetch blocks: {e.reason}"
    except Exception as e:
        return False, f"Error: {e}"


def import_blocks_to_picast(blocks: dict, server_url: str = "http://localhost:5050"):
    """Import block metadata into PiCast via its API."""
    imported = 0
    for block_name, meta in blocks.items():
        data = {
            "block_name": block_name,
            "display_name": meta.get("display_name", block_name),
            "emoji": meta.get("emoji", ""),
            "block_start": meta.get("block_start", ""),
            "block_end": meta.get("block_end", ""),
            "tagline": meta.get("tagline", ""),
            "block_type": meta.get("block_type", ""),
            "energy": meta.get("energy", ""),
        }
        body = json.dumps(data).encode()
        try:
            req = urllib.request.Request(
                f"{server_url}/api/settings/blocks",
                data=body,
                method="POST",
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                result = json.loads(resp.read())
                if result.get("ok"):
                    imported += 1
        except Exception:
            pass
    return imported


def run_wizard(config_path: str | None = None):
    """Run the interactive setup wizard."""
    path = Path(config_path) if config_path else CONFIG_FILE

    _print_header("PiCast Setup Wizard")
    print(f"  Config file: {path}")
    if path.exists():
        print("  (Existing config found — will update in place)")
    else:
        print("  (No config found — will create new)")

    config = _load_toml(path)

    # Ensure server section exists with defaults
    if "server" not in config:
        config["server"] = {"host": "0.0.0.0", "port": 5050}

    changes_made = False

    # --- Step 1: Pushover ---
    _print_step(1, "Pushover Notifications", "Push notifications for SD card alerts and daily summaries.")
    print("  Get tokens at: https://pushover.net/apps/build")

    existing_po = config.get("pushover", {})
    if existing_po.get("enabled") and existing_po.get("api_token"):
        print(f"  Currently configured: api_token=...{existing_po['api_token'][-4:]}")
        if not _prompt_yn("Reconfigure Pushover?", default=False):
            print("  Skipping (keeping existing config)")
        else:
            changes_made |= _setup_pushover(config)
    elif _prompt_yn("Set up Pushover notifications?"):
        changes_made |= _setup_pushover(config)
    else:
        print("  Skipped. You can run picast-setup again later.")

    # --- Step 2: YouTube Auth ---
    _print_step(2, "YouTube Authentication", "Required for age-restricted and some region-locked videos.")

    existing_cookies = config.get("server", {}).get("ytdl_cookies_from_browser", "")
    if existing_cookies:
        print(f"  Currently configured: cookies from '{existing_cookies}'")
        if not _prompt_yn("Reconfigure YouTube auth?", default=False):
            print("  Skipping (keeping existing config)")
        else:
            changes_made |= _setup_youtube(config)
    elif _prompt_yn("Set up YouTube authentication?"):
        changes_made |= _setup_youtube(config)
    else:
        print("  Skipped. Videos may fail for age-restricted content.")

    # --- Step 3: PiPulse ---
    _print_step(3, "PiPulse Integration", "Rich autoplay block metadata from your PiPulse instance.")

    existing_pp = config.get("pipulse", {})
    if existing_pp.get("enabled"):
        host = existing_pp.get("host", "10.0.0.103")
        port = existing_pp.get("port", 5055)
        print(f"  Currently configured: {host}:{port}")
        if not _prompt_yn("Reconfigure PiPulse?", default=False):
            print("  Skipping (keeping existing config)")
        else:
            changes_made |= _setup_pipulse(config)
    elif _prompt_yn("Set up PiPulse integration?"):
        changes_made |= _setup_pipulse(config)
    else:
        print("  Skipped. Block metadata can be managed via the web UI.")

    # --- Save ---
    if changes_made:
        print(f"\n  Saving config to {path}...")
        _write_toml(path, config)
        print(f"  Saved!")
        print(f"\n  Restart PiCast to apply changes:")
        print(f"    sudo systemctl restart picast")
    else:
        print("\n  No changes made.")

    _print_header("Setup Complete")
    print(f"  Web UI: http://picast.local:{config.get('server', {}).get('port', 5050)}")
    print(f"  Config: {path}")
    print()


def _setup_pushover(config: dict) -> bool:
    """Interactive Pushover setup. Returns True if config was updated."""
    api_token = _prompt("API Token")
    if not api_token:
        print("  No token entered, skipping.")
        return False

    user_key = _prompt("User Key")
    if not user_key:
        print("  No user key entered, skipping.")
        return False

    print("  Testing...")
    ok, msg = validate_pushover(api_token, user_key)
    if ok:
        print(f"  OK: {msg}")
        _merge_section(config, "pushover", {
            "enabled": True,
            "api_token": api_token,
            "user_key": user_key,
            "daily_summary_hour": config.get("pushover", {}).get("daily_summary_hour", 8),
        })
        return True
    else:
        print(f"  FAILED: {msg}")
        if _prompt_yn("Save tokens anyway (fix later)?", default=False):
            _merge_section(config, "pushover", {
                "enabled": False,
                "api_token": api_token,
                "user_key": user_key,
            })
            return True
        return False


def _setup_youtube(config: dict) -> bool:
    """Interactive YouTube auth setup. Returns True if config was updated."""
    browser = detect_chromium_cookies()
    if browser:
        print(f"  Detected: {browser} cookies available")
        if _prompt_yn("Use Chromium cookies for YouTube auth?"):
            _merge_section(config, "server", {
                **{k: v for k, v in config.get("server", {}).items()},
                "ytdl_cookies_from_browser": browser,
            })
            print(f"  Saved cookie config.")
            return True
    else:
        print("  No Chromium cookies detected.")
        print()
        print("  To set up YouTube authentication:")
        print("    1. Open Chromium on this Pi:")
        print("       chromium-browser youtube.com")
        print("    2. Sign into your YouTube/Google account")
        print("    3. Close Chromium")
        print("    4. Run picast-setup again")
        print()
        if _prompt_yn("Or enter a PO token instead (for headless setups)?", default=False):
            po_token = _prompt("PO Token")
            if po_token:
                _merge_section(config, "server", {
                    **{k: v for k, v in config.get("server", {}).items()},
                    "ytdl_po_token": po_token,
                })
                print("  Saved PO token config.")
                return True
    return False


def _setup_pipulse(config: dict) -> bool:
    """Interactive PiPulse setup. Returns True if config was updated."""
    default_host = config.get("pipulse", {}).get("host", "10.0.0.103")
    default_port = str(config.get("pipulse", {}).get("port", 5055))

    host = _prompt("PiPulse Host", default=default_host)
    port_str = _prompt("PiPulse Port", default=default_port)
    try:
        port = int(port_str)
    except ValueError:
        print(f"  Invalid port: {port_str}")
        return False

    print("  Testing connection...")
    ok, msg = check_pipulse_connection(host, port)
    if ok:
        print(f"  OK: {msg}")
        _merge_section(config, "pipulse", {
            "enabled": True,
            "host": host,
            "port": port,
        })

        # Offer to import block metadata
        if _prompt_yn("Import block metadata from PiPulse?"):
            print("  Fetching blocks...")
            bok, blocks = fetch_pipulse_blocks(host, port)
            if bok and isinstance(blocks, dict):
                picast_port = config.get("server", {}).get("port", 5050)
                count = import_blocks_to_picast(blocks, f"http://localhost:{picast_port}")
                print(f"  Imported {count}/{len(blocks)} blocks to local database.")
            else:
                print(f"  Could not fetch blocks: {blocks}")
                print("  You can import later via the web UI settings page.")
        return True
    else:
        print(f"  FAILED: {msg}")
        if _prompt_yn("Save config anyway (test later)?", default=False):
            _merge_section(config, "pipulse", {
                "enabled": False,
                "host": host,
                "port": port,
            })
            return True
        return False
