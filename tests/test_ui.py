"""Keyboard journeys and state transitions for the simplified workbench."""

import asyncio
import shlex
import threading
from pathlib import Path

import pytest
from textual.widgets import Button, Input, Link, Select, Static, TabbedContent

import minipro_ui.xgpro_images as xgpro_images
from minipro_ui.app import MiniproApp
from minipro_ui.commands import CommandScreen
from minipro_ui.demo import DemoBackend
from minipro_ui.models import Operation
from minipro_ui.screens import (
    ExtractImagesScreen,
    FileScreen,
    HelpScreen,
    ReviewScreen,
    RunScreen,
)
from minipro_ui.settings import Settings


async def command(pilot, name):
    """Invoke a command exactly as a keyboard user would."""
    await pilot.press("ctrl+p")
    await pilot.press(*name, "enter")
    await pilot.pause()


@pytest.mark.parametrize("size", [(80, 24), (120, 40)])
async def test_keyboard_backup_and_memory_browser(size, tmp_path):
    app = MiniproApp(demo=True, settings=Settings())
    destination = tmp_path / "backup.bin"
    async with app.run_test(size=size) as pilot:
        await pilot.pause()
        await command(pilot, "Device setup")
        # Search stays focused while browsing matches.
        await pilot.press(*"AT28C256", "enter", "enter")
        assert app.device_name == "AT28C256@DIP28"
        await command(pilot, "Choose file")
        assert isinstance(app.screen, FileScreen)
        await pilot.press(*str(destination), "enter")
        await command(pilot, "Review job")
        assert isinstance(app.screen, ReviewScreen)
        preview = str(app.screen.query_one("#review-command", Static).content)
        assert preview == "$ " + shlex.join(app.screen.prepared.command)
        staging_path = app.screen.prepared.execution_job.path.parent
        assert not destination.exists()
        await pilot.press("escape")
        assert not staging_path.exists()
        assert not destination.exists()
        await command(pilot, "Review job")
        await command(pilot, "Run job")
        for _ in range(30):
            await pilot.pause(0.05)
            if isinstance(app.screen, RunScreen) and not app.screen.running:
                break
        assert destination.read_bytes() == b"\xff" * 32768
        assert app.operation_state == "IDLE"
        assert not app.configuration_locked
        await command(pilot, "Done")
        assert "DEMO simulation" in app.session_log.text
        assert "-r" in app.session_log.text
        await command(pilot, "Memory browser")
        assert app.query_one(TabbedContent).active == "memory-tab"
        assert app.memory_path == destination
        assert app.query_one("#memory-log").virtual_size.height > size[1]
        assert not app.query("#memory-prev, #memory-next")


@pytest.mark.parametrize("operation", list(Operation))
async def test_operation_has_only_relevant_fields(operation):
    app = MiniproApp(demo=True, settings=Settings())
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        await command(pilot, "Device setup")
        name = "7400" if operation == Operation.TEST else "W25Q32JV"
        await pilot.press(*name, "enter", "enter")
        await command(pilot, operation.value)
        assert app.query_one(TabbedContent).active == "operate"
        assert app.query_one("#file-fields").display == operation.needs_file
        assert app.query_one("#memory-fields").display == (
            operation not in (Operation.ID, Operation.ERASE, Operation.TEST)
        )
        assert app.query_one("#interface").ancestors[1].id == "setup"


async def test_palette_fuzzy_search_help_and_focus_restore():
    app = MiniproApp(demo=True, settings=Settings())
    async with app.run_test() as pilot:
        await pilot.pause()
        await command(pilot, "Device setup")
        await pilot.press(*"AT28")
        focused = app.focused
        await pilot.press("ctrl+p")
        assert isinstance(app.screen, CommandScreen)
        names = {c.name for c in app.screen.commands}
        assert not names.intersection(
            {"Inspect file", "Compare files", "Save project", "Open project"}
        )
        await pilot.press(*"rdchpid")
        assert app.screen.matches[0].name == "Read chip ID"
        await pilot.press("ctrl+question_mark")
        assert isinstance(app.screen, HelpScreen)
        await pilot.press("escape")
        assert app.screen.query_one(Input).value == "rdchpid"
        await pilot.press("escape")
        assert app.focused is focused
        assert app.query_one("#search", Input).value == "AT28"
        await pilot.press("question_mark")
        assert not isinstance(app.screen, HelpScreen)
        assert app.query_one("#search", Input).value == "AT28?"


async def test_unavailable_command_explains_and_does_not_run():
    app = MiniproApp(demo=True, settings=Settings())
    async with app.run_test() as pilot:
        await pilot.pause()
        await command(pilot, "Review job")
        assert isinstance(app.screen, CommandScreen)
        assert "Select a device" in str(
            app.screen.query_one("#command-hint", Static).content
        )
        await pilot.press("escape")
        assert not isinstance(app.screen, CommandScreen)


