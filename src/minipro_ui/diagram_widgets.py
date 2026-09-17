"""Textual widgets for optional minipro adapter diagrams."""

from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Static

# Importing textual-image performs its terminal capability probe.  Keep this
# at module import time, before Textual owns stdin/stdout.
from textual_image.renderable import Image as AutoRenderable
from textual_image.renderable.sixel import Image as SixelRenderable
from textual_image.renderable.tgp import Image as TGPRenderable
from textual_image.widget import Image as BitmapImage

from .diagrams import DiagramAsset, load_diagram_assets

BITMAP_RENDERER_AVAILABLE = AutoRenderable in (TGPRenderable, SixelRenderable)


def open_image(path: Path) -> bool:
    """Open an image in the platform's native viewer, without a shell."""

    try:
        absolute = path.expanduser().resolve()
        if sys.platform == "darwin":
            result = subprocess.run(
                ["open", str(absolute)],
                check=False,
                capture_output=True,
                timeout=10,
            )
        elif sys.platform.startswith("linux"):
            result = subprocess.run(
                ["xdg-open", str(absolute)],
                check=False,
                capture_output=True,
                timeout=10,
            )
        elif sys.platform == "win32":
            import os

            os.startfile(str(absolute))  # type: ignore[attr-defined]
            return True
        else:
            return False
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


class DiagramViewerScreen(ModalScreen[None]):
    """Large, aspect-preserving viewer for a validated diagram bitmap."""

    BINDINGS = [Binding("escape", "close", "Back", show=False)]
    help_topic = "devices"

    DEFAULT_CSS = """
    #diagram-viewer {
        width: 94%;
        max-width: 120;
        height: 90%;
        max-height: 45;
        padding: 1 2;
        border: round #59847d;
        background: #18232e;
    }
    #diagram-viewer .diagram-large {
        width: auto;
        height: auto;
        min-height: 1;
        max-width: 100%;
        max-height: 100%;
    }
    #diagram-viewer-scroll {
        height: 1fr;
        width: 100%;
        align: center middle;
        overflow: auto;
    }
    #diagram-viewer .buttons {
        height: 3;
        margin-top: 1;
        align-horizontal: right;
    }
    """

    def __init__(self, asset: DiagramAsset) -> None:
        super().__init__()
        self.asset = asset

    def compose(self) -> ComposeResult:
        label = "Adapter" if self.asset.reference.kind == "package" else "ICSP wiring"
        origin = (
            "Bundled reference" if self.asset.origin == "bundled" else "Local image"
        )
        with Vertical(id="diagram-viewer"):
            yield Label(
                f"{label} · {self.asset.path.name} · {origin}",
                classes="eyebrow",
            )
            if self.asset.title:
                yield Static(self.asset.title, classes="diagram-caption", markup=False)
            with Vertical(id="diagram-viewer-scroll"):
                yield BitmapImage(self.asset.image, classes="diagram-large")
            with Horizontal(classes="buttons"):
                yield Button("Open image", id="viewer-open")
                yield Button("Close  ·  Esc", id="viewer-close", variant="primary")

    @on(Button.Pressed, "#viewer-open")
    def viewer_open(self) -> None:
        self.run_worker(self._open_asset(self.asset), exclusive=True)

    async def _open_asset(self, asset: DiagramAsset) -> None:
        if not await asyncio.to_thread(open_image, asset.path):
            self.app.notify("Could not open the diagram image.", severity="error")

    @on(Button.Pressed, "#viewer-close")
    def viewer_close(self) -> None:
        self.dismiss(None)

    def action_close(self) -> None:
        self.dismiss(None)


