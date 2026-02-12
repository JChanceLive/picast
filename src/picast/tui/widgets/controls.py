"""Controls bar widget - shows keybindings at the bottom of the screen."""

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.widget import Widget
from textual.widgets import Label


class ControlsBar(Widget):
    """Displays available keybindings in a compact footer bar."""

    DEFAULT_CSS = """
    ControlsBar {
        dock: bottom;
        height: 2;
        padding: 0 1;
        background: $surface;
    }
    ControlsBar .cb-row {
        height: 1;
        width: 1fr;
    }
    ControlsBar .cb-key {
        text-style: bold;
        color: $accent;
        width: auto;
    }
    ControlsBar .cb-desc {
        color: $text;
        width: auto;
        margin-right: 2;
    }
    """

    def compose(self) -> ComposeResult:
        with Horizontal(classes="cb-row"):
            yield Label("[Space]", classes="cb-key")
            yield Label("Play/Pause", classes="cb-desc")
            yield Label("[S]", classes="cb-key")
            yield Label("Skip", classes="cb-desc")
            yield Label("[A]", classes="cb-key")
            yield Label("Add URL", classes="cb-desc")
            yield Label("[Q]", classes="cb-key")
            yield Label("Quit", classes="cb-desc")
        with Horizontal(classes="cb-row"):
            yield Label("[+/-]", classes="cb-key")
            yield Label("Volume", classes="cb-desc")
            yield Label("[</>]", classes="cb-key")
            yield Label("Speed", classes="cb-desc")
            yield Label("[D]", classes="cb-key")
            yield Label("Remove", classes="cb-desc")
            yield Label("[C]", classes="cb-key")
            yield Label("Clear Played", classes="cb-desc")
            yield Label("[?]", classes="cb-key")
            yield Label("Help", classes="cb-desc")