async def test_refresh_preserves_selected_device_and_updates_connection():
    class PlugBackend(DemoBackend):
        attached = True

        async def connected(self):
            return ["T48"] if self.attached else []

    backend = PlugBackend()
    app = MiniproApp(demo=True, backend=backend, settings=Settings())
    async with app.run_test() as pilot:
        await pilot.pause()
        await command(pilot, "Device setup")
        await pilot.press("enter", "enter")
        selected = app.device_name
        await command(pilot, "Refresh connection")
        assert app.device_name == selected
        backend.attached = False
        app.poll_connection()
        await pilot.pause()
        assert app.device_name == ""
        assert "NO PROGRAMMER" in str(app.query_one(".connection", Static).content)


async def test_highlight_metadata_before_selection():
    app = MiniproApp(demo=True, settings=Settings())
    async with app.run_test() as pilot:
        await pilot.pause()
        assert "AT28C256" in str(app.query_one("#device-info", Static).content)
        assert app.device_name == ""
        await command(pilot, "Device setup")
        await pilot.press("down")
        await pilot.pause()
        assert "W25Q32JV" in str(app.query_one("#device-info", Static).content)
        assert app.device_name == ""
        await pilot.press("enter")
        assert app.device_name == ""
        assert app.draft_device_name == "W25Q32JV@SOIC8"
        await pilot.press("enter")
        assert app.device_name == "W25Q32JV@SOIC8"


async def test_history_has_save_at_bottom_and_exports(tmp_path):
    app = MiniproApp(demo=True, settings=Settings())
    app.session_log.command(["minipro", "-V"])
    app.session_log.output("stdout", "minipro version 0.7.4\n")
    destination = tmp_path / "session.log"
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        await command(pilot, "Log")
        assert app.query_one(TabbedContent).active == "log-tab"
        assert app.query_one("#save-log-file", Button).region.bottom <= 22
        await command(pilot, "Save log to file")
        await pilot.press(*str(destination), "enter")
        assert destination.read_text() == app.session_log.text
        assert app.query_one(TabbedContent).active == "log-tab"


async def test_cancel_button_stops_job_but_ctrl_c_does_not(tmp_path):
    class SlowBackend(DemoBackend):
        cancelled = False

        async def execute(self, job, output):
            try:
                await asyncio.sleep(100)
            except asyncio.CancelledError:
                self.cancelled = True
                raise

    backend = SlowBackend()
    app = MiniproApp(demo=True, backend=backend, settings=Settings())
    async with app.run_test() as pilot:
        await pilot.pause()
        await command(pilot, "Device setup")
        await pilot.press(*"W25Q32JV", "enter", "enter")
        await command(pilot, "Read chip ID")
        await command(pilot, "Review job")
        await command(pilot, "Run job")
        assert app.operation_state == "READING ID"
        assert app.configuration_locked
        assert app.query_one("#programmer").disabled
        assert app.query_one("#interface").disabled
        assert app.query_one("#search").disabled
        assert app.query_one(TabbedContent).get_tab("setup").disabled
        selected = app.device_name
        app.device_selected("7400")
        app.select_programmer("")
        assert app.device_name == selected
        assert app.selected_programmer == "T48"
        await pilot.press("ctrl+question_mark", "ctrl+c")
        await pilot.pause()
        assert not backend.cancelled
        await pilot.press("escape", "ctrl+c")
        assert not backend.cancelled
        await pilot.click("#stop")
        await pilot.pause()
        assert backend.cancelled
        assert not app.configuration_locked
        assert not app.query_one("#search").disabled
        assert app.operation_state == "IDLE"
        assert isinstance(app.screen, RunScreen)
        assert not app.screen.running


@pytest.mark.parametrize("save", [False, True])
async def test_picker_rejects_directory_and_cancels_without_changing_input(
    save, tmp_path
):
    app = MiniproApp(demo=True, settings=Settings())
    async with app.run_test() as pilot:
        await pilot.pause()
        app.push_screen(FileScreen(save=save))
        await pilot.pause()
        await pilot.press(*str(tmp_path), "enter")
        assert isinstance(app.screen, FileScreen)
        await pilot.press("escape")
        assert app.query_one("#file", Input).value == ""


