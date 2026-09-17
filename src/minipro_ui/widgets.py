"""Shared status and navigation chrome for the workbench and its dialogs."""

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.events import Key
from textual.widgets import DirectoryTree, Input, OptionList, Static


class BottomBar(Horizontal):
    """Keep hardware state distinct from the four global keyboard actions."""

    def compose(self) -> ComposeResult:
        yield Static("Checking…", classes="connection", markup=False)
        yield Static("", classes="shortcuts", markup=False)

    def update_shortcuts(self) -> None:
        """Reserve the rendered hint width and distinguish keys from action names."""
        text = Text(no_wrap=True)
        for key, action in (
            ("Ctrl+?", "Help"),
            ("Ctrl+p", "Commands"),
            ("Alt", "Hints"),
            ("Ctrl+q", "Quit"),
        ):
            if text:
                text.append("  ")
            text.append(key, style="bold #84d8c1")
            if self.size.width >= 70:
                text.append(f" {action}", style="#becdd8")
        hints = self.query_one(".shortcuts", Static)
        hints.update(text)
        hints.styles.width = text.cell_len

    def on_resize(self) -> None:
        self.update_shortcuts()
        update = getattr(self.app, "update_status", None)
        if update:
            self.call_after_refresh(update)

    def on_mount(self) -> None:
        self.update_shortcuts()
        update = getattr(self.app, "update_status", None)
        self.query_one(
            ".shortcuts", Static
        ).tooltip = (
            "Ctrl+? Help · Ctrl+p Commands · Alt / Alt+space Focus hints · Ctrl+q Quit"
        )
        if update:
            self.call_after_refresh(update)


class CatalogList(OptionList, can_focus=False):
    """Keep search focus while supporting explicit clicks and keyboard selection."""

    def on_click(self) -> None:
        self.screen.query_one("#search", Input).focus()


class CatalogSearch(Input):
    """Browse suggestions with arrows while retaining editable search focus."""

    def on_key(self, event: Key) -> None:
        if event.key in ("up", "down"):
            event.stop()
            event.prevent_default()
            listing = self.app.query_one(CatalogList)
            if event.key == "up":
                listing.action_cursor_up()
            else:
                listing.action_cursor_down()


class FileTree(DirectoryTree):
    """Use conventional branch navigation instead of horizontal arrow scrolling."""

    def on_key(self, event: Key) -> None:
        if event.key not in ("left", "right") or self.cursor_node is None:
            return
        event.stop()
        event.prevent_default()
        node = self.cursor_node
        if event.key == "right":
            if not node.is_expanded and node.allow_expand:
                node.expand()
            elif node.children:
                self.move_cursor(node.children[0])
        elif node.is_expanded:
            node.collapse()
        elif node.parent is not None:
            self.move_cursor(node.parent)
