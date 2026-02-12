"""Queue list widget - displays the playback queue with status indicators."""

from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Label, ListItem, ListView, Static


SOURCE_TAGS = {
    "youtube": "[YT]",
    "local": "[Local]",
    "twitch": "[Twitch]",
}

STATUS_ICONS = {
    "pending": "  ",
    "playing": ">>",
    "played": "ok",
    "skipped": "--",
}


class QueueList(Widget):
    """Displays the playback queue with status indicators."""

    DEFAULT_CSS = """
    QueueList {
        height: 1fr;
        padding: 0 1;
    }
    QueueList .ql-header {
        text-style: bold;
        height: 1;
        margin-bottom: 0;
    }
    QueueList ListView {
        height: 1fr;
    }
    QueueList ListItem {
        height: 1;
        padding: 0 1;
    }
    QueueList .ql-empty {
        text-style: italic;
        color: $text-muted;
        text-align: center;
        margin: 1 0;
    }
    QueueList ListItem.playing {
        background: $primary-background;
    }
    QueueList ListItem.played {
        color: $text-muted;
    }
    """

    queue_items: reactive[list] = reactive(list, always_update=True)

    def compose(self) -> ComposeResult:
        yield Label("Queue (0 pending)", id="ql-header", classes="ql-header")
        yield ListView(id="ql-list")
        yield Label("Queue is empty - press [bold]A[/bold] to add a URL", id="ql-empty", classes="ql-empty")

    def update_queue(self, items: list[dict]) -> None:
        """Update the queue display from API response."""
        self.queue_items = items

    def watch_queue_items(self, items: list) -> None:
        listview = self.query_one("#ql-list", ListView)
        empty_label = self.query_one("#ql-empty", Label)
        header = self.query_one("#ql-header", Label)

        pending_count = sum(1 for i in items if i.get("status") == "pending")
        header.update(f"Queue ({pending_count} pending)")

        # Clear and rebuild list
        listview.clear()

        if not items:
            empty_label.display = True
            listview.display = False
            return

        empty_label.display = False
        listview.display = True

        for idx, item in enumerate(items):
            status = item.get("status", "pending")
            icon = STATUS_ICONS.get(status, "  ")
            title = item.get("title") or item.get("url", "Unknown")
            source = SOURCE_TAGS.get(item.get("source_type", ""), "")

            # Truncate title if too long
            max_len = 50
            if len(title) > max_len:
                title = title[:max_len - 3] + "..."

            line = f"{idx + 1:2d}. {icon} {title}  {source}"

            li = ListItem(Label(line))
            if status == "playing":
                li.add_class("playing")
            elif status in ("played", "skipped"):
                li.add_class("played")

            listview.append(li)

    def get_selected_item_id(self) -> int | None:
        """Get the queue item ID of the currently highlighted item."""
        listview = self.query_one("#ql-list", ListView)
        if listview.index is not None and listview.index < len(self.queue_items):
            return self.queue_items[listview.index].get("id")
        return None
