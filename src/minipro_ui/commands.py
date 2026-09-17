"""Fuzzy command discovery with explanations and explicit availability."""

from dataclasses import dataclass

from rich.console import Group
from rich.rule import Rule
from rich.table import Table
from rich.text import Text
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.fuzzy import Matcher
from textual.screen import ModalScreen
from textual.widgets import Input, Label, OptionList, Static
from textual.widgets.option_list import Option

from .widgets import BottomBar


@dataclass(frozen=True)
class Command:
    """A named action and its current availability, shared by palette rendering."""

    name: str
    description: str
    action: str
    shortcut: str = ""
    reason: str = ""


class CommandScreen(ModalScreen[None]):
    """Search without changing the caller until a command is explicitly chosen."""

    BINDINGS = [
        Binding("escape", "close", show=False),
        Binding("down", "next_result", show=False),
        Binding("up", "previous_result", show=False),
    ]
    help_topic = "commands"

    def __init__(self, commands: list[Command]) -> None:
        super().__init__()
        self.commands = commands
        self.matches: list[Command] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog", classes="wide"):
            yield Label("COMMANDS", classes="eyebrow")
            yield Input(placeholder="Search commands…", id="command-search")
            yield OptionList(id="command-results")
            yield Static("↑↓ Choose · Enter Run · Esc Back", id="command-hint")
        yield BottomBar()

    def on_mount(self) -> None:
        self.search()
        self.query_one(Input).focus()

    @on(Input.Changed)
    def search(self) -> None:
        query = self.query_one(Input).value.strip()
        matcher = Matcher(query)
        scored = [
            (
                (
                    (10000 if c.name.casefold() == query.casefold() else 0)
                    + (1000 if c.name.casefold().startswith(query.casefold()) else 0)
                    + 2 * matcher.match(c.name)
                    + matcher.match(c.description)
                )
                if query
                else 0,
                i,
                c,
            )
            for i, c in enumerate(self.commands)
        ]
        self.matches = (
            [
                c
                for score, _, c in sorted(scored, key=lambda item: (-item[0], item[1]))
                if score > 0
            ]
            if query
            else self.commands
        )
        listing = self.query_one(OptionList)
        listing.clear_options()
        for command in self.matches:
            row = Table.grid(expand=True, padding=(0, 1))
            row.add_column(ratio=1)
            row.add_column(justify="right", no_wrap=True)
            row.add_row(
                Text(command.name, style="dim" if command.reason else "bold"),
                Text(command.shortcut, style="dim"),
            )
            row.add_row(Text(command.description), Text(""))
            if command.reason:
                row.add_row(Text(command.reason, style="yellow"), Text(""))
            listing.add_option(Option(Group(row, Rule(style="#435966"))))
        listing.highlighted = 0 if self.matches else None
        self.query_one("#command-hint", Static).update(
            "↑↓ Choose · Enter Run · Esc Back"
            if self.matches
            else "No matching commands. Try a shorter search."
        )

    def action_next_result(self) -> None:
        self.query_one(OptionList).action_cursor_down()

    def action_previous_result(self) -> None:
        self.query_one(OptionList).action_cursor_up()

    @on(Input.Submitted)
    @on(OptionList.OptionSelected)
    def activate(self) -> None:
        index = self.query_one(OptionList).highlighted
        if index is None:
            return
        command = self.matches[index]
        if command.reason:
            self.query_one("#command-hint", Static).update(command.reason)
            return
        self.dismiss()
        # Restore the caller before resolving actions and dialog button IDs.
        self.app.call_later(self.app.run_action, command.action)

    def action_close(self) -> None:
        self.dismiss()
