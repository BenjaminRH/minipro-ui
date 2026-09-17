"""Focus hints must never activate controls or lose the previous focus."""

from textual.app import App, ComposeResult
from textual.containers import Vertical
from textual.widgets import Button

from minipro_ui.focus_hints import FocusHints


class HintWorkbench(App):
    def compose(self) -> ComposeResult:
        with Vertical():
            for number in range(1, 13):
                yield Button(f"Control {number}", id=f"control-{number}")


async def test_multi_digit_target_focuses_without_activation():
    app = HintWorkbench()
    async with app.run_test(size=(80, 45)) as pilot:
        previous = app.focused
        app.push_screen(FocusHints(app.screen))
        await pilot.pause()
        assert len(app.screen.targets) == 12
        await pilot.press("1")
        assert isinstance(app.screen, FocusHints)
        await pilot.press("escape")
        assert app.focused is previous
        app.push_screen(FocusHints(app.screen))
        await pilot.pause()
        await pilot.press("alt+1", "alt+2")
        assert not isinstance(app.screen, FocusHints)
        assert app.focused is app.query_one("#control-12")
        assert not app.query_one("#control-12").has_class("-active")
