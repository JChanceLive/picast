"""mpv JSON IPC client.

Communicates with mpv via its Unix domain socket using the JSON IPC protocol.
Ref: https://mpv.io/manual/master/#json-ipc
"""

import json
import logging
import socket
import threading
import time

logger = logging.getLogger(__name__)


class MPVError(Exception):
    """Error communicating with mpv."""


class MPVClient:
    """Client for mpv's JSON IPC protocol over Unix socket.

    Usage:
        client = MPVClient("/tmp/mpv-socket")
        client.connect()
        client.set_property("pause", True)
        pos = client.get_property("time-pos")
        client.command("quit")
    """

    def __init__(self, socket_path: str = "/tmp/mpv-socket"):
        self.socket_path = socket_path
        self._sock: socket.socket | None = None
        self._lock = threading.Lock()
        self._request_id = 0
        self._recv_buffer = b""

    @property
    def connected(self) -> bool:
        return self._sock is not None

    def connect(self, timeout: float = 5.0) -> bool:
        """Connect to the mpv IPC socket.

        Returns True if connected, False if socket doesn't exist yet.
        """
        with self._lock:
            if self._sock is not None:
                return True
            try:
                sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                sock.settimeout(timeout)
                sock.connect(self.socket_path)
                self._sock = sock
                self._recv_buffer = b""
                logger.info("Connected to mpv at %s", self.socket_path)
                return True
            except (FileNotFoundError, ConnectionRefusedError):
                logger.debug("mpv socket not available at %s", self.socket_path)
                return False
            except OSError as e:
                logger.warning("Failed to connect to mpv: %s", e)
                return False

    def disconnect(self):
        """Close the connection."""
        with self._lock:
            if self._sock:
                try:
                    self._sock.close()
                except OSError:
                    pass
                self._sock = None
                self._recv_buffer = b""

    def _send(self, data: dict) -> dict | None:
        """Send a JSON command and wait for the response."""
        if not self._sock:
            if not self.connect():
                return None

        self._request_id += 1
        data["request_id"] = self._request_id

        msg = json.dumps(data) + "\n"
        try:
            self._sock.sendall(msg.encode("utf-8"))
        except (BrokenPipeError, OSError):
            self.disconnect()
            return None

        return self._recv_response(self._request_id)

    def _recv_response(self, request_id: int, timeout: float = 5.0) -> dict | None:
        """Read lines from the socket until we find our response."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            # Check buffer for complete lines
            while b"\n" in self._recv_buffer:
                line, self._recv_buffer = self._recv_buffer.split(b"\n", 1)
                if not line.strip():
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                # Skip event messages, wait for our response
                if "event" in msg:
                    continue
                if msg.get("request_id") == request_id:
                    return msg

            # Read more data
            try:
                remaining = max(0.1, deadline - time.monotonic())
                self._sock.settimeout(remaining)
                chunk = self._sock.recv(4096)
                if not chunk:
                    self.disconnect()
                    return None
                self._recv_buffer += chunk
            except socket.timeout:
                break
            except OSError:
                self.disconnect()
                return None
        return None

    def command(self, *args) -> dict | None:
        """Send a command to mpv.

        Examples:
            client.command("quit")
            client.command("seek", 10, "relative")
            client.command("loadfile", "/path/to/file.mp4")
        """
        return self._send({"command": list(args)})

    def get_property(self, name: str, default=None):
        """Get an mpv property value.

        Common properties:
            time-pos     - Current position in seconds
            duration     - Total duration in seconds
            volume       - Volume (0-100)
            speed        - Playback speed
            pause        - Whether paused (bool)
            media-title  - Current media title
            path         - Current file/URL path
            idle-active  - Whether mpv is idle (not playing)
            eof-reached  - Whether end of file was reached
        """
        resp = self._send({"command": ["get_property", name]})
        if resp and resp.get("error") == "success":
            return resp.get("data")
        return default

    def set_property(self, name: str, value) -> bool:
        """Set an mpv property value."""
        resp = self._send({"command": ["set_property", name, value]})
        return resp is not None and resp.get("error") == "success"

    def get_status(self) -> dict:
        """Get full player status as a dict.

        Returns a dict with all relevant player state, or a minimal
        dict with idle=True if mpv isn't playing.
        """
        if not self.connected and not self.connect():
            return {"idle": True, "connected": False}

        idle = self.get_property("idle-active", True)
        if idle:
            return {"idle": True, "connected": True}

        return {
            "idle": False,
            "connected": True,
            "paused": self.get_property("pause", False),
            "title": self.get_property("media-title", ""),
            "path": self.get_property("path", ""),
            "position": self.get_property("time-pos", 0),
            "duration": self.get_property("duration", 0),
            "volume": self.get_property("volume", 100),
            "speed": self.get_property("speed", 1.0),
        }

    def play(self, url: str, append: bool = False) -> bool:
        """Load and play a URL or file path.

        If append=True, adds to mpv's internal playlist instead of replacing.
        """
        mode = "append-play" if append else "replace"
        resp = self.command("loadfile", url, mode)
        return resp is not None and resp.get("error") == "success"

    def pause(self) -> bool:
        return self.set_property("pause", True)

    def resume(self) -> bool:
        return self.set_property("pause", False)

    def toggle_pause(self) -> bool:
        paused = self.get_property("pause", False)
        return self.set_property("pause", not paused)

    def stop(self) -> bool:
        resp = self.command("stop")
        return resp is not None and resp.get("error") == "success"

    def seek(self, seconds: float, mode: str = "absolute") -> bool:
        """Seek to position.

        mode: "absolute" (default), "relative", "absolute-percent"
        """
        resp = self.command("seek", seconds, mode)
        return resp is not None and resp.get("error") == "success"

    def set_volume(self, level: int) -> bool:
        """Set volume (0-100)."""
        level = max(0, min(100, level))
        return self.set_property("volume", level)

    def set_speed(self, speed: float) -> bool:
        """Set playback speed (0.25 to 4.0)."""
        speed = max(0.25, min(4.0, speed))
        return self.set_property("speed", speed)

    def quit(self) -> bool:
        """Tell mpv to exit."""
        resp = self.command("quit")
        self.disconnect()
        return resp is not None
