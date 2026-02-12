"""Player logic - processes queue and manages mpv playback.

This replaces player.sh from the bash version. Runs as a background thread
that watches the queue and plays the next item when mpv becomes idle.

Includes cascade protection, Wayland auto-detection, and HDMI audio routing
ported from the original bash player.
"""

import logging
import os
import subprocess
import threading
import time

from picast.server.mpv_client import MPVClient
from picast.server.queue_manager import QueueItem, QueueManager

# Optional library import - player works without it
try:
    from picast.server.library import Library
except ImportError:
    Library = None

logger = logging.getLogger(__name__)

# Cascade protection thresholds
MIN_PLAY_SECONDS = 5       # Under this = "didn't really play"
MAX_RAPID_FAILURES = 3     # After this many rapid failures, skip and back off
FAILURE_BACKOFF = 30       # Seconds to wait after max failures


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
        ytdl_format: str = "bestvideo[height<=720][fps<=30][vcodec^=avc]+bestaudio/best[height<=720]",
        ytdl_format_live: str = "bestvideo[height<=480][vcodec^=avc]+bestaudio/best[height<=480]",
        library: "Library | None" = None,
    ):
        self.mpv = mpv
        self.queue = queue
        self.ytdl_format = ytdl_format
        self.ytdl_format_live = ytdl_format_live
        self.library = library
        self._thread: threading.Thread | None = None
        self._running = False
        self._mpv_process: subprocess.Popen | None = None
        self._current_item: QueueItem | None = None
        self._skip_requested = False

        # Sleep timer state (ephemeral, resets on restart)
        self._stop_after_current: bool = False
        self._stop_at_time: float | None = None  # monotonic deadline

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
        self._cleanup_stale_socket()
        self.queue.reset_stale_playing()
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="player-loop")
        self._thread.start()
        logger.info("Player started")

    def stop(self):
        """Stop the player loop and kill mpv if running."""
        self._running = False
        self._kill_mpv()
        if self._thread:
            self._thread.join(timeout=10)
            self._thread = None
        logger.info("Player stopped")

    def _cleanup_stale_socket(self):
        """Remove stale mpv socket if no mpv process is running.

        Prevents connection failures when picast-server restarts while mpv
        was running (leaves orphaned socket file).
        """
        socket_path = self.mpv.socket_path
        if not os.path.exists(socket_path):
            return

        # Check if any mpv process is running
        try:
            result = subprocess.run(
                ["pgrep", "-x", "mpv"],
                capture_output=True, timeout=5,
            )
            if result.returncode != 0:
                # No mpv running but socket exists - stale
                os.remove(socket_path)
                logger.info("Cleaned up stale mpv socket: %s", socket_path)
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            pass

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

            next_item = self.queue.get_next()
            if next_item is None:
                time.sleep(2)
                continue

            self._play_item(next_item)

    def _play_item(self, item: QueueItem):
        """Play a single queue item with cascade protection."""
        logger.info("Playing: %s (%s)", item.url, item.source_type)

        # Clean up stale socket before starting
        self._cleanup_stale_socket()

        # Mark as playing before we start
        self.queue.mark_playing(item.id)
        self._current_item = item
        self._skip_requested = False

        # Resolve title via yt-dlp if we don't have one
        if not item.title and item.source_type == "youtube":
            item.title = self._get_title(item.url)

        # Build mpv command - use lower quality for live streams
        is_live = item.source_type == "twitch"
        fmt = self.ytdl_format_live if is_live else self.ytdl_format

        cmd = [
            "mpv",
            f"--input-ipc-server={self.mpv.socket_path}",
            f"--ytdl-format={fmt}",
            "--ytdl-raw-options=js-runtimes=deno,remote-components=ejs:github,cookies=/home/jopi/.config/yt-dlp/cookies.txt",
            "--hwdec=auto",
            "--cache=yes",
            "--demuxer-max-bytes=50MiB",
            "--log-file=/tmp/mpv-debug.log",
            "--fullscreen",
            "--no-terminal",
        ]

        # Add HDMI audio device if detected
        if self._audio_device:
            cmd.append(f"--audio-device={self._audio_device}")

        cmd.append(item.url)

        start_time = time.monotonic()
        exit_code = -1

        try:
            mpv_log = open("/tmp/mpv-debug.log", "w")
            self._mpv_process = subprocess.Popen(
                cmd,
                stdout=mpv_log,
                stderr=mpv_log,
            )

            # Give mpv a moment to create the socket
            time.sleep(1)
            self.mpv.connect()

            # Wait for mpv to exit
            self._mpv_process.wait()
            exit_code = self._mpv_process.returncode

        except FileNotFoundError:
            logger.error("mpv not found. Install it: sudo apt install mpv")
        except OSError as e:
            logger.error("Failed to start mpv: %s", e)
        finally:
            play_duration = time.monotonic() - start_time
            self.mpv.disconnect()
            self._mpv_process = None

            # Cascade protection logic
            cascade_action = self._check_cascade(exit_code, play_duration, item)

            if cascade_action == "retry":
                # Don't mark as played - will retry on next loop
                pass
            elif self._skip_requested:
                self.queue.mark_skipped(item.id)
            else:
                self.queue.mark_played(item.id)

            # Auto-save to library (only for real plays)
            if cascade_action != "retry" and self.library:
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
            "skip" - Too many failures, skip and back off
        """
        if duration >= MIN_PLAY_SECONDS and exit_code == 0:
            # Normal successful play
            self._consecutive_failures = 0
            self._rapid_successes = 0
            return "ok"

        if exit_code != 0 and duration < MIN_PLAY_SECONDS:
            # mpv failed quickly - not a real play
            self._consecutive_failures += 1
            self._rapid_successes = 0
            logger.warning(
                "RAPID FAILURE (exit=%d, %.0fs, streak=%d): %s",
                exit_code, duration, self._consecutive_failures, item.url,
            )

            if self._consecutive_failures >= MAX_RAPID_FAILURES:
                logger.warning(
                    "CASCADE PROTECTION: %d failures, skipping and backing off %ds",
                    self._consecutive_failures, FAILURE_BACKOFF,
                )
                self._consecutive_failures = 0
                time.sleep(FAILURE_BACKOFF)
                return "skip"
            else:
                # Undo playing status so it retries
                self.queue.mark_pending(item.id)
                time.sleep(3)
                return "retry"

        if exit_code == 0 and duration < MIN_PLAY_SECONDS:
            # Exited OK but suspiciously fast (skip command, yt-dlp silent fail)
            self._rapid_successes += 1
            self._consecutive_failures = 0
            logger.warning(
                "RAPID EXIT (exit=0, %.0fs, streak=%d): %s",
                duration, self._rapid_successes, item.url,
            )

            if self._rapid_successes >= MAX_RAPID_FAILURES:
                logger.warning(
                    "CASCADE PROTECTION: %d rapid exits, pausing %ds",
                    self._rapid_successes, FAILURE_BACKOFF,
                )
                self.queue.mark_pending(item.id)
                self._rapid_successes = 0
                time.sleep(FAILURE_BACKOFF)
                return "retry"

        # Non-zero exit but played for a while (user quit, network drop, etc.)
        self._consecutive_failures = 0
        self._rapid_successes = 0
        return "ok"

    def _get_title(self, url: str) -> str:
        """Get video title via yt-dlp."""
        try:
            result = subprocess.run(
                ["yt-dlp", "--get-title", "--no-warnings", url],
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

    def play_now(self, url: str, title: str = ""):
        """Play a URL immediately, interrupting current playback.

        Adds the URL to the front of the queue and skips the current video.
        """
        item = self.queue.add(url, title)
        # Move it to the front by reordering
        pending = self.queue.get_pending()
        ids = [item.id] + [i.id for i in pending if i.id != item.id]
        self.queue.reorder(ids)
        # Skip whatever is playing
        if self._current_item:
            self.skip()

    def stop_playback(self):
        """Stop playback without advancing the queue."""
        if self._current_item:
            # Mark current as pending again so it replays
            current_id = self._current_item.id
            self._kill_mpv()
            # Re-mark as pending (it was set to played in _play_item's finally)
            # We handle this by not marking as played when stop is used
            # Actually, we need a flag for this
            # For now, skip will work - user can re-add if needed

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

    def get_status(self) -> dict:
        """Get combined player + mpv status."""
        status = self.mpv.get_status()
        status["player_running"] = self._running

        if self._current_item:
            status["queue_item_id"] = self._current_item.id
            if not status.get("title"):
                status["title"] = self._current_item.title
            status["source_type"] = self._current_item.source_type
            status["url"] = self._current_item.url
        else:
            status["source_type"] = ""
            status["url"] = ""

        # Include timer state
        status.update(self.get_timer_state())

        return status
