from __future__ import annotations

import asyncio
import json
import subprocess
import threading
from pathlib import Path

import pytest
from PIL import Image
from textual.app import App, ComposeResult
from textual.widgets import Static

import minipro_ui.diagram_widgets as diagram_widgets
import minipro_ui.diagrams as diagrams
from minipro_ui.diagram_widgets import DeviceDiagrams, DiagramViewerScreen
from minipro_ui.diagrams import (
    DiagramReference,
    load_diagram_assets,
    parse_diagram_references,
    resolve_diagram,
)


def test_parse_references_is_limited_to_supported_metadata_fields() -> None:
    details = (
        "Package: Adapter012.JPG\n"
        "ICSP: ICP9.png\n"
        "Other: Adapter123.JPG\n"
        "Package: ../Adapter012.JPG\n"
    )

    assert parse_diagram_references(details) == (
        DiagramReference("package", "Adapter012.JPG"),
    )


def test_parse_references_accepts_programmer_specific_basename() -> None:
    assert parse_diagram_references("ICSP: T48ICP002.jpg") == (
        DiagramReference("icsp", "T48ICP002.jpg"),
    )


def test_parse_references_accepts_jpeg_extension() -> None:
    assert parse_diagram_references("Package: Adapter012.jpeg") == (
        DiagramReference("package", "Adapter012.jpeg"),
    )


def test_resolve_is_case_insensitive_and_checks_img_child(tmp_path: Path) -> None:
    folder = tmp_path / "IMG"
    folder.mkdir()
    image = folder / "adapter012.JpG"
    Image.new("RGB", (12, 8), "red").save(image)

    reference = DiagramReference("package", "Adapter012.JPG")
    assert resolve_diagram(reference, tmp_path) == image


def test_missing_and_corrupt_images_are_omitted(tmp_path: Path) -> None:
    (tmp_path / "ICP009.JPG").write_bytes(b"not an image")
    details = "Package: Adapter001.JPG\nICSP: ICP009.JPG"

    assert load_diagram_assets(details, tmp_path) == ()


def test_valid_image_is_decoded_and_detached(tmp_path: Path) -> None:
    image_path = tmp_path / "Adapter001.JPG"
    Image.new("RGB", (13, 7), "blue").save(image_path)

    assets = load_diagram_assets("Package: Adapter001.JPG", tmp_path)

    assert len(assets) == 1
    assert assets[0].path == image_path
    assert assets[0].image.size == (13, 7)


def _write_catalog(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "catalog"
    root.mkdir()
    path = root / "catalog.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "mappings": {
                    "TL866A": {"ICP001.JPG": "legacy/ICP001.jpg"},
                    "TL866II": {"ICP001.JPG": "current/ICP001.jpg"},
                    "T48": {"ICP002.JPG": "current/ICP002.jpg"},
                },
                "assets": {
                    "legacy/ICP001.jpg": {
                        "title": "Legacy reference",
                    },
                    "current/ICP001.jpg": {
                        "title": "Current reference",
                    },
                    "current/ICP002.jpg": {
                        "title": "T48 reference",
                    },
                },
            }
        )
    )
    monkeypatch.setattr(diagrams, "_catalog_path", lambda: path)
    return root


def test_programmer_mapping_avoids_legacy_filename_collision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_catalog(tmp_path, monkeypatch)
    (tmp_path / "legacy").mkdir()
    (tmp_path / "current").mkdir()
    legacy = tmp_path / "legacy" / "ICP001.JPG"
    current = tmp_path / "current" / "ICP001.JPG"
    Image.new("RGB", (4, 4), "red").save(legacy)
    Image.new("RGB", (4, 4), "blue").save(current)

    assert (
        resolve_diagram(
            DiagramReference("icsp", "ICP001.JPG"), tmp_path, programmer="TL866A"
        )
        == legacy
    )
    assert (
        resolve_diagram(
            DiagramReference("icsp", "ICP001.JPG"), tmp_path, programmer="TL866II"
        )
        == current
    )