class DiagramCard(Vertical):
    """One image and its actions, mounted only after successful validation."""

    DEFAULT_CSS = """
    DiagramCard {
        height: auto;
        margin: 1 0;
        padding: 1;
        border: solid #304453;
        background: #17232e;
    }
    DiagramCard .diagram-caption { height: auto; color: #9cabb9; }
    DiagramCard .diagram-preview {
        width: auto;
        height: auto;
        min-height: 1;
        max-width: 100%;
        max-height: 16;
        margin: 1 0;
    }
    DiagramCard .buttons { height: 3; margin-top: 1; align-horizontal: right; }
    """

    def __init__(self, asset: DiagramAsset, index: int) -> None:
        super().__init__(id=f"diagram-card-{index}")
        self.asset = asset
        self.index = index

    def compose(self) -> ComposeResult:
        label = "Adapter" if self.asset.reference.kind == "package" else "ICSP wiring"
        origin = (
            "Bundled reference" if self.asset.origin == "bundled" else "Local image"
        )
        yield Label(
            f"{label} · {self.asset.path.name} · {origin}",
            classes="eyebrow",
        )
        if self.asset.title:
            yield Static(self.asset.title, classes="diagram-caption", markup=False)
        if BITMAP_RENDERER_AVAILABLE:
            yield BitmapImage(self.asset.image, classes="diagram-preview")
        else:
            yield Static(
                "Bitmap preview unavailable in this terminal.",
                classes="diagram-caption",
                markup=False,
            )
        with Horizontal(classes="buttons"):
            if BITMAP_RENDERER_AVAILABLE:
                yield Button("Enlarge", id=f"diagram-enlarge-{self.index}")
            yield Button("Open image", id=f"diagram-open-{self.index}")


class DeviceDiagrams(Vertical):
    """Asynchronously load the diagrams associated with one device."""

    DEFAULT_CSS = """
    DeviceDiagrams { height: auto; width: 100%; }
    """

    def __init__(self, *, id: str = "device-diagrams") -> None:
        super().__init__(id=id)
        self._generation = 0
        self._pending: tuple[str, str, Path | None, str] | None = None
        self._assets: dict[int, DiagramAsset] = {}
        self._worker: object | None = None
        self.display = False

    def on_mount(self) -> None:
        if self._pending is not None:
            pending = self._pending
            self._pending = None
            self._start_refresh(*pending)

    def clear(self) -> None:
        """Immediately hide and invalidate the currently displayed images."""

        self._generation += 1
        self._pending = None
        self._assets.clear()
        self.display = False
        for child in self.children:
            child.display = False

    def show_device(
        self,
        details: str,
        directory: str = "",
        executable: Path | None = None,
        programmer: str = "",
    ) -> None:
        """Clear current cards, then load the new device's images in a worker."""

        self.clear()
        self._pending = (details, directory, executable, programmer)
        if self.is_attached:
            pending = self._pending
            self._pending = None
            self._start_refresh(*pending)

    def _start_refresh(
        self,
        details: str,
        directory: str,
        executable: Path | None,
        programmer: str,
    ) -> None:
        generation = self._generation
        self._worker = self.run_worker(
            self._refresh(details, directory, executable, programmer, generation),
            name="diagram-refresh",
            group="diagram-refresh",
            exclusive=True,
        )

    async def _refresh(
        self,
        details: str,
        directory: str,
        executable: Path | None,
        programmer: str,
        generation: int,
    ) -> None:
        assets = await asyncio.to_thread(
            load_diagram_assets, details, directory or None, executable, programmer
        )
        if generation != self._generation or not self.is_attached:
            return
        await self.remove_children()
        if generation != self._generation or not self.is_attached:
            return
        self._assets = {index: asset for index, asset in enumerate(assets)}
        self.display = bool(assets)
        if assets:
            await self.mount(
                *(DiagramCard(asset, index) for index, asset in self._assets.items())
            )
            if generation != self._generation:
                for child in self.children:
                    child.display = False
                self.display = False

    @on(Button.Pressed)
    def card_action(self, event: Button.Pressed) -> None:
        button_id = event.button.id or ""
        parts = button_id.rsplit("-", 1)
        if len(parts) != 2:
            return
        try:
            index = int(parts[1])
        except ValueError:
            return
        asset = self._assets.get(index)
        if asset is None:
            return
        if parts[0].endswith("enlarge") and BITMAP_RENDERER_AVAILABLE:
            self.app.push_screen(DiagramViewerScreen(asset))
        elif parts[0].endswith("open"):
            self.run_worker(self._open_asset(asset), exclusive=True)

    async def _open_asset(self, asset: DiagramAsset) -> None:
        if not await asyncio.to_thread(open_image, asset.path):
            self.app.notify("Could not open the diagram image.", severity="error")
