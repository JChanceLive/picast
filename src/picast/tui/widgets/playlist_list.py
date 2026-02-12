"""Playlist list widget - displays named playlists."""

from textual.app import ComposeResult
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Label, ListItem, ListView


class PlaylistList(Widget):
    """Displays playlists for browsing and queueing."""

    DEFAULT_CSS = """
    PlaylistList {
        height: 1fr;
        padding: 0 1;
    }
    PlaylistList .pl-header {
        text-style: bold;
        height: 1;
        margin-bottom: 0;
    }
    PlaylistList ListView {
        height: 1fr;
    }
    PlaylistList ListItem {
        height: 1;
        padding: 0 1;
    }
    PlaylistList .pl-empty {
        text-style: italic;
        color: $text-muted;
        text-align: center;
        margin: 1 0;
    }
    """

    playlists: reactive[list] = reactive(list, always_update=True)

    def compose(self) -> ComposeResult:
        yield Label("Collections", id="pl-header", classes="pl-header")
        yield ListView(id="pl-list")
        yield Label("No collections yet", id="pl-empty", classes="pl-empty")

    def update_playlists(self, items: list[dict]) -> None:
        self.playlists = items

    def watch_playlists(self, items: list) -> None:
        listview = self.query_one("#pl-list", ListView)
        empty_label = self.query_one("#pl-empty", Label)
        header = self.query_one("#pl-header", Label)

        header.update(f"Collections ({len(items)})")
        listview.clear()

        if not items:
            empty_label.display = True
            listview.display = False
            return

        empty_label.display = False
        listview.display = True

        for idx, pl in enumerate(items):
            name = pl.get("name", "Untitled")
            count = pl.get("item_count", 0)
            desc = pl.get("description", "")

            line = f"  {idx + 1:2d}. {name} ({count} items)"
            if desc:
                line += f" - {desc[:30]}"

            listview.append(ListItem(Label(line)))

    def get_selected_playlist(self) -> dict | None:
        listview = self.query_one("#pl-list", ListView)
        if listview.index is not None and listview.index < len(self.playlists):
            return self.playlists[listview.index]
        return None