async def test_settings_exposes_every_persisted_field():
    app = MiniproApp(demo=True, settings=Settings())
    async with app.run_test() as pilot:
        await pilot.pause()
        await command(pilot, "Settings")
        assert app.query_one(TabbedContent).active == "preferences"
        await pilot.press(*"/custom/minipro")
        await command(pilot, "Save settings")
        await pilot.pause()
        assert app.settings.executable == "/custom/minipro"
        assert not app.query("#preferred-programmer, #recent-devices")
        assert app.query_one("#save-log")
        assert app.query_one("#log-path")


async def test_mouse_hover_preserves_device_preview():
    app = MiniproApp(demo=True, settings=Settings())
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        await command(pilot, "Device setup")
        listing = app.query_one("#matches")
        highlighted = listing.highlighted
        details = str(app.query_one("#device-info", Static).content)
        await pilot.hover("#matches", offset=(4, 2))
        await pilot.pause()
        assert listing.highlighted == highlighted
        assert str(app.query_one("#device-info", Static).content) == details
        assert app.device_name == ""
        await pilot.click("#matches", offset=(4, 2))
        await pilot.pause()
        assert "W25Q32JV" in app.draft_device_name
        assert app.device_name == ""
        assert app.focused is app.query_one("#search", Input)


async def test_search_down_and_no_results():
    app = MiniproApp(demo=True, settings=Settings())
    async with app.run_test() as pilot:
        await pilot.pause()
        await command(pilot, "Device setup")
        await pilot.press("down")
        assert isinstance(app.focused, Input)
        await pilot.press(*"does-not-exist")
        assert not app.matches
        assert "No matching devices" in str(
            app.query_one("#device-info", Static).content
        )


async def test_exact_settings_command_does_not_save_preferences():
    app = MiniproApp(demo=True, settings=Settings())
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("ctrl+p", *"Settings")
        assert app.screen.matches[0].name == "Settings"
        await pilot.press("escape", "ctrl+p", *"no-such-command-xyz")
        assert not app.screen.matches
        await pilot.press("enter")
        assert isinstance(app.screen, CommandScreen)


async def test_programmer_and_device_gate_tabs_and_commands():
    class Unplugged(DemoBackend):
        models = []

        async def connected(self):
            return self.models

    backend = Unplugged()
    app = MiniproApp(demo=True, backend=backend, settings=Settings())
    async with app.run_test() as pilot:
        await pilot.pause()
        tabs = app.query_one(TabbedContent)
        assert tabs.active == "programmers"
        assert tabs.get_tab("setup").disabled
        assert tabs.get_tab("operate").disabled
        backend.models = ["T56"]
        app.poll_connection()
        await pilot.pause()
        assert app.selected_programmer == "T56"
        assert not tabs.get_tab("setup").disabled
        assert tabs.get_tab("operate").disabled
        assert next(c for c in app.commands() if c.name == "Read chip ID").reason
        await command(pilot, "Device setup")
        await pilot.press("enter", "enter")
        assert not tabs.get_tab("operate").disabled
        backend.models = ["T48"]
        app.poll_connection()
        await pilot.pause()
        assert app.device_name == ""
        assert tabs.get_tab("operate").disabled
        backend.models = ["T48", "T48"]
        app.poll_connection()
        await pilot.pause()
        assert app.selected_programmer == ""
        assert tabs.get_tab("setup").disabled


@pytest.mark.parametrize(
    ("models", "target"),
    [
        ([], "#refresh-programmers"),
        (["T48"], "#to-device"),
        (["T48", "T48"], "#refresh-programmers"),
    ],
)
async def test_programmer_default_focus_matches_connection_state(models, target):
    class ConnectionBackend(DemoBackend):
        async def connected(self):
            return models

    app = MiniproApp(demo=True, backend=ConnectionBackend(), settings=Settings())
    async with app.run_test() as pilot:
        await pilot.pause()
        assert app.focused is app.query_one(target)


async def test_programmer_revisit_focuses_save_without_poll_stealing_focus():
    class ConnectionBackend(DemoBackend):
        async def connected(self):
            return ["T48"]

    app = MiniproApp(demo=True, backend=ConnectionBackend(), settings=Settings())
    async with app.run_test() as pilot:
        await pilot.pause()
        await command(pilot, "Device setup")
        await command(pilot, "Programmer")
        assert app.focused is app.query_one("#to-device")
        refresh = app.query_one("#refresh-programmers", Button)
        refresh.focus()
        app.poll_connection()
        await pilot.pause()
        assert app.focused is refresh


async def test_vim_arrows_keep_inline_search_focus_and_tab_exits():
    app = MiniproApp(demo=True, settings=Settings())
    async with app.run_test() as pilot:
        await pilot.pause()
        await command(pilot, "Device setup")
        search = app.query_one("#search", Input)
        listing = app.query_one("#matches")
        await pilot.press("ctrl+j")
        assert listing.highlighted == 1
        assert app.focused is search
        await pilot.press("ctrl+k")
        assert listing.highlighted == 0
        await pilot.press(*"AT28", "ctrl+h", "x", "ctrl+l", "y")
        assert search.value == "AT2x8y"
        await pilot.press("tab")
        assert app.focused is not search
        assert app.focused is not listing


