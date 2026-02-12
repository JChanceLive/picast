"""Header bar widget - shows app name, device, and connection status."""

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Label


class HeaderBar(Widget):
    """Top bar showing PiCast branding and connection status."""

    DEFAULT_CSS = """
    HeaderBar {
        dock: top;
        height: 1;
        background: $primary;
        color: $text;
        padding: 0 1;
    }
    HeaderBar .hb-title {
        text-style: bold;
        width: auto;
    }
    HeaderBar .hb-spacer {
        width: 1fr;
    }
    HeaderBar .hb-device {
        width: auto;
        margin-right: 2;
        color: $text-muted;
    }
    HeaderBar .hb-status {
        width: auto;
        text-style: bold;
    }
    HeaderBar .hb-status.connected {
        color: $success;
    }
    HeaderBar .hb-status.disconnected {
        color: $error;
    }
    """

    device_name: reactive[str] = reactive("raspberrypi.local")
    connected: reactive[bool] = reactive(False)

    def compose(self) -> ComposeResult:
        with Horizontal():
            yield Label("PiCast", classes="hb-title")
            yield Label("", classes="hb-spacer")
            yield Label("", id="hb-device", classes="hb-device")
            yield Label("Connecting...", id="hb-status", classes="hb-status disconnected")

    def watch_device_name(self, name: str) -> None:
        self.query_one("#hb-device", Label).update(name)

    def watch_connected(self, connected: bool) -> None:
        status_label = self.query_one("#hb-status", Label)
        if connected:
            status_label.update("Connected")
            status_label.remove_class("disconnected")
            status_label.add_class("connected")
        else:
            status_label.update("Disconnected")
            status_label.remove_class("connected")
            status_label.add_class("disconnected")
