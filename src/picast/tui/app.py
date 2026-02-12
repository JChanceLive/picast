"""PiCast TUI - Textual terminal dashboard for controlling PiCast.

Run with `picast` command on your Mac to connect to the Pi server.
"""

import logging

from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, Label, Static, TextArea

from picast.tui.api_client import AsyncPiCastClient, PiCastAPIError
from picast.tui.widgets.controls import ControlsBar
from picast.tui.widgets.header_bar import HeaderBar
from picast.tui.widgets.library_list import LibraryList
from picast.tui.widgets.now_playing import NowPlaying
from picast.tui.widgets.playlist_list import PlaylistList
from picast.tui.widgets.queue_list import QueueList

logger = logging.getLogger(__name__)


class AddURLScreen(ModalScreen[str | None]):
    """Modal screen for adding a URL to the queue."""

    DEFAULT_CSS = """
    AddURLScreen {
        align: center middle;
    }
    AddURLScreen > Container {
        width: 70;
        height: auto;
        max-height: 7;
        border: thick $primary;
        background: $surface;
        padding: 1 2;
    }
    AddURLScreen Label {
        margin-bottom: 1;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
    ]

    def compose(self) -> ComposeResult:
        with Container():
            yield Label("Add URL to queue:")
            yield Input(placeholder="https://www.youtube.com/watch?v=...", id="url-input")

    def on_mount(self) -> None:
        self.query_one("#url-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        url = event.value.strip()
        if url:
            self.dismiss(url)
        else:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class HelpScreen(ModalScreen):
    """Help screen showing all keybindings."""

    DEFAULT_CSS = """
    HelpScreen {
        align: center middle;
    }
    HelpScreen > Container {
        width: 60;
        height: auto;
        max-height: 22;
        border: thick $primary;
        background: $surface;
        padding: 1 2;
    }
    HelpScreen .help-title {
        text-style: bold;
        text-align: center;
        margin-bottom: 1;
    }
    HelpScreen .help-line {
        height: 1;
    }
    HelpScreen .help-footer {
        text-align: center;
        margin-top: 1;
        color: $text-muted;
    }
    """

    BINDINGS = [
        Binding("escape", "dismiss_help", "Close"),
        Binding("question_mark", "dismiss_help", "Close"),
    ]

    HELP_LINES = [
        ("Space", "Toggle play/pause"),
        ("S", "Skip to next in queue"),
        ("A", "Add URL to queue"),
        ("Q", "Quit PiCast TUI"),
        ("", ""),
        ("+/=", "Volume up (5%)"),
        ("-", "Volume down (5%)"),
        (">", "Speed up (0.25x)"),
        ("<", "Speed down (0.25x)"),
        ("", ""),
        ("L", "Open history"),
        ("P", "Open collections"),
        ("Tab", "Switch device"),
        ("Up/Down", "Navigate queue"),
        ("D", "Remove selected from queue"),
        ("C", "Clear played items"),
        ("R", "Force refresh"),
    ]

    def compose(self) -> ComposeResult:
        with Container():
            yield Label("PiCast Keybindings", classes="help-title")
            for key, desc in self.HELP_LINES:
                if not key:
                    yield Label("", classes="help-line")
                else:
                    yield Label(f"  [{key:>10}]  {desc}", classes="help-line")
            yield Label("Press [?] or [Esc] to close", classes="help-footer")

    def action_dismiss_help(self) -> None:
        self.dismiss()


class LibraryScreen(ModalScreen):
    """Library browser screen."""

    DEFAULT_CSS = """
    LibraryScreen {
        align: center middle;
    }
    LibraryScreen > Container {
        width: 80;
        height: 24;
        border: thick $primary;
        background: $surface;
        padding: 1 2;
    }
    """

    BINDINGS = [
        Binding("escape", "dismiss_screen", "Close"),
        Binding("q", "queue_item", "Queue Selected"),
        Binding("f", "toggle_fav", "Toggle Favorite"),
        Binding("n", "edit_notes", "Notes"),
        Binding("d", "delete_item", "Delete"),
    ]

    def compose(self) -> ComposeResult:
        with Container():
            yield LibraryList()

    async def on_mount(self) -> None:
        await self._refresh()

    async def _refresh(self) -> None:
        api = self.app.api
        if api:
            try:
                items = await api.get_library()
                self.query_one(LibraryList).update_library(items)
            except Exception:
                pass

    async def action_dismiss_screen(self) -> None:
        self.dismiss()

    async def action_queue_item(self) -> None:
        item = self.query_one(LibraryList).get_selected_item()
        if item and self.app.api:
            try:
                await self.app.api.queue_library_item(item["id"])
                self.app.notify(f"Queued: {item.get('title', item['url'])}", timeout=2)
            except Exception:
                pass

    async def action_toggle_fav(self) -> None:
        item = self.query_one(LibraryList).get_selected_item()
        if item and self.app.api:
            try:
                await self.app.api.toggle_favorite(item["id"])
                await self._refresh()
            except Exception:
                pass

    async def action_edit_notes(self) -> None:
        item = self.query_one(LibraryList).get_selected_item()
        if item:
            self.app.push_screen(
                NotesScreen(item["id"], item.get("title", ""), item.get("notes", "")),
            )

    async def action_delete_item(self) -> None:
        item = self.query_one(LibraryList).get_selected_item()
        if item and self.app.api:
            try:
                await self.app.api.delete_library_item(item["id"])
                await self._refresh()
                self.app.notify("Deleted from library", timeout=2)
            except Exception:
                pass


class NotesScreen(ModalScreen):
    """Edit notes for a library item."""

    DEFAULT_CSS = """
    NotesScreen {
        align: center middle;
    }
    NotesScreen > Container {
        width: 70;
        height: 14;
        border: thick $accent;
        background: $surface;
        padding: 1 2;
    }
    NotesScreen Label {
        margin-bottom: 1;
    }
    NotesScreen TextArea {
        height: 8;
    }
    """

    BINDINGS = [
        Binding("escape", "save_and_close", "Save & Close"),
    ]

    def __init__(self, library_id: int, title: str, notes: str):
        super().__init__()
        self._library_id = library_id
        self._title = title
        self._notes = notes

    def compose(self) -> ComposeResult:
        with Container():
            yield Label(f"Notes: {self._title[:50]}")
            yield TextArea(self._notes, id="notes-area")
            yield Label("[Esc] Save and close", classes="help-footer")

    def on_mount(self) -> None:
        self.query_one("#notes-area", TextArea).focus()

    async def action_save_and_close(self) -> None:
        text = self.query_one("#notes-area", TextArea).text
        if self.app.api:
            try:
                await self.app.api.update_notes(self._library_id, text)
                self.app.notify("Notes saved", timeout=2)
            except Exception:
                pass
        self.dismiss()


class PlaylistScreen(ModalScreen):
    """Playlist browser screen."""

    DEFAULT_CSS = """
    PlaylistScreen {
        align: center middle;
    }
    PlaylistScreen > Container {
        width: 70;
        height: 20;
        border: thick $secondary;
        background: $surface;
        padding: 1 2;
    }
    """

    BINDINGS = [
        Binding("escape", "dismiss_screen", "Close"),
        Binding("a", "create_playlist", "New Collection"),
        Binding("q", "queue_playlist", "Queue All"),
        Binding("d", "delete_playlist", "Delete"),
    ]

    def compose(self) -> ComposeResult:
        with Container():
            yield PlaylistList()

    async def on_mount(self) -> None:
        await self._refresh()

    async def _refresh(self) -> None:
        api = self.app.api
        if api:
            try:
                playlists = await api.get_playlists()
                self.query_one(PlaylistList).update_playlists(playlists)
            except Exception:
                pass

    async def action_dismiss_screen(self) -> None:
        self.dismiss()

    async def action_queue_playlist(self) -> None:
        pl = self.query_one(PlaylistList).get_selected_playlist()
        if pl and self.app.api:
            try:
                result = await self.app.api.queue_playlist(pl["id"])
                count = result.get("queued", 0)
                self.app.notify(f"Queued {count} items from '{pl['name']}'", timeout=3)
            except Exception:
                pass

    def action_create_playlist(self) -> None:
        self.app.push_screen(
            CreatePlaylistScreen(),
            self._on_playlist_created,
        )

    async def _on_playlist_created(self, name: str | None) -> None:
        if name and self.app.api:
            try:
                await self.app.api.create_playlist(name)
                await self._refresh()
                self.app.notify(f"Created playlist: {name}", timeout=2)
            except Exception:
                self.app.notify("Failed to create playlist", severity="error", timeout=3)

    async def action_delete_playlist(self) -> None:
        pl = self.query_one(PlaylistList).get_selected_playlist()
        if pl and self.app.api:
            try:
                await self.app.api.delete_playlist(pl["id"])
                await self._refresh()
                self.app.notify(f"Deleted playlist: {pl['name']}", timeout=2)
            except Exception:
                pass


class CreatePlaylistScreen(ModalScreen[str | None]):
    """Modal for creating a new playlist."""

    DEFAULT_CSS = """
    CreatePlaylistScreen {
        align: center middle;
    }
    CreatePlaylistScreen > Container {
        width: 60;
        height: auto;
        max-height: 7;
        border: thick $secondary;
        background: $surface;
        padding: 1 2;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
    ]

    def compose(self) -> ComposeResult:
        with Container():
            yield Label("Create collection:")
            yield Input(placeholder="Collection name...", id="pl-name-input")

    def on_mount(self) -> None:
        self.query_one("#pl-name-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        name = event.value.strip()
        self.dismiss(name if name else None)

    def action_cancel(self) -> None:
        self.dismiss(None)


class DeviceScreen(ModalScreen[tuple[str, int] | None]):
    """Device switcher screen - select which Pi to control."""

    DEFAULT_CSS = """
    DeviceScreen {
        align: center middle;
    }
    DeviceScreen > Container {
        width: 60;
        height: auto;
        max-height: 18;
        border: thick $primary;
        background: $surface;
        padding: 1 2;
    }
    DeviceScreen .dev-title {
        text-style: bold;
        text-align: center;
        margin-bottom: 1;
    }
    DeviceScreen .dev-item {
        height: 1;
        padding: 0 1;
    }
    DeviceScreen .dev-item.selected {
        background: $primary;
    }
    DeviceScreen .dev-footer {
        text-align: center;
        margin-top: 1;
        color: $text-muted;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("up", "nav_up", "Up", show=False),
        Binding("down", "nav_down", "Down", show=False),
        Binding("enter", "select_device", "Select", show=False),
    ]

    def __init__(self, devices: list[tuple[str, str, int]], current_host: str, current_port: int):
        super().__init__()
        self._devices = devices
        self._current = (current_host, current_port)
        self._selected_idx = 0
        # Find current device index
        for i, (name, host, port) in enumerate(devices):
            if host == current_host and port == current_port:
                self._selected_idx = i
                break

    def compose(self) -> ComposeResult:
        with Container():
            yield Label("Switch Device", classes="dev-title")
            for i, (name, host, port) in enumerate(self._devices):
                marker = " *" if (host, port) == self._current else ""
                yield Label(
                    f"  {name} ({host}:{port}){marker}",
                    id=f"dev-{i}",
                    classes="dev-item",
                )
            yield Label("[Enter] Select  [Esc] Cancel", classes="dev-footer")

    def on_mount(self) -> None:
        self._highlight()

    def _highlight(self) -> None:
        for i in range(len(self._devices)):
            label = self.query_one(f"#dev-{i}", Label)
            if i == self._selected_idx:
                label.add_class("selected")
            else:
                label.remove_class("selected")

    def action_nav_up(self) -> None:
        if self._selected_idx > 0:
            self._selected_idx -= 1
            self._highlight()

    def action_nav_down(self) -> None:
        if self._selected_idx < len(self._devices) - 1:
            self._selected_idx += 1
            self._highlight()

    def action_select_device(self) -> None:
        name, host, port = self._devices[self._selected_idx]
        self.dismiss((host, port))

    def action_cancel(self) -> None:
        self.dismiss(None)


class PiCastApp(App):
    """PiCast terminal UI."""

    TITLE = "PiCast"
    CSS_PATH = "picast.tcss"

    BINDINGS = [
        Binding("space", "toggle_pause", "Play/Pause", show=False),
        Binding("s", "skip", "Skip", show=False),
        Binding("a", "add_url", "Add URL", show=False),
        Binding("q", "quit_app", "Quit", show=False),
        Binding("plus,equal", "volume_up", "Vol+", show=False),
        Binding("minus", "volume_down", "Vol-", show=False),
        Binding("greater_than_sign,period", "speed_up", "Speed+", show=False),
        Binding("less_than_sign,comma", "speed_down", "Speed-", show=False),
        Binding("d", "remove_selected", "Remove", show=False),
        Binding("c", "clear_played", "Clear Played", show=False),
        Binding("r", "refresh", "Refresh", show=False),
        Binding("l", "show_history", "History", show=False),
        Binding("p", "show_collections", "Collections", show=False),
        Binding("tab", "switch_device", "Switch Device", show=False),
        Binding("question_mark", "show_help", "Help", show=False),
    ]

    def __init__(
        self,
        host: str = "raspberrypi.local",
        port: int = 5000,
        devices: list[tuple[str, str, int]] | None = None,
    ):
        super().__init__()
        self.host = host
        self.port = port
        self.devices = devices or []
        self.api: AsyncPiCastClient | None = None
        self._poll_active = True
        self._last_volume = 100
        self._last_speed = 1.0

    def compose(self) -> ComposeResult:
        yield HeaderBar()
        with Vertical(id="main"):
            yield NowPlaying()
            yield QueueList()
        yield ControlsBar()

    async def on_mount(self) -> None:
        self.api = AsyncPiCastClient(self.host, self.port)
        header = self.query_one(HeaderBar)
        header.device_name = f"{self.host}:{self.port}"
        self._poll_status()

    async def on_unmount(self) -> None:
        self._poll_active = False
        if self.api:
            await self.api.close()

    @work(exclusive=True, group="poll")
    async def _poll_status(self) -> None:
        """Poll the server for status updates every second."""
        import asyncio

        while self._poll_active:
            try:
                status = await self.api.get_status()
                queue = await self.api.get_queue()

                self.query_one(HeaderBar).connected = True
                self.query_one(NowPlaying).update_status(status)
                self.query_one(QueueList).update_queue(queue)

                self._last_volume = int(status.get("volume", 100) or 100)
                self._last_speed = status.get("speed", 1.0) or 1.0

            except PiCastAPIError:
                self.query_one(HeaderBar).connected = False
            except Exception:
                self.query_one(HeaderBar).connected = False

            await asyncio.sleep(1)

    @work(exclusive=True, group="command")
    async def _send_command(self, coro) -> None:
        """Send a command to the API and handle errors."""
        try:
            await coro
        except PiCastAPIError as e:
            self.notify(str(e), severity="error", timeout=3)
        except Exception as e:
            self.notify(f"Error: {e}", severity="error", timeout=3)

    # --- Actions ---

    async def action_toggle_pause(self) -> None:
        if self.api:
            self._send_command(self.api.toggle())

    async def action_skip(self) -> None:
        if self.api:
            self._send_command(self.api.skip())
            self.notify("Skipped", timeout=2)

    def action_add_url(self) -> None:
        self.push_screen(AddURLScreen(), self._on_url_added)

    def _on_url_added(self, url: str | None) -> None:
        if url and self.api:
            self._add_url_to_queue(url)

    @work(exclusive=True, group="command")
    async def _add_url_to_queue(self, url: str) -> None:
        try:
            result = await self.api.add_to_queue(url)
            title = result.get("title", "") or url
            self.notify(f"Added: {title}", timeout=3)
        except PiCastAPIError as e:
            self.notify(str(e), severity="error", timeout=3)

    def action_quit_app(self) -> None:
        self.exit()

    async def action_volume_up(self) -> None:
        if self.api:
            new_vol = min(100, self._last_volume + 5)
            self._last_volume = new_vol
            self._send_command(self.api.set_volume(new_vol))

    async def action_volume_down(self) -> None:
        if self.api:
            new_vol = max(0, self._last_volume - 5)
            self._last_volume = new_vol
            self._send_command(self.api.set_volume(new_vol))

    async def action_speed_up(self) -> None:
        if self.api:
            new_speed = min(4.0, self._last_speed + 0.25)
            self._last_speed = new_speed
            self._send_command(self.api.set_speed(new_speed))

    async def action_speed_down(self) -> None:
        if self.api:
            new_speed = max(0.25, self._last_speed - 0.25)
            self._last_speed = new_speed
            self._send_command(self.api.set_speed(new_speed))

    async def action_remove_selected(self) -> None:
        if self.api:
            queue_list = self.query_one(QueueList)
            item_id = queue_list.get_selected_item_id()
            if item_id is not None:
                self._send_command(self.api.remove_from_queue(item_id))
                self.notify("Removed from queue", timeout=2)

    async def action_clear_played(self) -> None:
        if self.api:
            self._send_command(self.api.clear_played())
            self.notify("Cleared played items", timeout=2)

    async def action_refresh(self) -> None:
        self.notify("Refreshing...", timeout=1)

    def action_show_history(self) -> None:
        self.push_screen(LibraryScreen())

    def action_show_collections(self) -> None:
        self.push_screen(PlaylistScreen())

    def action_switch_device(self) -> None:
        if not self.devices:
            self.notify("No other devices configured", timeout=2)
            return
        self.push_screen(
            DeviceScreen(self.devices, self.host, self.port),
            self._on_device_selected,
        )

    @work(exclusive=True, group="command")
    async def _on_device_selected(self, result: tuple[str, int] | None) -> None:
        if result is None:
            return
        host, port = result
        if host == self.host and port == self.port:
            return

        self.host = host
        self.port = port

        # Reconnect API client
        if self.api:
            await self.api.close()
        self.api = AsyncPiCastClient(host, port)

        header = self.query_one(HeaderBar)
        header.device_name = f"{host}:{port}"
        header.connected = False

        self.notify(f"Switched to {host}:{port}", timeout=2)

    def action_show_help(self) -> None:
        self.push_screen(HelpScreen())