async def test_vim_arrows_in_command_palette():
    app = MiniproApp(demo=True, settings=Settings())
    async with app.run_test() as pilot:
        await pilot.pause()
        await pilot.press("ctrl+p", "ctrl+j")
        assert app.screen.query_one("#command-results").highlighted == 1
        await pilot.press("ctrl+k")
        assert app.screen.query_one("#command-results").highlighted == 0


async def test_select_device_focuses_set_device_and_updates_status():
    app = MiniproApp(demo=True, settings=Settings())
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        await command(pilot, "Device setup")
        await pilot.press(*"AT28C256", "enter")
        assert app.focused.id == "to-operation"
        assert str(app.focused.label) == "Save"
        assert "AT28C256@DIP28" not in str(app.query_one(".connection", Static).content)
        assert app.query_one(".connection", Static).region.height == 1
        await pilot.press("enter")
        assert "AT28C256" in str(app.query_one(".connection", Static).content)
        assert app.query_one(TabbedContent).active == "operate"


async def test_demo_has_log_and_sample_memory_without_reading():
    app = MiniproApp(demo=True, settings=Settings())
    async with app.run_test() as pilot:
        await pilot.pause()
        tabs = app.query_one(TabbedContent)
        assert not tabs.get_tab("log-tab").disabled
        assert "DEMO simulation" in app.session_log.text
        await command(pilot, "Device setup")
        await pilot.press("enter", "enter")
        await command(pilot, "Memory browser")
        assert tabs.active == "memory-tab"
        assert "DEMO sample" in str(app.query_one("#memory-source", Static).content)
        assert app.memory_path is None
        before = app.session_log.text
        await pilot.press("pagedown")
        assert app.query_one("#memory-log").scroll_y > 0
        viewer = app.query_one("#memory-log")
        assert not viewer.show_horizontal_scrollbar
        await pilot.press("right", "ctrl+l")
        assert viewer.scroll_x == 0
        # Navigation itself never emits a simulated read command.
        assert "-r" not in app.session_log.text[len(before) :]


async def test_deselecting_programmer_clears_device():
    app = MiniproApp(demo=True, settings=Settings())
    async with app.run_test() as pilot:
        await pilot.pause()
        await command(pilot, "Device setup")
        await pilot.press("enter", "enter")
        assert app.device_name
        await command(pilot, "Programmer")
        app.query_one("#programmer", Select).clear()
        await pilot.pause()
        assert app.selected_programmer == ""
        assert app.device_name == ""
        tabs = app.query_one(TabbedContent)
        assert tabs.get_tab("setup").disabled
        assert tabs.get_tab("operate").disabled
        assert "NO PROGRAMMER" in str(app.query_one(".connection", Static).content)


async def test_settings_log_path_follows_checkbox():
    app = MiniproApp(demo=True, settings=Settings())
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        await command(pilot, "Settings")
        path = app.query_one("#log-path", Input)
        assert path.disabled
        checkbox = app.query_one("#save-log")
        assert str(checkbox.label) == "Automatically save log"
        checkbox.focus()
        await pilot.press("space")
        assert not path.disabled
        await pilot.press("space")
        assert path.disabled
        assert (
            app.query_one("#used-minipro").region.y
            > app.query_one("#executable").region.y
        )
        assert app.query_one("#save-settings", Button).region.bottom <= 23


@pytest.mark.parametrize(
    "key",
    [
        "ctrl+question_mark",
        "ctrl+slash",
        "ctrl+underscore",
        "ctrl+shift+slash",
        "ctrl+shift+question_mark",
        "ctrl+shift+underscore",
    ],
)
async def test_help_accepts_terminal_control_code(key):
    app = MiniproApp(demo=True, settings=Settings())
    async with app.run_test() as pilot:
        await pilot.pause()
        await command(pilot, "Device setup")
        await pilot.press(key)
        assert isinstance(app.screen, HelpScreen)
        await pilot.press("escape")
        assert app.query_one("#search", Input).value == ""


