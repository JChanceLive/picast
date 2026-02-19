"""Player logic - processes queue and manages mpv playback.

This replaces player.sh from the bash version. Runs as a background thread
that watches the queue and plays the next item when mpv becomes idle.

Includes cascade protection, Wayland auto-detection, and HDMI audio routing
ported from the original bash player.
"""

from __future__ import annotations

import logging
import os
import shutil
import signal
import subprocess
import threading
import time
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlparse

from picast.server.mpv_client import MPVClient
from picast.server.queue_manager import QueueItem, QueueManager

if TYPE_CHECKING:
    from picast.config import ServerConfig
    from picast.server.events import EventBus

# Optional library import - player works without it
try:
    from picast.server.library import Library
except ImportError:
    Library = None

logger = logging.getLogger(__name__)

# Cascade protection thresholds
MIN_PLAY_SECONDS = 5       # Under this = "didn't really play"
MAX_RAPID_FAILURES = 3     # After this many rapid failures, mark failed
FAILURE_BACKOFF = [1, 5, 30]  # Exponential backoff delays per retry attempt


def detect_wayland() -> str | None:
    """Auto-detect Wayland display socket.

    Checks XDG_RUNTIME_DIR for wayland-* sockets. Returns the socket name
    (e.g. 'wayland-0') or None if not found.
    """
    if os.environ.get("WAYLAND_DISPLAY"):
        return os.environ["WAYLAND_DISPLAY"]

    uid = os.getuid()
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{uid}")

    try:
        for entry in os.listdir(runtime_dir):
            if entry.startswith("wayland-") and os.path.exists(os.path.join(runtime_dir, entry)):
                sock_path = os.path.join(runtime_dir, entry)
                if os.path.exists(sock_path):
                    logger.info("Auto-detected Wayland: %s", entry)
                    return entry
    except (FileNotFoundError, PermissionError):
        pass

    logger.debug("No Wayland display found")
    return None


def detect_hdmi_audio() -> str | None:
    """Detect HDMI audio device for ALSA direct output.

    Returns an ALSA device string like 'alsa/hdmi:CARD=vc4hdmi,DEV=0' if
    found, otherwise None (mpv will use its default audio output).
    """
    try:
        # Check if the vc4hdmi ALSA card exists
        result = subprocess.run(
            ["aplay", "-l"],
            capture_output=True, text=True, timeout=5,
        )
        if "vc4hdmi" in result.stdout:
            logger.info("Detected HDMI audio: vc4hdmi")
            return "alsa/hdmi:CARD=vc4hdmi,DEV=0"
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass
    return None


