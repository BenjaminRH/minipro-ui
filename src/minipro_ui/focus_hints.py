"""A transient numbered overlay for jumping to visible controls."""

from dataclasses import dataclass

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.errors import NoWidget
from textual.events import Key
from textual.geometry import Offset
from textual.screen import ModalScreen, Screen
from textual.widget import Widget
from textual.widgets import Static, Tab, Tabs


@dataclass(frozen=True)
class FocusTarget:
    """A visible control and the position of its number badge."""

    widget: Widget
    x: int
    y: int


def visible_targets(screen: Screen[object]) -> list[FocusTarget]:
    """Number visible, enabled controls in screen order, including individual tabs."""
    candidates = [
        widget
        for widget in screen.focus_chain
        if not isinstance(widget, (Tabs, VerticalScroll))
    ]
    candidates.extend(screen.query(Tab))
    targets = []
    for widget in candidates:
        if not widget.visible or widget.disabled or not widget.region:
            continue
        region = widget.region.intersection(screen.region)
        if not region:
            continue
        # Containers may extend beyond a scrolled viewport. Hit testing excludes
        # controls hidden by clipping or by another widget.
        x, y = region.x, region.y
        try:
            hit, _ = screen.get_widget_at(x, y)
        except NoWidget:
            continue
        if hit is not widget and widget not in hit.ancestors:
            continue
        targets.append(FocusTarget(widget, x, y))
    return sorted(targets, key=lambda target: (target.y, target.x))


class FocusHints(ModalScreen[None]):
    """Keep the previous screen visible while capturing numeric focus choices."""

    BINDINGS = [Binding("escape", "close", show=False)]
    DEFAULT_CSS = """
    FocusHints { background: #070d15 35%; align: left top; }
    FocusHints .focus-number {
        position: absolute; width: auto; height: 1;
        background: #ead39b; color: #16202a; text-style: bold;
    }
    FocusHints #focus-instructions {
        dock: bottom; height: 1; background: #182b32; color: #dce5ee;
        text-align: center;
    }
    """

    def __init__(self, source: Screen[object]) -> None:
        super().__init__()
        self.source = source
        self.previous_focus = source.focused
        self.targets = visible_targets(source)
        self.digits = ""

    def compose(self) -> ComposeResult:
        for number, target in enumerate(self.targets, start=1):
            badge = Static(f" {number} ", classes="focus-number")
            badge.styles.offset = Offset(target.x, target.y)
            yield badge
        yield Static(
            "Type a number to focus · Enter confirms · Escape returns",
            id="focus-instructions",
        )

    def on_key(self, event: Key) -> None:
        event.stop()
        digit = event.character or event.key.removeprefix("alt+")
        if event.key == "escape":
            self.action_close()
        elif len(digit) == 1 and digit in "0123456789":
            event.prevent_default()
            candidate = self.digits + digit
            matches = [
                number
                for number in range(1, len(self.targets) + 1)
                if str(number).startswith(candidate)
            ]
            if matches:
                self.digits = candidate
                if len(matches) == 1 and str(matches[0]) == candidate:
                    self.choose(matches[0])
                else:
                    self.query_one("#focus-instructions", Static).update(
                        f"Number: {self.digits} · Enter confirms · Escape returns"
                    )
        elif event.key == "enter" and self.digits:
            self.choose(int(self.digits))
        elif event.key == "backspace":
            self.digits = self.digits[:-1]
            self.query_one("#focus-instructions", Static).update(
                f"Number: {self.digits or '…'} · Enter confirms · Escape returns"
            )

    def choose(self, number: int) -> None:
        if not 1 <= number <= len(self.targets):
            return
        widget = self.targets[number - 1].widget
        self.dismiss()
        if not widget.is_mounted or widget.disabled:
            return
        if isinstance(widget, Tab):
            tabs = next(node for node in widget.ancestors if isinstance(node, Tabs))
            tabs.active = widget.id or ""
            tabs.focus()
        else:
            widget.focus()

    def action_close(self) -> None:
        self.dismiss()
        if self.previous_focus is not None and self.previous_focus.is_mounted:
            self.previous_focus.focus()