async def test_settings_save_tracks_modifications(tmp_path):
    app = MiniproApp(demo=True, settings=Settings())
    async with app.run_test() as pilot:
        await pilot.pause()
        await command(pilot, "Device setup")
        await pilot.press("enter", "enter")
        selected = app.device_name
        await command(pilot, "Settings")
        save = app.query_one("#save-settings", Button)
        assert save.disabled
        assert str(save.label) == "Save settings"
        checkbox = app.query_one("#save-log")
        checkbox.focus()
        await pilot.press("space")
        assert not save.disabled
        await pilot.press("space")
        assert save.disabled
        await pilot.press("space")
        app.query_one("#log-path", Input).value = str(tmp_path / "%Y.log")
        await command(pilot, "Save settings")
        assert save.disabled
        assert app.device_name == selected
        assert app.session_log.path.exists()


async def test_xgpro_extraction_uses_and_persists_output_folder(tmp_path, monkeypatch):
    output = tmp_path / "extracted"
    calls = []
    saved = []

    def fake_extract(installer, destination, *, progress=None):
        calls.append((installer, destination))
        if progress is not None:
            progress("Reading installer")
        output.mkdir(exist_ok=True)
        return xgpro_images.ExtractionResult(12, 0, output)

    def fake_save(settings, path=None):
        saved.append(
            (
                settings.executable,
                settings.save_log,
                settings.log_path,
                settings.image_directory,
            )
        )

    monkeypatch.setattr(xgpro_images, "extract_images", fake_extract)
    monkeypatch.setattr(Settings, "save", fake_save)
    app = MiniproApp(
        settings=Settings(executable="old-minipro", log_path="old.log"),
        backend=DemoBackend(),
    )
    refreshed = []
    monkeypatch.setattr(app, "refresh_device_diagram", lambda: refreshed.append(True))
    async with app.run_test() as pilot:
        await pilot.pause()
        await command(pilot, "Settings")
        app.action_extract_xgpro()
        await pilot.pause()
        assert isinstance(app.screen, ExtractImagesScreen)
        download = app.screen.query_one("#xgpro-download", Link)
        assert download.url == "http://www.xgecu.com/EN/Download.html"
        app.query_one("#executable", Input).value = "draft-minipro"
        app.query_one("#log-path", Input).value = "draft.log"
        app.screen.query_one("#extract-installer", Input).value = "installer.exe"
        app.screen.query_one("#extract-output", Input).value = str(output)
        app.screen.run_extraction()
        assert app.screen.query_one("#extract-installer", Input).disabled
        assert app.screen.query_one("#extract-output", Input).disabled
        for _ in range(30):
            await pilot.pause(0.05)
            if not isinstance(app.screen, ExtractImagesScreen):
                break
        assert not isinstance(app.screen, ExtractImagesScreen)
        assert calls == [("installer.exe", str(output))]
        assert app.settings.image_directory == str(output)
        assert app.query_one("#image-directory", Input).value == str(output)
        assert not app.query_one("#image-directory-warning", Static).display
        assert app.settings_baseline[3] == str(output)
        assert saved == [("old-minipro", False, "old.log", str(output))]
        assert app.settings.executable == "old-minipro"
        assert app.query_one("#executable", Input).value == "draft-minipro"
        assert not app.query_one("#save-settings", Button).disabled
        assert refreshed == [True]


async def test_settings_image_folder_warning_tracks_saved_and_edited_paths(tmp_path):
    missing = tmp_path / "not-created"
    regular_file = tmp_path / "image.jpg"
    regular_file.write_bytes(b"image")
    existing = tmp_path / "images"
    existing.mkdir()
    app = MiniproApp(demo=True, settings=Settings(image_directory=str(missing)))
    async with app.run_test() as pilot:
        await pilot.pause()
        app.action_settings()
        await pilot.pause()
        field = app.query_one("#image-directory", Input)
        warning = app.query_one("#image-directory-warning", Static)
        assert warning.display
        assert str(warning.content) == "This image folder does not exist."

        field.value = str(existing)
        await pilot.pause()
        assert not warning.display

        field.value = str(regular_file)
        await pilot.pause()
        assert warning.display
        assert str(warning.content) == "This image path is not a folder."

        field.value = "~user-that-does-not-exist/images"
        await pilot.pause()
        assert warning.display
        assert "could not be checked" in str(warning.content)


@pytest.mark.parametrize("size", [(80, 24), (120, 42)])
async def test_settings_image_folder_warning_has_clear_spacing(size, tmp_path):
    missing = tmp_path / "not-created"
    app = MiniproApp(demo=True, settings=Settings())
    async with app.run_test(size=size) as pilot:
        await pilot.pause()
        app.action_settings()
        await pilot.pause()
        field = app.query_one("#image-directory", Input)
        warning = app.query_one("#image-directory-warning", Static)
        extract = app.query_one("#extract-xgpro", Button)
        field.value = str(missing)
        await pilot.pause()
        assert warning.display
        assert field.region.bottom <= warning.region.y
        assert warning.region.bottom <= extract.region.y


