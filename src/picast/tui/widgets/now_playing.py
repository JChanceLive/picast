"""Now Playing widget - shows current track info, progress bar, volume, speed."""

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Label, ProgressBar, Static


class NowPlaying(Widget):
    """Displays the currently playing track with progress."""

    DEFAULT_CSS = """
    NowPlaying {
        height: auto;
        padding: 0 1;
    }
    NowPlaying .np-title {
        text-style: bold;
        width: 1fr;
    }
    NowPlaying .np-url {
        color: $text-muted;
        width: 1fr;
    }
    NowPlaying .np-progress-row {
        height: 1;
        margin-top: 1;
    }
    NowPlaying .np-time {
        width: auto;
        min-width: 14;
        text-align: right;
        margin-left: 1;
    }
    NowPlaying .np-bar {
        width: 1fr;
    }
    NowPlaying .np-meta-row {
        height: 1;
    }
    NowPlaying .np-volume {
        width: auto;
        min-width: 12;
    }
    NowPlaying .np-speed {
        width: auto;
        min-width: 14;
        margin-left: 2;
    }
    NowPlaying .np-source {
        width: auto;
        min-width: 10;
        text-align: right;
        color: $accent;
        margin-left: 1;
    }
    NowPlaying .np-idle {
        text-style: italic;
        color: $text-muted;
        text-align: center;
        width: 1fr;
        margin: 1 0;
    }
    """

    title: reactive[str] = reactive("")
    url: reactive[str] = reactive("")
    position: reactive[float] = reactive(0.0)
    duration: reactive[float] = reactive(0.0)
    volume: reactive[int] = reactive(100)
    speed: reactive[float] = reactive(1.0)
    paused: reactive[bool] = reactive(False)
    idle: reactive[bool] = reactive(True)
    source_type: reactive[str] = reactive("")

    def compose(self) -> ComposeResult:
        yield Label("Nothing playing - add a URL with [bold]A[/bold]", id="np-idle", classes="np-idle")
        yield Label("", id="np-title", classes="np-title")
        yield Label("", id="np-url", classes="np-url")
        with Horizontal(classes="np-progress-row"):
            yield ProgressBar(total=100, show_eta=False, show_percentage=False, id="np-bar", classes="np-bar")
            yield Label("0:00 / 0:00", id="np-time", classes="np-time")
        with Horizontal(classes="np-meta-row"):
            yield Label("Vol: 100", id="np-volume", classes="np-volume")
            yield Label("Speed: 1.0x", id="np-speed", classes="np-speed")
            yield Label("", id="np-source", classes="np-source")

    def update_status(self, status: dict) -> None:
        """Update all fields from a status dict."""
        self.idle = status.get("idle", True)
        if not self.idle:
            self.title = status.get("title", "") or status.get("path", "")
            self.url = status.get("url", "") or status.get("path", "")
            self.position = status.get("position", 0) or 0
            self.duration = status.get("duration", 0) or 0
            self.volume = int(status.get("volume", 100) or 100)
            self.speed = status.get("speed", 1.0) or 1.0
            self.paused = status.get("paused", False)
            self.source_type = status.get("source_type", "")

    def watch_idle(self, idle: bool) -> None:
        idle_label = self.query_one("#np-idle", Label)
        title_label = self.query_one("#np-title", Label)
        url_label = self.query_one("#np-url", Label)
        bar = self.query_one("#np-bar", ProgressBar)
        time_label = self.query_one("#np-time", Label)
        vol_label = self.query_one("#np-volume", Label)
        speed_label = self.query_one("#np-speed", Label)
        source_label = self.query_one("#np-source", Label)

        idle_label.display = idle
        title_label.display = not idle
        url_label.display = not idle
        bar.display = not idle
        time_label.display = not idle
        vol_label.display = not idle
        speed_label.display = not idle
        source_label.display = not idle

    def watch_title(self, title: str) -> None:
        icon = "|| " if self.paused else ">> " if not self.idle else ""
        self.query_one("#np-title", Label).update(f"{icon}{title}")

    def watch_paused(self, paused: bool) -> None:
        # Re-trigger title update to change icon
        self.watch_title(self.title)

    def watch_url(self, url: str) -> None:
        self.query_one("#np-url", Label).update(url)

    def watch_position(self, position: float) -> None:
        self._update_progress()

    def watch_duration(self, duration: float) -> None:
        self._update_progress()

    def _update_progress(self) -> None:
        bar = self.query_one("#np-bar", ProgressBar)
        time_label = self.query_one("#np-time", Label)

        if self.duration > 0:
            pct = (self.position / self.duration) * 100
            bar.update(progress=pct)
        else:
            bar.update(progress=0)

        pos_str = _format_time(self.position)
        dur_str = _format_time(self.duration)
        time_label.update(f"{pos_str} / {dur_str}")

    def watch_volume(self, volume: int) -> None:
        self.query_one("#np-volume", Label).update(f"Vol: {volume}")

    def watch_speed(self, speed: float) -> None:
        self.query_one("#np-speed", Label).update(f"Speed: {speed:.1f}x")

    def watch_source_type(self, source_type: str) -> None:
        tag = {"youtube": "[YT]", "local": "[Local]", "twitch": "[Twitch]"}.get(source_type, "")
        self.query_one("#np-source", Label).update(tag)


def _format_time(seconds: float) -> str:
    """Format seconds as M:SS or H:MM:SS."""
    s = int(seconds)
    if s < 0:
        s = 0
    h, remainder = divmod(s, 3600)
    m, sec = divmod(remainder, 60)
    if h > 0:
        return f"{h}:{m:02d}:{sec:02d}"
    return f"{m}:{sec:02d}"
