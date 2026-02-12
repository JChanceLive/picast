"""Library list widget - displays the video library with search and browse."""

from textual.app import ComposeResult
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Label, ListItem, ListView

SOURCE_TAGS = {
    "youtube": "[YT]",
    "local": "[Local]",
    "twitch": "[Twitch]",
}


class LibraryList(Widget):
    """Displays the video library."""

    DEFAULT_CSS = """
    LibraryList {
        height: 1fr;
        padding: 0 1;
    }
    LibraryList .ll-header {
        text-style: bold;
        height: 1;
        margin-bottom: 0;
    }
    LibraryList ListView {
        height: 1fr;
    }
    LibraryList ListItem {
        height: 1;
        padding: 0 1;
    }
    LibraryList .ll-empty {
        text-style: italic;
        color: $text-muted;
        text-align: center;
        margin: 1 0;
    }
    LibraryList ListItem.favorite {
        color: $warning;
    }
    """

    library_items: reactive[list] = reactive(list, always_update=True)

    def compose(self) -> ComposeResult:
        yield Label("History (0 videos)", id="ll-header", classes="ll-header")
        yield ListView(id="ll-list")
        yield Label(
            "History is empty - videos are saved automatically after playing",
            id="ll-empty", classes="ll-empty",
        )

    def update_library(self, items: list[dict]) -> None:
        self.library_items = items

    def watch_library_items(self, items: list) -> None:
        listview = self.query_one("#ll-list", ListView)
        empty_label = self.query_one("#ll-empty", Label)
        header = self.query_one("#ll-header", Label)

        header.update(f"History ({len(items)} videos)")

        listview.clear()

        if not items:
            empty_label.display = True
            listview.display = False
            return

        empty_label.display = False
        listview.display = True

        for idx, item in enumerate(items):
            title = item.get("title") or item.get("url", "Unknown")
            source = SOURCE_TAGS.get(item.get("source_type", ""), "")
            fav = "*" if item.get("favorite") else " "
            plays = item.get("play_count", 0)

            max_len = 45
            if len(title) > max_len:
                title = title[:max_len - 3] + "..."

            line = f"{fav} {idx + 1:2d}. {title}  {source}  x{plays}"

            li = ListItem(Label(line))
            if item.get("favorite"):
                li.add_class("favorite")
            listview.append(li)

    def get_selected_item(self) -> dict | None:
        listview = self.query_one("#ll-list", ListView)
        if listview.index is not None and listview.index < len(self.library_items):
            return self.library_items[listview.index]
        return None