async def test_xgpro_extraction_failure_and_cancel_keep_existing_folder(
    tmp_path, monkeypatch
):
    old = tmp_path / "existing"

    def fail_extract(installer, destination, *, progress=None):
        raise xgpro_images.ExtractionError("output file collision")

    monkeypatch.setattr(xgpro_images, "extract_images", fail_extract)
    app = MiniproApp(demo=True, settings=Settings(image_directory=str(old)))
    async with app.run_test() as pilot:
        await pilot.pause()
        await command(pilot, "Settings")
        app.action_extract_xgpro()
        await pilot.pause()
        assert isinstance(app.screen, ExtractImagesScreen)
        app.screen.query_one("#extract-installer", Input).value = "installer.exe"
        app.screen.query_one("#extract-output", Input).value = str(tmp_path / "new")
        app.screen.run_extraction()
        await pilot.pause(0.2)
        assert isinstance(app.screen, ExtractImagesScreen)
        assert "collision" in str(
            app.screen.query_one("#extract-status", Static).content
        )
        await pilot.press("escape")
        assert app.settings.image_directory == str(old)
        assert app.query_one("#image-directory", Input).value == str(old)
        app.action_extract_xgpro()
        await pilot.pause()
        assert isinstance(app.screen, ExtractImagesScreen)
        app.screen.cancel_extraction()
        await pilot.pause()
        assert not isinstance(app.screen, ExtractImagesScreen)
        assert app.settings.image_directory == str(old)


@pytest.mark.parametrize("size", [(80, 24), (120, 42)])
async def test_xgpro_extraction_layout_scrolls_fields_without_overlapping_actions(size):
    app = MiniproApp(demo=True, settings=Settings())
    async with app.run_test(size=size) as pilot:
        await pilot.pause()
        app.action_settings()
        app.action_extract_xgpro()
        await pilot.pause()
        assert isinstance(app.screen, ExtractImagesScreen)
        fields = app.screen.query_one("#extract-fields")
        output = app.screen.query_one("#extract-output", Input)
        run = app.screen.query_one("#extract-run", Button)
        dialog = app.screen.query_one("#dialog")
        assert run.region.bottom <= dialog.content_region.bottom
        assert fields.region.bottom <= run.region.y

        output.focus()
        await pilot.pause(0.1)
        assert (
            fields.region.x <= output.region.x
            and fields.region.y <= output.region.y
            and output.region.right <= fields.region.right
            and output.region.bottom <= fields.region.bottom
        )
        assert fields.region.bottom <= run.region.y


async def test_xgpro_completion_waits_for_covered_screen_before_dismissing(
    tmp_path, monkeypatch
):
    output = tmp_path / "extracted"
    started = threading.Event()
    release = threading.Event()

    def blocked_extract(installer, destination, *, progress=None):
        started.set()
        release.wait(timeout=5)
        output.mkdir(exist_ok=True)
        return xgpro_images.ExtractionResult(1, 0, output)

    monkeypatch.setattr(xgpro_images, "extract_images", blocked_extract)
    app = MiniproApp(demo=True, settings=Settings())
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        app.action_settings()
        app.action_extract_xgpro()
        await pilot.pause()
        assert isinstance(app.screen, ExtractImagesScreen)
        screen = app.screen
        screen.query_one("#extract-installer", Input).value = "installer.exe"
        screen.query_one("#extract-output", Input).value = str(output)
        screen.run_extraction()
        for _ in range(20):
            await pilot.pause(0.05)
            if started.is_set():
                break
        assert started.is_set()
        await pilot.press("escape")
        await app.action_quit()
        assert app.screen is screen

        app.action_help()
        await pilot.pause()
        assert isinstance(app.screen, HelpScreen)
        release.set()
        for _ in range(40):
            await pilot.pause(0.05)
            if screen.pending_result is not None:
                break
        assert app.screen is not screen
        assert screen.pending_result is not None

        await pilot.press("escape")
        await pilot.pause()
        assert not isinstance(app.screen, ExtractImagesScreen)
        assert app.settings.image_directory == str(output)


async def test_file_dialog_live_path_and_navigation(tmp_path):
    from textual.widgets import DirectoryTree

    directory = tmp_path / "folder"
    directory.mkdir()
    file = directory / "image.bin"
    file.write_bytes(b"data")
    app = MiniproApp(demo=True, settings=Settings())
    async with app.run_test() as pilot:
        await pilot.pause()
        app.push_screen(FileScreen(str(directory / "partial" / "name.bin"), save=True))
        await pilot.pause()
        field = app.screen.query_one("#path", Input)
        tree = app.screen.query_one(DirectoryTree)
        await pilot.press("tab")
        assert tree.path == directory
        await pilot.click("#parent-folder")
        assert tree.path == tmp_path
        assert field.value == str(tmp_path)
        await pilot.click("#home-folder")
        assert field.value == str(Path.home())
        await pilot.press("shift+tab", "shift+tab")
        assert app.focused is field
        field.value = str(directory / "unfinished")
        await pilot.pause(0.25)
        assert app.focused is field
        assert tree.path == directory