def test_known_programmer_does_not_fall_back_to_unmapped_reference(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_catalog(tmp_path, monkeypatch)
    Image.new("RGB", (4, 4), "red").save(tmp_path / "ICP001.JPG")
    assert load_diagram_assets("ICSP: ICP001.JPG", tmp_path, programmer="T48") == ()


def test_bundled_mapping_is_used_when_no_user_folder_is_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _write_catalog(tmp_path, monkeypatch)
    (root / "current").mkdir()
    bundled = root / "current" / "ICP002.jpg"
    Image.new("RGB", (4, 4), "blue").save(bundled)
    assets = load_diagram_assets("ICSP: ICP002.JPG", programmer="T48")
    assert len(assets) == 1
    assert assets[0].path == bundled
    assert assets[0].origin == "bundled"
    assert assets[0].title == "T48 reference"


def test_explicit_image_folder_overrides_bundled_mapping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _write_catalog(tmp_path, monkeypatch)
    (root / "current").mkdir()
    bundled = root / "current" / "ICP002.jpg"
    local = tmp_path / "ICP002.JPG"
    Image.new("RGB", (4, 4), "blue").save(bundled)
    Image.new("RGB", (4, 4), "orange").save(local)

    assert (
        resolve_diagram(
            DiagramReference("icsp", "ICP002.JPG"),
            tmp_path,
            programmer="T48",
        )
        == local
    )
    assets = load_diagram_assets("ICSP: ICP002.JPG", tmp_path, programmer="T48")
    assert len(assets) == 1
    assert assets[0].path == local
    assert assets[0].origin == "local"


@pytest.mark.parametrize("invalid_kind", ["missing", "file", "bad-home"])
def test_invalid_explicit_image_folder_falls_back_to_bundled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, invalid_kind: str
) -> None:
    root = _write_catalog(tmp_path, monkeypatch)
    (root / "current").mkdir()
    bundled = root / "current" / "ICP002.jpg"
    Image.new("RGB", (4, 4), "blue").save(bundled)
    if invalid_kind == "missing":
        configured = tmp_path / "not-created"
    elif invalid_kind == "file":
        configured = tmp_path / "image-directory-file"
        configured.write_bytes(b"not a directory")
    else:
        configured = Path("~definitely-not-a-real-minipro-user/images")

    assets = load_diagram_assets("ICSP: ICP002.JPG", configured, programmer="T48")
    assert len(assets) == 1
    assert assets[0].path == bundled
    assert assets[0].origin == "bundled"


