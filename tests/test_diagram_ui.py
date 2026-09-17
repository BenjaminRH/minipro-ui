from __future__ import annotations

import json
from dataclasses import replace

from PIL import Image
from textual.containers import VerticalScroll
from textual.widgets import Button, Input

import minipro_ui.diagrams as diagrams
from minipro_ui.app import MiniproApp
from minipro_ui.demo import DemoBackend
from minipro_ui.diagram_widgets import DeviceDiagrams
from minipro_ui.settings import Settings


class DiagramDemoBackend(DemoBackend):
    attached = True
    fail_at28 = False

    async def connected(self):
        return ["T48"] if self.attached else []

    async def device_info(self, programmer: str, name: str):
        if self.fail_at28 and name.startswith("AT28"):
            raise RuntimeError("synthetic metadata failure")
        info = await super().device_info(programmer, name)
        if name.startswith("AT28"):
            return replace(info, details=info.details + "\nICSP: ICP002.JPG")
        return info


def use_synthetic_catalog(tmp_path, monkeypatch):
    catalog_root = tmp_path / "catalog"
    catalog_root.mkdir()
    catalog_path = catalog_root / "catalog.json"
    catalog_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "mappings": {"T48": {"ICP002.JPG": "t48/ICP002.jpg"}},
                "assets": {
                    "t48/ICP002.jpg": {
                        "title": "Synthetic T48 reference",
                    }
                },
            }
        )
    )
    monkeypatch.setattr(diagrams, "_catalog_path", lambda: catalog_path)


async def test_highlighted_device_shows_local_diagram_without_saving_device(
    tmp_path, monkeypatch
):
    use_synthetic_catalog(tmp_path, monkeypatch)
    Image.new("RGB", (24, 12), "#34776b").save(tmp_path / "ICP002.jpg")
    app = MiniproApp(
        demo=True,
        backend=DiagramDemoBackend(),
        settings=Settings(image_directory=str(tmp_path)),
    )
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.3)
        gallery = app.query_one(DeviceDiagrams)
        assert app.device_name == ""
        assert app.draft_device_name == ""
        assert gallery.display
        assert app.query_one("#diagram-card-0").query_one(Button)

        app.action_device()
        await pilot.press(*"7400")
        await pilot.pause(0.1)
        assert not gallery.display
        assert not app.query("#diagram-open-0, #diagram-enlarge-0")


async def test_image_folder_setting_refreshes_preview_without_resetting_device(
    tmp_path, monkeypatch
):
    use_synthetic_catalog(tmp_path, monkeypatch)
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    Image.new("RGB", (24, 12), "#34776b").save(image_dir / "ICP002.JPG")
    app = MiniproApp(
        demo=True,
        backend=DiagramDemoBackend(),
        settings=Settings(image_directory=""),
    )
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.3)
        app.action_settings()
        await pilot.pause()
        # The device has only been highlighted; saving the image folder must not
        # turn that draft into the active device.
        await pilot.click("#image-directory")
        await pilot.press(*str(image_dir))
        await pilot.click("#save-settings")
        await pilot.pause(0.3)
        assert app.device_name == ""
        assert app.draft_device_name == ""
        assert app.settings.image_directory == str(image_dir)
        assert app.query_one(DeviceDiagrams).display
        assert app.query_one("#image-directory", Input).value == str(image_dir)


async def test_same_programmer_reconnect_restores_cached_diagram(tmp_path, monkeypatch):
    use_synthetic_catalog(tmp_path, monkeypatch)
    Image.new("RGB", (24, 12), "#34776b").save(tmp_path / "ICP002.JPG")
    backend = DiagramDemoBackend()
    app = MiniproApp(
        demo=True,
        backend=backend,
        settings=Settings(image_directory=str(tmp_path)),
    )
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.3)
        assert app.query_one(DeviceDiagrams).display
        backend.attached = False
        app.poll_connection()
        await pilot.pause(0.3)
        assert not app.query_one(DeviceDiagrams).display
        backend.attached = True
        app.poll_connection()
        await pilot.pause(0.6)
        assert app.query_one(DeviceDiagrams).display


async def test_metadata_error_clears_diagram_and_highlight(tmp_path, monkeypatch):
    use_synthetic_catalog(tmp_path, monkeypatch)
    Image.new("RGB", (24, 12), "#34776b").save(tmp_path / "ICP002.JPG")
    backend = DiagramDemoBackend()
    app = MiniproApp(
        demo=True,
        backend=backend,
        settings=Settings(image_directory=str(tmp_path)),
    )
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.3)
        assert app.query_one(DeviceDiagrams).display
        backend.fail_at28 = True
        app.metadata_cache.pop(("T48", "AT28C256@DIP28"), None)
        app.action_device()
        search = app.query_one("#search", Input)
        search.value = ""
        await pilot.pause(0.1)
        search.value = "AT28"
        await pilot.pause(0.4)
        assert not app.query_one(DeviceDiagrams).display
        assert app.highlighted_device_name == ""


async def test_image_folder_is_locked_during_operations(tmp_path):
    app = MiniproApp(demo=True, settings=Settings())
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause(0.3)
        image_directory = app.query_one("#image-directory", Input)
        app.set_configuration_locked(True)
        assert image_directory.disabled
        app.set_configuration_locked(False)
        assert not image_directory.disabled


async def test_diagram_action_is_reachable_after_compact_pane_scroll(
    tmp_path, monkeypatch
):
    use_synthetic_catalog(tmp_path, monkeypatch)
    Image.new("RGB", (24, 12), "#34776b").save(tmp_path / "ICP002.JPG")
    app = MiniproApp(
        demo=True,
        backend=DiagramDemoBackend(),
        settings=Settings(image_directory=str(tmp_path)),
    )
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause(0.6)
        assert app.query_one(DeviceDiagrams).display
        app.action_device()
        await pilot.pause()
        pane = app.query_one("#device-info-pane", VerticalScroll)
        pane.scroll_end(animate=False)
        await pilot.pause()
        button = app.query_one("#diagram-open-0", Button)
        assert button.region.intersection(pane.region).height > 0
