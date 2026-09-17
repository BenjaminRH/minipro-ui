"""Focused UI coverage for the collapsed native advanced options."""

from __future__ import annotations

import shlex
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from textual.containers import VerticalScroll
from textual.widgets import Button, Checkbox, Input, Select, Static

from minipro_ui.app import MiniproApp
from minipro_ui.demo import DemoBackend
from minipro_ui.models import AdvancedOptions, DeviceInfo, TuningOption
from minipro_ui.screens import ReviewScreen
from minipro_ui.settings import Settings


async def command(pilot: Any, name: str) -> None:
    await pilot.press("ctrl+p")
    await pilot.press(*name, "enter")
    await pilot.pause()


class AdvancedDemoBackend(DemoBackend):
    async def device_info(self, programmer: str, name: str) -> DeviceInfo:
        info = await super().device_info(programmer, name)
        return replace(
            info,
            tuning_options=(
                TuningOption("vcc", values=("3.3", "5.0")),
                TuningOption("speed", values=("1", "4")),
                TuningOption("pulse", minimum=1, maximum=100),
            ),
            supports_pin_check=True,
            supports_unprotect=True,
            supports_protect=True,
            supported_interfaces=("zif", "icsp"),
            supported_config_sections=("fuses", "lock"),
            supports_read_format=True,
            supports_size_override=True,
        )


class RegionDemoBackend(AdvancedDemoBackend):
    async def device_info(self, programmer: str, name: str) -> DeviceInfo:
        return replace(
            await super().device_info(programmer, name),
            regions=("code", "data", "config", "user", "calibration"),
        )


async def open_operation(pilot: Any, app: MiniproApp) -> None:
    await pilot.pause(0.4)
    await command(pilot, "Device setup")
    await pilot.press(*"W25Q32JV", "enter", "enter")
    await pilot.pause(0.2)


@pytest.mark.parametrize("size", [(80, 24), (120, 40)])
async def test_advanced_defaults_collapsed_and_review_command(
    size: tuple[int, int], tmp_path: Path
) -> None:
    app = MiniproApp(demo=True, backend=AdvancedDemoBackend(), settings=Settings())
    async with app.run_test(size=size) as pilot:
        await open_operation(pilot, app)
        panel = app.query_one("#advanced-options")
        assert not panel.query_one("#advanced-fields").display
        assert app.current_job().advanced == AdvancedOptions()

        operation_pane = app.query_one("#operate").query_one(VerticalScroll)
        operation_pane.scroll_to(y=100)
        await pilot.pause()
        await pilot.click("#advanced-toggle")
        app.query_one("#advanced-read-format", Select).value = "ihex"
        app.query_one("#advanced-tuning-speed", Select).value = "4"
        destination = tmp_path / "encoded.hex"
        app.query_one("#file", Input).value = str(destination)
        await command(pilot, "Review job")
        assert isinstance(app.screen, ReviewScreen)
        command_text = str(app.screen.query_one("#review-command", Static).content)
        assert "-f ihex" in command_text
        assert "-o speed=4" in command_text
        assert command_text == "$ " + shlex.join(app.screen.prepared.command)
        assert "Read format: IHEX" in str(
            app.screen.query_one("#advanced-review", Static).content
        )
        app.save_screenshot(filename=f"minipro-ui-advanced-{size[0]}.svg", path="/tmp")
        await pilot.press("escape")
        await command(pilot, "Program")
        app.query_one("#advanced-erase-mode", Select).value = "skip"
        app.query_one("#advanced-unprotect", Checkbox).value = True
        app.query_one("#advanced-protect", Checkbox).value = True
        app.query_one("#advanced-tuning-vcc", Select).value = "3.3"
        vcc = app.query_one("#advanced-tuning-vcc", Select)
        vcc.focus()
        vcc.scroll_visible(animate=False, force=True, top=True)
        await pilot.pause()
        assert vcc.region.intersection(app.screen.region).height > 0
        protect = app.query_one("#advanced-protect", Checkbox)
        protect.focus()
        protect.scroll_visible(animate=False, force=True, top=True)
        await pilot.pause()
        assert protect.region.intersection(app.screen.region).height > 0
        app.save_screenshot(
            filename=f"minipro-ui-final-advanced-{size[0]}.svg", path="/tmp"
        )