def test_configured_folder_created_later_takes_authority(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _write_catalog(tmp_path, monkeypatch)
    (root / "current").mkdir()
    bundled = root / "current" / "ICP002.jpg"
    Image.new("RGB", (4, 4), "blue").save(bundled)
    configured = tmp_path / "created-later"

    initial = load_diagram_assets("ICSP: ICP002.JPG", configured, programmer="T48")
    assert initial[0].path == bundled
    configured.mkdir()
    local = configured / "ICP002.JPG"
    Image.new("RGB", (4, 4), "orange").save(local)

    refreshed = load_diagram_assets("ICSP: ICP002.JPG", configured, programmer="T48")
    assert refreshed[0].path == local
    assert refreshed[0].origin == "local"


def test_explicit_incomplete_image_folder_does_not_fall_back_to_bundled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _write_catalog(tmp_path, monkeypatch)
    (root / "current").mkdir()
    Image.new("RGB", (4, 4), "blue").save(root / "current" / "ICP002.jpg")
    local = tmp_path / "empty-local-folder"
    local.mkdir()

    assert (
        resolve_diagram(
            DiagramReference("icsp", "ICP002.JPG"),
            local,
            programmer="T48",
        )
        is None
    )


def test_unknown_explicit_programmer_never_uses_unverified_images(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_catalog(tmp_path, monkeypatch)
    Image.new("RGB", (4, 4), "red").save(tmp_path / "ICP001.JPG")
    assert load_diagram_assets("ICSP: ICP001.JPG", tmp_path, programmer="T76") == ()


def test_device_diagrams_clears_stale_images_before_loading(tmp_path: Path) -> None:
    image_path = tmp_path / "Adapter001.JPG"
    Image.new("RGB", (4, 4), "green").save(image_path)

    class DiagramApp(App[None]):
        def compose(self) -> ComposeResult:
            yield DeviceDiagrams()

    async def check() -> None:
        async with DiagramApp().run_test() as pilot:
            gallery = pilot.app.query_one(DeviceDiagrams)
            gallery.show_device("Package: Adapter001.JPG", str(tmp_path))
            assert not gallery.display
            await pilot.pause(0.2)
            assert gallery.display
            gallery.show_device("Package: Missing.JPG", str(tmp_path))
            assert not gallery.display
            await pilot.pause(0.2)
            assert not gallery.display

    import asyncio

    asyncio.run(check())


def test_loader_completion_after_clear_cannot_resurrect_card(
    tmp_path: Path, monkeypatch
):
    image_path = tmp_path / "Adapter001.JPG"
    Image.new("RGB", (4, 4), "green").save(image_path)
    original = diagram_widgets.load_diagram_assets
    started = threading.Event()
    release = threading.Event()

    def slow_loader(details, directory, executable, programmer=""):
        started.set()
        release.wait(2)
        return original(details, directory, executable, programmer)

    monkeypatch.setattr(diagram_widgets, "load_diagram_assets", slow_loader)

    class DiagramApp(App[None]):
        def compose(self) -> ComposeResult:
            yield DeviceDiagrams()

    async def check() -> None:
        async with DiagramApp().run_test() as pilot:
            gallery = pilot.app.query_one(DeviceDiagrams)
            gallery.show_device("Package: Adapter001.JPG", str(tmp_path))
            await pilot.pause(0.1)
            assert started.is_set()
            gallery.clear()
            release.set()
            await pilot.pause(0.2)
            assert not gallery.display
            assert not gallery.query("DiagramCard")

    asyncio.run(check())


@pytest.mark.parametrize("bitmap", [False, True])
def test_card_controls_follow_renderer_capability(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bitmap: bool
) -> None:
    image_path = tmp_path / "Adapter001.JPG"
    Image.new("RGB", (4, 4), "green").save(image_path)
    monkeypatch.setattr(diagram_widgets, "BITMAP_RENDERER_AVAILABLE", bitmap)

    class DiagramApp(App[None]):
        def compose(self) -> ComposeResult:
            yield DeviceDiagrams()

    async def check() -> None:
        async with DiagramApp().run_test() as pilot:
            gallery = pilot.app.query_one(DeviceDiagrams)
            gallery.show_device("Package: Adapter001.JPG", str(tmp_path))
            await pilot.pause(0.2)
            assert gallery.query_one("#diagram-open-0")
            if bitmap:
                assert gallery.query_one("#diagram-enlarge-0")
            else:
                assert not gallery.query("#diagram-enlarge-0")
                assert gallery.query_one(Static)

    asyncio.run(check())


def test_native_opener_is_quiet_and_reports_failure(monkeypatch, tmp_path: Path):
    calls: list[tuple[list[str], dict[str, object]]] = []

    def failed_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 1)

    monkeypatch.setattr(diagram_widgets.sys, "platform", "linux")
    monkeypatch.setattr(diagram_widgets.subprocess, "run", failed_run)

    assert not diagram_widgets.open_image(tmp_path / "diagram.jpg")
    assert calls[0][0][0] == "xdg-open"
    assert Path(calls[0][0][1]).is_absolute()
    assert calls[0][1]["capture_output"] is True


def test_native_opener_timeout_is_handled(monkeypatch, tmp_path: Path):
    def timed_out(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

    monkeypatch.setattr(diagram_widgets.sys, "platform", "darwin")
    monkeypatch.setattr(diagram_widgets.subprocess, "run", timed_out)

    assert not diagram_widgets.open_image(tmp_path / "diagram.jpg")


@pytest.mark.parametrize("size", [(1600, 200), (200, 1600)])
def test_viewer_fits_wide_and_tall_images_and_escape_closes(
    tmp_path: Path, size: tuple[int, int]
):
    image_path = tmp_path / "Adapter001.JPG"
    Image.new("RGB", size, "green").save(image_path)
    asset = load_diagram_assets("Package: Adapter001.JPG", tmp_path)[0]

    class ViewerApp(App[None]):
        def compose(self) -> ComposeResult:
            yield from ()

    async def check() -> None:
        async with ViewerApp().run_test(size=(80, 24)) as pilot:
            pilot.app.push_screen(DiagramViewerScreen(asset))
            await pilot.pause(0.2)
            viewer = pilot.app.screen
            image = viewer.query_one(".diagram-large")
            viewport = viewer.query_one("#diagram-viewer-scroll")
            assert image.content_size.width <= viewport.content_size.width
            assert image.content_size.height <= viewport.content_size.height
            await pilot.press("escape")
            await pilot.pause()
            assert not isinstance(pilot.app.screen, DiagramViewerScreen)

    asyncio.run(check())
