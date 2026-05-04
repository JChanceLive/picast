"""Player adapter protocol + reference mpv implementation.

Splits the playback subsystem out of the monolithic picast_receiver.py
so it can be reused by other Flask apps (e.g. StarScreen on OPi5+) by
implementing the PlayerAdapter Protocol against a different backend.

The reference implementation MpvPlayer reproduces the v0.8.0 receiver
behavior byte-for-byte: same mpv argv, same Twitch streamlink pipe,
same module-level globals (now instance attrs), same lock semantics.
"""

from __future__ import annotations

import json
import logging
import os
import socket as sock
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Protocol


logger = logging.getLogger("picast-receiver.player")


# --- Config ----------------------------------------------------------------


@dataclass
class ReceiverConfig:
    """Tunable receiver knobs. Defaults match v0.8.0 for picast-z1."""

    mpv_socket: str = "/tmp/picast-receiver-socket"
    hwdec: str = "v4l2m2m-copy"
    mpv_log_path: str = "/tmp/mpv.log"
    # YouTube/VOD ytdl format selector (Pi Zero 2 W friendly)
    ytdl_format: str = (
        "bestvideo[height<=720][vcodec^=avc][fps<=30]+bestaudio"
        "/bestvideo[height<=720][vcodec^=avc]+bestaudio"
        "/best[height<=720][vcodec^=avc]"
        "/best[height<=720]"
        "/best"
    )
    # Twitch streamlink quality fallback chain
    twitch_quality: str = "480p,360p,720p30,720p,160p,best"
    # Wayland env injected into subprocesses
    wayland_env: dict = field(default_factory=lambda: {
        **os.environ,
        "XDG_RUNTIME_DIR": os.environ.get("XDG_RUNTIME_DIR", "/run/user/1000"),
        "WAYLAND_DISPLAY": os.environ.get("WAYLAND_DISPLAY", "wayland-0"),
    })


# --- Adapter protocol -----------------------------------------------------


class PlayerAdapter(Protocol):
    """Interface a Flask Blueprint uses to drive a player.

    Different hosts (picast-z1, StarScreen) provide different
    implementations. The Blueprint never sees mpv directly.
    """

    def play(self, url: str, title: str = "", mute: bool = False) -> bool: ...
    def stop(self) -> None: ...
    def pause(self) -> bool: ...
    def resume(self) -> bool: ...
    def set_volume(self, level: int) -> bool: ...

    def is_idle(self) -> bool: ...
    def status(self) -> dict: ...

    # Used by watchdog
    @property
    def last_url(self) -> str: ...
    @property
    def last_title(self) -> str: ...
    @property
    def last_volume(self) -> int: ...
    @property
    def intentional_stop(self) -> bool: ...
    def streamlink_alive(self) -> bool: ...
    def get_time_pos(self) -> float | None: ...


# --- Reference implementation: mpv subprocess + streamlink pipe ----------