async def test_advanced_values_reset_and_hidden_options_do_not_leak(
    tmp_path: Path,
) -> None:
    app = MiniproApp(demo=True, backend=AdvancedDemoBackend(), settings=Settings())
    async with app.run_test() as pilot:
        await open_operation(pilot, app)
        panel = app.query_one("#advanced-options")
        panel.query_one("#advanced-toggle", Button).press()
        app.query_one("#advanced-read-format", Select).value = "srec"
        app.query_one("#operation", Select).value = "Program"
        await pilot.pause()
        assert app.current_job().advanced.read_format == "bin"
        app.query_one("#operation", Select).value = "Read / back up"
        await pilot.pause()
        assert app.current_job().advanced.read_format == "bin"
        assert not app.query_one("#advanced-tuning-vcc-row").display
        assert not panel.query_one("#advanced-unprotect").display


async def test_unsaved_interface_edit_preserves_advanced_draft() -> None:
    app = MiniproApp(demo=True, backend=AdvancedDemoBackend(), settings=Settings())
    async with app.run_test() as pilot:
        await open_operation(pilot, app)
        app.query_one("#advanced-options").query_one(
            "#advanced-read-format", Select
        ).value = "ihex"
        app.query_one("#interface", Select).value = "icsp"
        await pilot.pause()
        assert app.current_job().advanced.read_format == "ihex"


async def test_plain_device_hides_empty_advanced_panel_for_id_and_erase() -> None:
    app = MiniproApp(demo=True, settings=Settings())
    async with app.run_test() as pilot:
        await open_operation(pilot, app)
        await command(pilot, "Erase")
        assert not app.query_one("#advanced-options").display
        assert not app.query_one("#memory-help", Static).display
        await command(pilot, "Device setup")
        app.query_one("#search", Input).value = "AT28C256"
        await pilot.pause()
        await pilot.press("enter", "enter")
        await command(pilot, "Read chip ID")
        assert not app.query_one("#advanced-options").display
        assert not app.query_one("#memory-help", Static).display


async def test_region_help_follows_selected_memory() -> None:
    app = MiniproApp(demo=True, backend=RegionDemoBackend(), settings=Settings())
    async with app.run_test() as pilot:
        await open_operation(pilot, app)
        memory = app.query_one("#memory", Select)
        for value, phrase in (
            ("code", "main addressable"),
            ("data", "separate data"),
            ("config", "device settings"),
            ("user", "user/ID"),
            ("calibration", "factory"),
        ):
            memory.value = value
            await pilot.pause()
            assert phrase in str(app.query_one("#memory-help", Static).content)


async def test_invalid_pulse_is_reported_by_review(tmp_path: Path) -> None:
    app = MiniproApp(demo=True, backend=AdvancedDemoBackend(), settings=Settings())
    image = tmp_path / "image.bin"
    image.write_bytes(b"data")
    async with app.run_test() as pilot:
        await open_operation(pilot, app)
        await command(pilot, "Program")
        app.query_one("#advanced-options").query_one("#advanced-toggle", Button).press()
        app.query_one("#advanced-tuning-pulse", Input).value = "not-a-number"
        app.query_one("#file", Input).value = str(image)
        await command(pilot, "Review job")
        assert "finite integer" in str(app.query_one("#job-error", Static).content)


async def test_advanced_controls_lock_with_operation(tmp_path: Path) -> None:
    app = MiniproApp(demo=True, backend=AdvancedDemoBackend(), settings=Settings())
    async with app.run_test() as pilot:
        await open_operation(pilot, app)
        app.operation_started(app.current_job())
        assert app.configuration_locked
        assert app.query_one("#advanced-toggle").disabled
        assert app.query_one("#advanced-read-format").disabled
        app.operation_finished(app.current_job(), False)
        assert not app.configuration_locked