@pytest.mark.parametrize("left,right", [("left", "right"), ("ctrl+h", "ctrl+l")])
async def test_file_tree_branch_navigation(tmp_path, left, right):
    from minipro_ui.widgets import FileTree

    folder = tmp_path / "folder"
    folder.mkdir()
    (folder / "image.bin").write_bytes(b"data")
    app = MiniproApp(demo=True, settings=Settings())
    async with app.run_test() as pilot:
        app.push_screen(FileScreen(str(tmp_path), save=True))
        await pilot.pause()
        tree = app.screen.query_one(FileTree)
        tree.focus()
        tree.move_cursor(tree.root)
        await pilot.press(right)
        await pilot.pause()
        node = tree.cursor_node
        assert node.data.path == folder
        await pilot.press(right)
        await pilot.pause()
        assert node.is_expanded
        await pilot.press(left)
        assert not node.is_expanded
        await pilot.press(left)
        assert tree.cursor_node is tree.root
        assert app.focused is tree


async def test_log_overwrite_needs_confirmation(tmp_path):
    from minipro_ui.screens import ReplaceFileScreen

    destination = tmp_path / "session.log"
    destination.write_text("keep")
    app = MiniproApp(demo=True, settings=Settings())
    async with app.run_test() as pilot:
        await pilot.pause()
        await command(pilot, "Save log to file")
        await pilot.press(*str(destination), "enter")
        assert isinstance(app.screen, ReplaceFileScreen)
        assert destination.read_text() == "keep"
        await pilot.press("escape")
        assert isinstance(app.screen, FileScreen)
        assert destination.read_text() == "keep"
        await pilot.press("enter")
        await command(pilot, "Replace")
        assert "DEMO simulation" in destination.read_text()


async def test_focus_hints_choose_cancel_and_skip_help():
    from minipro_ui.focus_hints import FocusHints

    app = MiniproApp(demo=True, settings=Settings())
    async with app.run_test() as pilot:
        await pilot.pause()
        previous = app.focused
        await pilot.press("alt+space")
        assert isinstance(app.screen, FocusHints)
        assert app.screen.targets
        await pilot.press("escape")
        assert app.focused is previous
        await pilot.press("alt+space")
        target = app.query_one("#refresh-programmers", Button)
        number = next(
            i for i, item in enumerate(app.screen.targets, 1) if item.widget is target
        )
        await pilot.press(*str(number), "enter")
        assert not isinstance(app.screen, FocusHints)
        assert app.focused is target
        await pilot.press("ctrl+shift+slash")
        assert isinstance(app.screen, HelpScreen)
        help_screen = app.screen
        for key in ("alt+space", "left_alt", "right_alt"):
            await pilot.press(key)
            assert app.screen is help_screen


async def test_focus_hints_inside_file_dialog(tmp_path):
    from minipro_ui.focus_hints import FocusHints

    app = MiniproApp(demo=True, settings=Settings())
    async with app.run_test() as pilot:
        await pilot.pause()
        dialog = FileScreen(str(tmp_path))
        app.push_screen(dialog)
        await pilot.pause()
        field = dialog.query_one("#path", Input)
        await pilot.press("left_alt")
        assert isinstance(app.screen, FocusHints)
        assert all(item.widget.screen is dialog for item in app.screen.targets)
        await pilot.press("escape")
        assert app.screen is dialog
        assert app.focused is field


async def test_device_draft_does_not_change_active_job_or_status():
    from minipro_ui.focus_hints import FocusHints

    app = MiniproApp(demo=True, settings=Settings())
    async with app.run_test(size=(100, 32)) as pilot:
        await pilot.pause()
        await command(pilot, "Device setup")
        await pilot.press("enter")
        assert app.draft_device_name
        assert not app.device_name
        assert app.query_one(TabbedContent).get_tab("operate").disabled
        await pilot.press("enter")
        saved_device = app.device_name
        await command(pilot, "Device setup")
        await pilot.press(*"ATMEGA328P", "enter")
        assert app.draft_device_name != saved_device
        app.query_one("#interface", Select).value = "icsp"
        await command(pilot, "Log")
        assert app.device_name == saved_device
        assert app.current_job().device == saved_device
        assert app.current_job().interface == "zif"
        assert saved_device in str(app.query_one(".connection", Static).content)
        await command(pilot, "Device setup")
        await pilot.press("alt+space")
        assert isinstance(app.screen, FocusHints)
        assert all(t.widget.id != "device-info-pane" for t in app.screen.targets)
        await pilot.press("escape")
        await pilot.click("#to-operation")
        assert app.device_name == app.draft_device_name
        assert app.current_job().interface == "icsp"
        await pilot.press("alt+space")
        from textual.containers import VerticalScroll

        assert not any(isinstance(t.widget, VerticalScroll) for t in app.screen.targets)