class MpvPlayer:
    """Reference PlayerAdapter — spawns mpv, manages Twitch streamlink pipe.

    Behavior is byte-identical to picast_receiver.py v0.8.0: same flags,
    same lock order, same OSD format. State that used to be module-level
    globals is held on the instance.
    """

    def __init__(self, config: ReceiverConfig | None = None) -> None:
        self._config = config or ReceiverConfig()

        self._lock = threading.Lock()
        self._player_proc: subprocess.Popen | None = None
        self._streamlink_proc: subprocess.Popen | None = None
        self._current_video: dict = {}

        self._last_known_url: str = ""
        self._last_known_title: str = ""
        self._last_known_volume: int = 100
        self._intentional_stop: bool = False
        self._last_stable_since: float = 0.0
        self._last_play_at: float = 0.0  # set on each (re)start; watchdog reads

    # --- Adapter interface implementations ---

    def play(
        self, url: str, title: str = "", mute: bool = False, **_extras,
    ) -> bool:
        # MpvPlayer doesn't consume audio_url/codec — receiver-style
        # devices resolve their own URLs. Extras are accepted and ignored
        # to keep the Blueprint protocol stable across player backends.
        self.stop()
        if "twitch.tv/" in url:
            return self._play_twitch(url, title, mute)
        return self._play_youtube(url, title, mute)

    def stop(self) -> None:
        self._intentional_stop = True
        with self._lock:
            if self._player_proc is not None and self._player_proc.poll() is None:
                self._player_proc.terminate()
                try:
                    self._player_proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self._player_proc.kill()
                self._player_proc = None
            if self._streamlink_proc is not None and self._streamlink_proc.poll() is None:
                self._streamlink_proc.terminate()
                try:
                    self._streamlink_proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self._streamlink_proc.kill()
                self._streamlink_proc = None
            self._current_video = {}

    def pause(self) -> bool:
        return self._mpv_command(["set_property", "pause", True]) is not None

    def resume(self) -> bool:
        return self._mpv_command(["set_property", "pause", False]) is not None

    def set_volume(self, level: int) -> bool:
        level = max(0, min(100, level))
        if self._mpv_command(["set_property", "volume", level]) is not None:
            self._last_known_volume = level
            return True
        return False

    def is_idle(self) -> bool:
        with self._lock:
            if self._player_proc is None:
                return True
            return self._player_proc.poll() is not None

    def status(self) -> dict:
        idle = self.is_idle()
        result = {
            "idle": idle,
            "title": self._current_video.get("title", "") if not idle else "",
            "url": self._current_video.get("url", "") if not idle else "",
        }
        if not idle:
            url = result.get("url", "")
            result["source_type"] = "twitch" if "twitch.tv/" in url else "youtube"
            for prop, key in (
                ("time-pos", "position"),
                ("duration", "duration"),
                ("pause", "paused"),
                ("volume", "volume"),
            ):
                resp = self._mpv_command(["get_property", prop])
                if resp:
                    result[key] = resp.get("data")
        return result

    @property
    def last_url(self) -> str:
        return self._last_known_url

    @property
    def last_title(self) -> str:
        return self._last_known_title

    @property
    def last_volume(self) -> int:
        return self._last_known_volume

    @property
    def intentional_stop(self) -> bool:
        return self._intentional_stop

    def reset_intentional_stop(self) -> None:
        """Watchdog flips this back on after deciding to recover."""
        self._intentional_stop = False

    def streamlink_alive(self) -> bool:
        with self._lock:
            if self._streamlink_proc is None:
                return True  # No pipe required
            return self._streamlink_proc.poll() is None

    def get_time_pos(self) -> float | None:
        resp = self._mpv_command(["get_property", "time-pos"])
        if resp and resp.get("error") == "success":
            return resp.get("data")
        return None

    @property
    def last_stable_since(self) -> float:
        return self._last_stable_since

    # --- Private playback paths (verbatim from v0.8.0) ---

    def _play_youtube(self, url: str, title: str, mute: bool) -> bool:
        cfg = self._config
        cmd = [
            "mpv",
            "--no-terminal",
            "--video-sync=display-desync",
            "--fullscreen",
            f"--input-ipc-server={cfg.mpv_socket}",
            f"--ytdl-format={cfg.ytdl_format}",
            "--demuxer-max-bytes=30M",
            "--demuxer-max-back-bytes=10M",
            "--framedrop=decoder+vo",
            f"--hwdec={cfg.hwdec}",
            "--profile=fast",
            "--initial-audio-sync=no",
            f"--log-file={cfg.mpv_log_path}",
        ]
        if mute:
            cmd.append("--volume=0")
        if title:
            cmd.extend(_osd_args(title))
        cmd.append(url)

        try:
            with self._lock:
                self._player_proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    env=cfg.wayland_env,
                )
                self._current_video = {
                    "url": url, "title": title, "started_at": time.time(),
                }
            self._mark_started(url, title)
            logger.info("Playing: %s (%s)", title or "untitled", url)
            return True
        except FileNotFoundError:
            logger.error("mpv not found — is it installed?")
            return False
        except OSError as e:
            logger.error("Failed to start mpv: %s", e)
            return False

    def _play_twitch(self, url: str, title: str, mute: bool) -> bool:
        cfg = self._config
        sl_cmd = [
            "streamlink",
            "--stdout",
            "--twitch-disable-ads",
            url,
            cfg.twitch_quality,
        ]
        mpv_cmd = [
            "mpv",
            "--no-terminal",
            "--video-sync=display-desync",
            "--fullscreen",
            f"--input-ipc-server={cfg.mpv_socket}",
            f"--hwdec={cfg.hwdec}",
            "--profile=fast",
            "--framedrop=decoder+vo",
            "--demuxer-max-bytes=30M",
            "--demuxer-max-back-bytes=10M",
            "--cache=no",
            f"--log-file={cfg.mpv_log_path}",
        ]
        if mute:
            mpv_cmd.append("--volume=0")
        if title:
            mpv_cmd.extend(_osd_args(title))
        mpv_cmd.append("-")  # stdin

        try:
            with self._lock:
                self._streamlink_proc = subprocess.Popen(
                    sl_cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    env=cfg.wayland_env,
                )
                self._player_proc = subprocess.Popen(
                    mpv_cmd,
                    stdin=self._streamlink_proc.stdout,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    env=cfg.wayland_env,
                )
                # Close streamlink's stdout in parent so SIGPIPE
                # propagates when mpv exits
                self._streamlink_proc.stdout.close()
                self._current_video = {
                    "url": url, "title": title, "started_at": time.time(),
                }
            self._mark_started(url, title)
            logger.info("Playing (twitch): %s (%s)", title or "untitled", url)
            return True
        except FileNotFoundError as e:
            logger.error("streamlink or mpv not found: %s", e)
            return False
        except OSError as e:
            logger.error("Failed to start twitch pipe: %s", e)
            return False

    def _mark_started(self, url: str, title: str) -> None:
        self._last_known_url = url
        self._last_known_title = title
        self._intentional_stop = False
        self._last_stable_since = time.time()
        self._last_play_at = time.time()

    def _mpv_command(self, command: list) -> dict | None:
        """Send a command to mpv via IPC socket; return parsed response."""
        try:
            s = sock.socket(sock.AF_UNIX, sock.SOCK_STREAM)
            s.settimeout(2)
            s.connect(self._config.mpv_socket)
            payload = json.dumps({"command": command}) + "\n"
            s.sendall(payload.encode())
            resp = s.recv(4096)
            s.close()
            return json.loads(resp)
        except (OSError, ConnectionRefusedError, json.JSONDecodeError):
            return None


def _osd_args(title: str) -> list[str]:
    return [
        "--osd-level=3",
        f"--osd-status-msg={title}",
        "--osd-align-x=left",
        "--osd-align-y=bottom",
        "--osd-margin-x=20",
        "--osd-margin-y=20",
    ]