class Player:
    """Manages mpv playback and queue processing.

    The player runs a background thread that:
    1. Checks if mpv is idle
    2. Grabs the next pending item from the queue
    3. Resolves the URL (yt-dlp for YouTube)
    4. Launches mpv with the resolved URL
    5. Monitors until playback ends
    6. Marks the item as played
    7. Loops
    """

    def __init__(
        self,
        mpv: MPVClient,
        queue: QueueManager,
        ytdl_format: str = (
            "bestvideo[height<=720][fps<=30][vcodec^=avc]"
            "+bestaudio/best[height<=720]"
        ),
        ytdl_format_live: str = (
            "best[height<=480][vcodec^=avc]"
            "/best[height<=480]"
        ),
        library: "Library | None" = None,
        config: "ServerConfig | None" = None,
        event_bus: "EventBus | None" = None,
    ):
        self.mpv = mpv
        self.queue = queue
        self.ytdl_format = ytdl_format
        self.ytdl_format_live = ytdl_format_live
        self.library = library
        self._config = config
        self.event_bus = event_bus
        self._thread: threading.Thread | None = None
        self._running = False
        self._mpv_process: subprocess.Popen | None = None
        self._current_item: QueueItem | None = None
        self._skip_requested = False
        self._stop_requested = False

        # Sleep timer state (ephemeral, resets on restart)
        self._stop_after_current: bool = False
        self._stop_at_time: float | None = None  # monotonic deadline

        # Queue loop state (ephemeral, resets on restart)
        self._loop_enabled: bool = False
        self._loop_count: int = 0

        # Start position for next play_now item (seconds)
        self._next_start_time: float = 0

        # Cascade protection state
        self._consecutive_failures = 0
        self._rapid_successes = 0

        # Hardware detection (cached at init)
        self._audio_device = detect_hdmi_audio()
        self._wayland_display = detect_wayland()

    @property
    def is_running(self) -> bool:
        return self._running

    def start(self):
        """Start the player processing loop."""
        if self._running:
            return
        self._cleanup_stale_mpv()
        self.queue.reset_stale_playing()
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="player-loop")
        self._thread.start()

        # Start DB backup thread if configured
        backup_hours = self._config.db_backup_interval_hours if self._config else 6
        if backup_hours > 0:
            self._backup_thread = threading.Thread(
                target=self._backup_loop, args=(backup_hours,),
                daemon=True, name="db-backup",
            )
            self._backup_thread.start()

        logger.info("Player started")

    def stop(self):
        """Stop the player loop and kill mpv if running."""
        self._running = False
        self._kill_mpv()
        if self._thread:
            self._thread.join(timeout=10)
            self._thread = None
        logger.info("Player stopped")

    def _cleanup_stale_mpv(self):
        """Kill any orphaned mpv processes and remove stale socket.

        When picast-server restarts while mpv is playing, the old mpv becomes
        an orphan that holds the display and IPC socket. This method kills ALL
        mpv processes (since we're about to start fresh) and removes the socket.
        """
        socket_path = self.mpv.socket_path

        # Kill any existing mpv processes - they're orphans from a previous server
        try:
            result = subprocess.run(
                ["pgrep", "-x", "mpv"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode == 0:
                pids = result.stdout.strip().split("\n")
                for pid in pids:
                    pid = pid.strip()
                    if pid:
                        try:
                            os.kill(int(pid), signal.SIGTERM)
                            logger.info("Killed orphaned mpv process: %s", pid)
                        except (ProcessLookupError, ValueError):
                            pass
                # Give them a moment to die
                time.sleep(1)
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            pass

        # Remove stale socket
        if os.path.exists(socket_path):
            try:
                os.remove(socket_path)
                logger.info("Removed stale mpv socket: %s", socket_path)
            except OSError:
                pass

    def _backup_loop(self, interval_hours: int):
        """Periodically back up the SQLite database."""
        interval_secs = interval_hours * 3600
        while self._running:
            time.sleep(interval_secs)
            if not self._running:
                break
            db_path = self._config.db_file if self._config else ""
            if db_path and os.path.exists(db_path):
                bak_path = db_path + ".bak"
                try:
                    shutil.copy2(db_path, bak_path)
                    logger.info("Database backed up to %s", bak_path)
                except OSError as e:
                    logger.warning("Database backup failed: %s", e)

    def _loop(self):
        """Main player loop."""
        while self._running:
            # Check timed stop deadline
            if self._stop_at_time is not None and time.monotonic() >= self._stop_at_time:
                logger.info("Sleep timer expired, stopping playback")
                self._stop_at_time = None
                self._stop_after_current = False
                self._kill_mpv()
                continue

            # Check stop-after-current (between videos)
            if self._stop_after_current and self._current_item is None:
                time.sleep(2)
                continue

            # If stop was requested, don't pick up next item
            if self._stop_requested:
                time.sleep(2)
                continue

            next_item = self.queue.get_next()
            if next_item is None:
                if self._loop_enabled and self.queue.has_loopable():
                    count = self.queue.reset_for_loop()
                    self._loop_count += 1
                    self._emit("playback", f"Queue looped (pass #{self._loop_count})", f"Reset {count} videos")
                    self._show_osd(f"Queue looped - pass #{self._loop_count}")
                    continue  # Re-check immediately
                time.sleep(2)
                continue

            self._play_item(next_item)

    def _emit(self, event_type: str, title: str = "", detail: str = "",
              queue_item_id: int | None = None):
        """Emit an event if event bus is available."""
        if self.event_bus:
            self.event_bus.emit(event_type, title, detail, queue_item_id)

    def _show_osd(self, text: str):
        """Show text on TV screen via mpv OSD if enabled."""
        if self._config and not self._config.osd_enabled:
            return
        duration = self._config.osd_duration_ms if self._config else 2500
        self.mpv.show_text(text, duration)

    def _resolve_direct_urls(self, url: str, fmt: str) -> tuple[str, str | None]:
        """Use yt-dlp to resolve a YouTube URL to direct CDN URLs.

        Returns (video_url, audio_url). audio_url is None for combined formats.
        Direct CDN URLs support HTTP range requests, enabling --start= seeking.
        """
        auth = []
        if self._config:
            from picast.config import ytdl_auth_args
            auth = ytdl_auth_args(self._config)
        try:
            result = subprocess.run(
                ["yt-dlp", "-g", "-f", fmt, "--no-warnings", *auth, url],
                capture_output=True, text=True, timeout=120,
            )
            if result.returncode == 0:
                urls = result.stdout.strip().split("\n")
                video_url = urls[0]
                audio_url = urls[1] if len(urls) > 1 else None
                return video_url, audio_url
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as e:
            logger.warning("Failed to resolve direct URLs: %s", e)
        return "", None

    @staticmethod
    def _extract_video_id(url: str) -> str | None:
        """Extract YouTube video ID from URL via string parsing (no API calls).

        Handles youtube.com/watch?v=, music.youtube.com/watch?v=, youtu.be/,
        youtube.com/shorts/, /embed/, /live/.
        Returns None for non-YouTube URLs.
        """
        try:
            parsed = urlparse(url)
            host = (parsed.hostname or "").lower()
        except Exception:
            return None

        if "youtu.be" in host:
            vid = parsed.path.lstrip("/").split("/")[0]
            return vid if vid else None

        if "youtube.com" in host:
            if parsed.path == "/watch":
                params = parse_qs(parsed.query)
                ids = params.get("v", [])
                return ids[0] if ids else None
            for prefix in ("/shorts/", "/embed/", "/live/"):
                if parsed.path.startswith(prefix):
                    vid = parsed.path[len(prefix):].split("/")[0].split("?")[0]
                    return vid if vid else None

        return None

    def _resolve_for_seek(self, url: str, fmt: str) -> tuple[float | None, str, str | None]:
        """Resolve duration + direct CDN URLs in a single yt-dlp call.

        Returns (duration, video_url, audio_url).
        duration is None for live streams or parse failures.
        audio_url is None for combined formats.
        """
        auth = []
        if self._config:
            from picast.config import ytdl_auth_args
            auth = ytdl_auth_args(self._config)
        try:
            result = subprocess.run(
                ["yt-dlp", "--print", "duration", "-g", "-f", fmt,
                 "--no-warnings", *auth, url],
                capture_output=True, text=True, timeout=120,
            )
            if result.returncode != 0:
                logger.warning("_resolve_for_seek failed: %s", result.stderr.strip())
                return None, "", None

            lines = [l for l in result.stdout.strip().split("\n") if l.strip()]
            if len(lines) < 2:
                logger.warning("_resolve_for_seek: unexpected output (%d lines)", len(lines))
                return None, "", None

            # Line 1: duration (may be "NA" for live streams)
            raw_dur = lines[0].strip()
            duration: float | None = None
            if raw_dur not in ("NA", ""):
                try:
                    duration = float(raw_dur)
                except ValueError:
                    pass

            video_url = lines[1].strip()
            audio_url = lines[2].strip() if len(lines) > 2 else None

            return duration, video_url, audio_url

        except (FileNotFoundError, subprocess.TimeoutExpired, OSError) as e:
            logger.warning("_resolve_for_seek error: %s", e)
            return None, "", None

    def _play_item(self, item: QueueItem):
        """Play a single queue item with cascade protection."""
        logger.info("Playing: %s (%s)", item.url, item.source_type)

        # Mark as playing before we start
        self.queue.mark_playing(item.id)
        self._current_item = item
        self._skip_requested = False
        self._stop_requested = False

        # Use title from extension/queue (no blocking yt-dlp call)
        display_title = item.title or item.url

        # Emit loading event
        self._emit("playback", f"Loading: {display_title}", item.url, item.id)

        # Build mpv command - use source-appropriate format strings
        is_live = item.source_type == "twitch"
        is_archive = item.source_type == "archive"
        if is_live:
            fmt = self.ytdl_format_live
        elif is_archive:
            # Archive.org has varied codecs (MPEG-4, VP8, etc.)
            # YouTube-specific format string (vcodec^=avc) fails here
            fmt = "best[height<=720]/best"
        else:
            fmt = self.ytdl_format

        # Build ytdl-raw-options with auth if configured
        raw_opts = "js-runtimes=deno,remote-components=ejs:github"
        if self._config:
            from picast.config import ytdl_raw_options_auth
            auth_opt = ytdl_raw_options_auth(self._config)
            if auth_opt:
                raw_opts += f",{auth_opt}"

        hwdec = self._config.mpv_hwdec if self._config else "auto"

        # Capture seek position (from play_now with start_time)
        seek_to = self._next_start_time
        self._next_start_time = 0

        # Base mpv command — always starts idle so we get a window + OSD immediately
        # ytdl options go on the command line (not in loadfile IPC) because
        # ytdl-raw-options contains commas that break mpv's loadfile option parser
        cmd = [
            "mpv",
            f"--input-ipc-server={self.mpv.socket_path}",
            f"--hwdec={hwdec}",
            f"--ytdl-format={fmt}",
            f"--ytdl-raw-options={raw_opts}",
            "--cache=yes",
            "--demuxer-max-bytes=50MiB",
            "--log-file=/tmp/mpv-debug.log",
            "--fullscreen",
            "--idle=yes",
            "--force-window=immediate",
            "--osc=no",
            "--no-terminal",
            "--image-display-duration=inf",
        ]

        # Live stream optimizations (Twitch, etc.)
        if is_live:
            cmd.extend([
                "--profile=low-latency",
                "--cache-secs=10",
                "--demuxer-readahead-secs=5",
                "--demuxer-lavf-o=live_start_index=-1,fflags=+discardcorrupt",
                "--vd-lavc-threads=4",
                "--framedrop=decoder+vo",
                "--audio-stream-silence",
            ])

        # Add HDMI audio device if detected
        if self._audio_device:
            cmd.append(f"--audio-device={self._audio_device}")

        start_time = time.monotonic()
        exit_code = -1

        try:
            mpv_log = open("/tmp/mpv-debug.log", "w")

            # Pass Wayland display to mpv so it renders on the compositor
            env = os.environ.copy()
            if self._wayland_display:
                env["WAYLAND_DISPLAY"] = self._wayland_display
                env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")

            self._mpv_process = subprocess.Popen(
                cmd,
                stdout=mpv_log,
                stderr=mpv_log,
                env=env,
            )

            # Wait for mpv IPC socket (Pi needs 2-4s to create it)
            connected = False
            for _ in range(20):  # 10 seconds max
                if self.mpv.connect():
                    connected = True
                    break
                time.sleep(0.5)
            if not connected:
                logger.error("Failed to connect to mpv IPC after 10s")

            # Show thumbnail + title immediately for YouTube videos
            video_id = self._extract_video_id(item.url) if item.source_type == "youtube" else None
            if video_id:
                thumb_url = f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"
                self.mpv.command("loadfile", thumb_url, "replace")
                self.mpv.show_text(display_title, 120000)
            else:
                self.mpv.show_text(f"Loading: {display_title}", 120000)

            self._emit("playback", f"Loading: {display_title}", item.url, item.id)

            # Track whether this is a DRM-protected movie (trailer-only)
            is_protected_movie = False

            if seek_to > 0 and item.source_type == "youtube" and not is_live:
                # Single yt-dlp call: get duration + direct CDN URLs
                logger.info("Resolving URLs + duration for timestamp seek to %ds", int(seek_to))
                duration, video_url, audio_url = self._resolve_for_seek(item.url, fmt)

                # Duration validation: if start_time >= reported duration,
                # yt-dlp resolved a trailer for a DRM-protected movie.
                # mpv can only play the trailer — seek is impossible.
                if duration is not None and seek_to >= duration:
                    logger.warning(
                        "start_time %ds >= duration %ds — DRM-protected movie, "
                        "trailer only (Widevine)",
                        int(seek_to), int(duration),
                    )
                    video_url = ""
                    is_protected_movie = True

                if seek_to > 0 and video_url:
                    # Load video with start position
                    # Index arg (0) required for mpv v0.38+ IPC: loadfile url flags index options
                    resp = self.mpv.command("loadfile", video_url, "replace",
                                           0, f"start={int(seek_to)}")
                    if not resp or resp.get("error") != "success":
                        logger.error("loadfile seek failed: %s", resp)
                    # Add audio track separately (CDN URLs have commas that
                    # break mpv's comma-separated option parser)
                    if audio_url:
                        time.sleep(0.5)
                        self.mpv.command("audio-add", audio_url)
                else:
                    # Fallback: load YouTube URL normally (no seek via CDN)
                    if not video_url and seek_to > 0 and not is_protected_movie:
                        logger.warning("Direct URL resolve failed, loading without seek")
                    self.mpv.command("loadfile", item.url, "replace")
            else:
                # Normal load via mpv's yt-dlp hook (ytdl opts set on CLI)
                self.mpv.command("loadfile", item.url, "replace")

            self._show_osd(f"Now Playing: {display_title}")

            # Brief pause to let mpv process the loadfile before polling
            # (avoids race where thumbnail's idle-active state is read)
            time.sleep(0.5)

            # Two-phase poll: wait for playback to START, then wait for it to END.
            # mpv starts idle (--idle=yes), so idle-active is True until loadfile
            # finishes loading the stream. On Pi this takes 30-90 seconds.

            # Phase 1: Wait for playback to start (idle-active becomes False)
            load_deadline = time.monotonic() + 150  # 2.5 min timeout for Pi
            playback_started = False
            while time.monotonic() < load_deadline:
                if self._mpv_process.poll() is not None:
                    break
                if self._skip_requested or self._stop_requested:
                    break
                idle = self.mpv.get_property("idle-active", True)
                if not idle:
                    playback_started = True
                    logger.info("Playback started for: %s", display_title)
                    break
                time.sleep(1)

            if not playback_started:
                logger.warning("Playback never started (timeout or exit): %s", item.url)

            # Backfill title from mpv if we didn't have one
            if playback_started and not item.title:
                mpv_title = self.mpv.get_property("media-title", "")
                if mpv_title and mpv_title != item.url:
                    item.title = mpv_title
                    display_title = mpv_title
                    logger.info("Backfilled title from mpv: %s", mpv_title)

            # DRM-protected movie: notify user on TV and web UI
            if playback_started and is_protected_movie:
                notice = "YouTube Movie \u2014 trailer only (DRM protected)"
                self.mpv.show_text(notice, 8000)
                self._emit(
                    "protected",
                    title=notice,
                    detail="This is a paid YouTube movie. Only the trailer "
                           "is available without Widevine DRM decryption.",
                    queue_item_id=item.id,
                )

            # Phase 2: Wait for playback to end (idle-active or eof-reached)
            while playback_started:
                if self._mpv_process.poll() is not None:
                    break
                if self._skip_requested or self._stop_requested:
                    break
                idle = self.mpv.get_property("idle-active", False)
                eof = self.mpv.get_property("eof-reached", False)
                if idle or eof:
                    break
                time.sleep(1)

            exit_code = self._mpv_process.poll()
            if exit_code is None:
                # mpv is still running (idle) — quit it
                self.mpv.command("quit")
                try:
                    self._mpv_process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self._mpv_process.kill()
                exit_code = self._mpv_process.returncode or 0

        except FileNotFoundError:
            logger.error("mpv not found. Install it: sudo apt install mpv")
            exit_code = -1
        except OSError as e:
            logger.error("Failed to start mpv: %s", e)
            exit_code = -1
        finally:
            play_duration = time.monotonic() - start_time
            self.mpv.disconnect()
            self._mpv_process = None

            # Cascade protection logic
            cascade_action = self._check_cascade(exit_code, play_duration, item)

            if self._stop_requested:
                # Stop requested: re-mark as pending so it replays next time
                self.queue.mark_pending(item.id)
                logger.info("Stop requested - item %d returned to pending", item.id)
            elif cascade_action == "retry":
                # Don't mark as played - will retry on next loop
                pass
            elif cascade_action == "failed":
                # Permanently failed after max retries
                pass
            elif self._skip_requested:
                self.queue.mark_skipped(item.id)
            else:
                self.queue.mark_played(item.id)
                self._emit("playback", f"Completed: {display_title}", item.url, item.id)

            # Auto-save to library (only for real plays, not stop/retry/failed)
            if (not self._stop_requested and cascade_action not in ("retry", "failed")
                    and self.library):
                try:
                    self.library.record_play(
                        url=item.url,
                        title=item.title,
                        source_type=item.source_type,
                    )
                except Exception as e:
                    logger.warning("Failed to save to library: %s", e)

            self._current_item = None
            logger.info(
                "Finished: %s (exit=%d, %.0fs)",
                item.url, exit_code, play_duration,
            )

    def _check_cascade(self, exit_code: int, duration: float, item: QueueItem) -> str:
        """Check for cascade failure patterns and apply protection.

        Returns:
            "ok" - Normal play, counters reset
            "retry" - Failed quickly, will retry (item NOT marked played)
            "failed" - Too many failures, marked as permanently failed
        """
        display_title = item.title or item.url

        # Skip cascade checks if user requested stop/skip
        if self._stop_requested or self._skip_requested:
            self._consecutive_failures = 0
            self._rapid_successes = 0
            return "ok"

        if duration >= MIN_PLAY_SECONDS and exit_code == 0:
            # Normal successful play
            self._consecutive_failures = 0
            self._rapid_successes = 0
            return "ok"

        if exit_code != 0 and duration < MIN_PLAY_SECONDS:
            # mpv failed quickly - not a real play
            self._consecutive_failures += 1
            self._rapid_successes = 0

            error_reason = self._classify_error(exit_code, item)
            error_count = self.queue.increment_error(item.id, error_reason)

            logger.warning(
                "RAPID FAILURE (exit=%d, %.0fs, streak=%d, errors=%d): %s",
                exit_code, duration, self._consecutive_failures, error_count, item.url,
            )

            if self._consecutive_failures >= MAX_RAPID_FAILURES:
                # Mark as permanently failed
                self.queue.mark_failed(item.id)
                self._consecutive_failures = 0

                self._emit(
                    "failed",
                    f"Failed: {display_title}",
                    error_reason,
                    item.id,
                )
                self._show_osd(f"Failed: {display_title}")

                logger.warning(
                    "CASCADE PROTECTION: %d failures, marking failed: %s",
                    error_count, item.url,
                )
                backoff = FAILURE_BACKOFF[-1]
                time.sleep(backoff)
                return "failed"
            else:
                # Retry with exponential backoff
                self.queue.mark_pending(item.id)
                retry_num = self._consecutive_failures
                backoff_idx = min(retry_num - 1, len(FAILURE_BACKOFF) - 1)
                backoff = FAILURE_BACKOFF[backoff_idx]

                self._emit(
                    "error",
                    f"Retrying ({retry_num}/{MAX_RAPID_FAILURES}): {display_title}",
                    error_reason,
                    item.id,
                )
                self._show_osd(
                    f"Retrying ({retry_num}/{MAX_RAPID_FAILURES}): {display_title}"
                )

                time.sleep(backoff)
                return "retry"

        if exit_code == 0 and duration < MIN_PLAY_SECONDS:
            # Exited OK but suspiciously fast (skip command, yt-dlp silent fail)
            self._rapid_successes += 1
            self._consecutive_failures = 0

            error_reason = self._classify_error(exit_code, item)
            error_count = self.queue.increment_error(item.id, error_reason)

            logger.warning(
                "RAPID EXIT (exit=0, %.0fs, streak=%d, errors=%d): %s",
                duration, self._rapid_successes, error_count, item.url,
            )

            if self._rapid_successes >= MAX_RAPID_FAILURES:
                self.queue.mark_failed(item.id)
                self._rapid_successes = 0

                self._emit(
                    "failed",
                    f"Failed: {display_title}",
                    error_reason,
                    item.id,
                )
                self._show_osd(f"Failed: {display_title}")

                backoff = FAILURE_BACKOFF[-1]
                time.sleep(backoff)
                return "failed"
            else:
                self.queue.mark_pending(item.id)
                retry_num = self._rapid_successes
                backoff_idx = min(retry_num - 1, len(FAILURE_BACKOFF) - 1)
                backoff = FAILURE_BACKOFF[backoff_idx]

                self._emit(
                    "error",
                    f"Retrying ({retry_num}/{MAX_RAPID_FAILURES}): {display_title}",
                    error_reason,
                    item.id,
                )
                self._show_osd(
                    f"Retrying ({retry_num}/{MAX_RAPID_FAILURES}): {display_title}"
                )

                time.sleep(backoff)
                return "retry"

        # Non-zero exit but played for a while (user quit, network drop, etc.)
        self._consecutive_failures = 0
        self._rapid_successes = 0
        return "ok"

    def _classify_error(self, exit_code: int, item: QueueItem) -> str:
        """Classify the error reason from mpv exit code and log file.

        Returns a human-readable error string.
        """
        # Check known mpv exit codes
        mpv_codes = {
            2: "mpv: file/device unavailable",
            3: "mpv: network error",
            4: "mpv: codec/format not supported",
        }
        if exit_code in mpv_codes:
            return mpv_codes[exit_code]

        # Try to parse mpv debug log for more detail
        try:
            with open("/tmp/mpv-debug.log", "r") as f:
                lines = f.readlines()
            # Scan last 50 lines for error patterns
            for line in reversed(lines[-50:]):
                line_lower = line.lower()
                if "403" in line or "forbidden" in line_lower:
                    return "HTTP 403 Forbidden (auth/geo-blocked)"
                if "unable to extract" in line_lower:
                    return "yt-dlp: unable to extract video data"
                if "timeout" in line_lower or "timed out" in line_lower:
                    return "Network timeout"
                if "error" in line_lower and "level" not in line_lower:
                    # Get the actual error message (trim to reasonable length)
                    msg = line.strip()[-120:]
                    if msg:
                        return f"mpv: {msg}"
        except (OSError, IndexError):
            pass

        # Fallback
        if exit_code == 0:
            return "Exited too quickly (possible yt-dlp failure)"
        return f"mpv exited with code {exit_code}"

    def _get_title(self, url: str) -> str:
        """Get video title via yt-dlp."""
        try:
            auth = []
            if self._config:
                from picast.config import ytdl_auth_args
                auth = ytdl_auth_args(self._config)
            result = subprocess.run(
                ["yt-dlp", "--get-title", "--no-warnings", *auth, url],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            pass
        return ""

    def _kill_mpv(self):
        """Kill the mpv subprocess if running."""
        if self._mpv_process and self._mpv_process.poll() is None:
            self._mpv_process.terminate()
            try:
                self._mpv_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._mpv_process.kill()

    def skip(self):
        """Skip the current video."""
        self._skip_requested = True
        # Try IPC first (graceful)
        if self.mpv.connected:
            self.mpv.command("quit")
        else:
            # Fallback: kill the process
            self._kill_mpv()

    def play_now(self, url: str, title: str = "", start_time: float = 0):
        """Play a URL immediately, interrupting current playback.

        Adds the URL to the front of the queue and skips the current video.
        """
        # Clear stop state so new playback can start
        self._stop_requested = False
        self._next_start_time = start_time
        item = self.queue.add(url, title)
        # Move it to the front by reordering
        pending = self.queue.get_pending()
        ids = [item.id] + [i.id for i in pending if i.id != item.id]
        self.queue.reorder(ids)
        # Skip whatever is playing
        if self._current_item:
            self.skip()

    def play_item_now(self, item_id: int, start_time: float = 0):
        """Play an existing queue item immediately by its ID.

        Moves the item to the front of the queue and skips the current video.
        Unlike play_now(), this does NOT create a duplicate queue entry.
        """
        self._stop_requested = False
        self._next_start_time = start_time
        if not self.queue.move_to_front(item_id):
            raise ValueError(f"Queue item {item_id} not found")
        # Skip whatever is playing
        if self._current_item:
            self.skip()

    def stop_playback(self):
        """Stop playback and pause the queue (don't advance to next item).

        The current item is returned to pending status so it replays when
        playback is resumed via play_now() or resume_playback().
        """
        self._stop_requested = True
        self._kill_mpv()

    def resume_playback(self):
        """Resume queue processing after stop_playback()."""
        self._stop_requested = False

    def set_stop_after_current(self, enabled: bool):
        """Toggle stop-after-current-video mode."""
        self._stop_after_current = enabled
        logger.info("Stop after current: %s", enabled)

    def set_stop_timer(self, minutes: int):
        """Set a sleep timer. 0 = cancel."""
        if minutes <= 0:
            self._stop_at_time = None
            logger.info("Sleep timer cancelled")
        else:
            self._stop_at_time = time.monotonic() + (minutes * 60)
            logger.info("Sleep timer set: %d minutes", minutes)

    def get_timer_state(self) -> dict:
        """Get current timer state."""
        remaining = None
        if self._stop_at_time is not None:
            remaining = max(0, self._stop_at_time - time.monotonic())
        return {
            "stop_after_current": self._stop_after_current,
            "stop_timer_remaining": remaining,
        }

    def set_loop(self, enabled: bool):
        """Toggle queue loop mode."""
        self._loop_enabled = enabled
        logger.info("Queue loop: %s", "enabled" if enabled else "disabled")

    def get_loop_state(self) -> dict:
        """Get current loop state."""
        return {"loop_enabled": self._loop_enabled, "loop_count": self._loop_count}

    def get_status(self) -> dict:
        """Get combined player + mpv status."""
        status = self.mpv.get_status()
        status["player_running"] = self._running
        status["stopped"] = self._stop_requested

        if self._current_item:
            # Override idle if we have an active item - mpv may briefly
            # report idle during livestream buffering/reconnection
            status["idle"] = False
            status["queue_item_id"] = self._current_item.id
            if not status.get("title"):
                status["title"] = self._current_item.title
            status["source_type"] = self._current_item.source_type
            status["url"] = self._current_item.url
        else:
            status["source_type"] = ""
            status["url"] = ""

        # Include timer + loop state
        status.update(self.get_timer_state())
        status.update(self.get_loop_state())

        return status