@pytest.mark.parametrize("width", [60, 80, 100])
async def test_footer_shortcuts_fit_and_style_keys_separately(width):
    from minipro_ui.widgets import BottomBar

    app = MiniproApp(demo=True, settings=Settings())
    async with app.run_test(size=(width, 24)) as pilot:
        await pilot.pause()
        hints = app.query_one(BottomBar).query_one(".shortcuts", Static)
        text = hints.content
        assert hints.region.right <= width
        assert hints.region.width >= text.cell_len
        assert text.plain.index("Ctrl+p") < text.plain.index("Alt")
        assert "^" not in text.plain
        assert "Ctrl+P" not in text.plain
        if width >= 70:
            assert text.get_style_at_offset(app.console, 0) != text.get_style_at_offset(
                app.console, text.plain.index("Help")
            )


async def test_leaving_operation_clears_validation_error():
    app = MiniproApp(demo=True, settings=Settings())
    async with app.run_test() as pilot:
        await pilot.pause()
        await command(pilot, "Device setup")
        await pilot.press("enter", "enter")
        await command(pilot, "Review job")
        error = app.query_one("#job-error", Static)
        assert str(error.content)
        # Use the actual tab control so this covers navigation outside commands.
        await pilot.click("#--content-tab-setup")
        assert str(error.content) == ""
        previous = app.device_name
        await pilot.click("#search")
        await pilot.press("down", "enter")
        await pilot.click("#to-operation")
        assert app.device_name != previous
        assert app.query_one(TabbedContent).active == "operate"
        assert str(error.content) == ""


async def test_operations_and_regions_follow_saved_device():
    from textual.widgets import Select

    app = MiniproApp(demo=True, settings=Settings())
    async with app.run_test() as pilot:
        await pilot.pause()
        await command(pilot, "Device setup")
        await pilot.press("enter", "enter")
        assert "Read chip ID" not in {c.name for c in app.commands()}
        assert "Test logic / RAM" not in {c.name for c in app.commands()}
        assert app.device_info.available_regions(Operation.READ) == ("code",)
        await command(pilot, "Device setup")
        app.query_one("#search", Input).value = "ATMEGA328P"
        await pilot.pause()
        await pilot.press("enter", "enter")
        assert "Read chip ID" in {c.name for c in app.commands()}
        app.query_one("#memory", Select).value = "calibration"
        await command(pilot, "Program")
        assert app.query_one("#memory", Select).value == "code"
        await command(pilot, "Device setup")
        app.query_one("#search", Input).value = "7400"
        await pilot.pause()
        await pilot.press("enter", "enter")
        assert app.query_one("#operation", Select).value == Operation.TEST.value
        assert not app.query_one("#file-fields").display
        assert not app.query_one("#memory-fields").display
        names = {c.name for c in app.commands()}
        assert Operation.TEST.value in names
        assert not any(op.value in names for op in Operation if op != Operation.TEST)


@pytest.mark.parametrize("operations", [None, ()])
async def test_unknown_or_unsupported_device_has_no_runnable_options(operations):
    from dataclasses import replace

    class RestrictedBackend(DemoBackend):
        async def device_info(self, programmer, name):
            info = await super().device_info(programmer, name)
            return (
                replace(info, operations=operations) if name == self.NAMES[0] else info
            )

    app = MiniproApp(demo=True, backend=RestrictedBackend(), settings=Settings())
    async with app.run_test() as pilot:
        await pilot.pause()
        await command(pilot, "Device setup")
        await pilot.press("enter", "enter")
        assert not app.query_one("#operation").display
        assert app.query_one("#review", Button).disabled
        assert not app.query_one("#memory-fields").display
        assert all(op.value not in {c.name for c in app.commands()} for op in Operation)
        review = next(c for c in app.commands() if c.name == "Review job")
        assert review.reason
        await command(pilot, "Device setup")
        await pilot.press(*"W25Q32JV", "enter", "enter")
        assert app.query_one("#operation").display
        assert app.query_one("#file-fields").display
        assert app.query_one("#memory-fields").display
        assert not app.query_one("#review", Button).disabled
