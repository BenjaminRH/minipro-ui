"""Discover and load the adapter diagrams referenced by minipro metadata.

The minipro database stores diagram *names*, while users may provide the
corresponding JPEG files separately.  This module deliberately keeps the two
concerns separate: parsing is pure, and filesystem/image work can be run in a
worker thread by the UI.
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from PIL import Image as PILImage
from PIL import UnidentifiedImageError

_REFERENCE = re.compile(
    r"^\s*(Package|ICSP)\s*:\s*([^\s,;()]+\.jpe?g)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
# minipro and programmer-specific catalogs also use names such as
# ``T48ICP002.jpg`` and ``T56ICP014.jpg``.  Keep the metadata lookup bounded to
# a plain safe JPEG basename while accepting those references.
_KNOWN_FILENAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*\.jpe?g$", re.IGNORECASE)


@dataclass(frozen=True)
class DiagramReference:
    """A diagram filename named by one field in ``minipro -d`` output."""

    kind: str
    filename: str


@dataclass(frozen=True)
class DiagramAsset:
    """A validated, detached bitmap ready to pass to textual-image."""

    reference: DiagramReference
    path: Path
    image: PILImage.Image
    origin: str = "local"
    title: str = ""
    programmer: str = ""

    @property
    def width(self) -> int:
        return self.image.width

    @property
    def height(self) -> int:
        return self.image.height


def parse_diagram_references(details: str) -> tuple[DiagramReference, ...]:
    """Extract only the package and ICSP JPG references from device details.

    ``minipro`` emits one reference per line, for example ``ICSP: ICP009.JPG``.
    Paths and arbitrary image names are intentionally rejected; this prevents
    metadata from escaping the bounded search roots.
    """

    references: list[DiagramReference] = []
    seen: set[tuple[str, str]] = set()
    for match in _REFERENCE.finditer(details):
        kind = match.group(1).casefold()
        filename = Path(match.group(2)).name
        if filename != match.group(2) or not _KNOWN_FILENAME.fullmatch(filename):
            continue
        key = (kind, filename.casefold())
        if key not in seen:
            seen.add(key)
            references.append(DiagramReference(kind, filename))
    return tuple(references)


def _roots(
    directory: str | os.PathLike[str] | None, executable: Path | None
) -> tuple[Path, ...]:
    candidates: list[Path] = []
    if directory:
        candidates.append(Path(directory).expanduser())
        # An explicit folder is authoritative; it must not turn into a broad
        # scan of unrelated system folders.
        return tuple(candidates)

    home = os.environ.get("MINIPRO_HOME")
    if home:
        candidates.extend((Path(home), Path(home) / "share" / "minipro"))

    if executable is not None:
        executable_parent = executable.expanduser().resolve().parent
        candidates.extend(
            (
                executable_parent,
                executable_parent / "share" / "minipro",
                executable_parent.parent / "share" / "minipro",
                executable_parent.parent.parent / "share" / "minipro",
            )
        )

    if sys.platform == "darwin":
        candidates.extend(
            (Path("/opt/homebrew/share/minipro"), Path("/usr/local/share/minipro"))
        )
    elif sys.platform.startswith("linux"):
        candidates.extend(
            (Path("/usr/share/minipro"), Path("/usr/local/share/minipro"))
        )

    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        try:
            key = str(candidate.resolve()).casefold()
        except OSError:
            key = str(candidate).casefold()
        if key not in seen:
            seen.add(key)
            unique.append(candidate)
    return tuple(unique)


def _configured_directory(
    directory: str | os.PathLike[str] | None,
) -> Path | None:
    """Return a usable configured folder, treating invalid values as unset."""
    if directory is None:
        return None
    try:
        raw = os.fspath(directory)
        if isinstance(raw, str) and not raw.strip():
            return None
        path = Path(raw).expanduser()
        return path if path.is_dir() else None
    except (OSError, RuntimeError, ValueError):
        return None


def _safe_relative(value: object) -> Path | None:
    if not isinstance(value, str) or not value.strip() or "\\" in value:
        return None
    path = Path(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        return None
    return path


def _catalog_path() -> Path:
    return Path(__file__).parent / "assets" / "diagrams" / "catalog.json"


def _catalog_for(programmer: str) -> dict[str, tuple[Path, str]]:
    """Return bounded mappings for one explicit programmer family."""
    if not programmer:
        return {}
    try:
        raw = json.loads(_catalog_path().read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    if not isinstance(raw, dict) or raw.get("schema_version") != 1:
        return {}
    mappings = raw.get("mappings")
    assets = raw.get("assets")
    if not isinstance(mappings, dict) or not isinstance(assets, dict):
        return {}
    selected = next(
        (
            value
            for key, value in mappings.items()
            if str(key).casefold() == programmer.casefold()
        ),
        None,
    )
    if not isinstance(selected, dict):
        return {}
    selected_mappings: dict[str, tuple[Path, str]] = {}
    for reference, mapped in selected.items():
        mapped_path = _safe_relative(mapped)
        if mapped_path is None or mapped_path.suffix.casefold() not in {
            ".jpg",
            ".jpeg",
        }:
            continue
        metadata = next(
            (
                value
                for key, value in assets.items()
                if str(key).casefold() == mapped_path.as_posix().casefold()
            ),
            None,
        )
        if not isinstance(metadata, dict):
            continue
        if not isinstance(reference, str) or Path(reference).name != reference:
            continue
        selected_mappings[reference.casefold()] = (
            mapped_path,
            str(metadata.get("title", "")).strip(),
        )
    return selected_mappings


def _find_relative(root: Path, relative: Path) -> Path | None:
    """Resolve each relative path component case-insensitively."""
    current = root
    for component in relative.parts:
        try:
            match = next(
                entry
                for entry in current.iterdir()
                if entry.name.casefold() == component.casefold()
            )
        except (OSError, StopIteration):
            return None
        current = match
    return current if current.is_file() else None


def _candidate_directories(root: Path) -> tuple[Path, ...]:
    """Return root and its optional IMG directory, without recursive scanning."""

    result = [root]
    try:
        for entry in root.iterdir():
            if entry.is_dir() and entry.name.casefold() == "img":
                result.append(entry)
    except OSError:
        pass
    return tuple(result)


def resolve_diagram(
    reference: DiagramReference,
    directory: str | os.PathLike[str] | None = None,
    executable: Path | None = None,
    programmer: str = "",
) -> Path | None:
    """Find one referenced image by case-insensitive filename.

    An explicit programmer uses only its bounded catalog mapping.  The empty
    programmer form retains the original direct filename lookup for callers
    that use this helper independently of the application.
    """
    configured_directory = _configured_directory(directory)
    if programmer:
        mapping = _catalog_for(programmer)
        mapped = mapping.get(reference.filename.casefold())
        if mapped is None:
            return None
        mapped_path, _ = mapped
        # A configured folder is an explicit source of truth.  This lets a
        # user replace a bundled reference and avoids silently showing a
        # bundled image when their configured image set is incomplete.
        roots: list[tuple[Path, str]] = []
        if configured_directory is not None:
            roots.extend(
                (root, "local") for root in _roots(configured_directory, executable)
            )
        else:
            roots.append((_catalog_path().parent, "bundled"))
            roots.extend((root, "local") for root in _roots(None, executable))
        for root, _origin in roots:
            relative_candidates = (
                mapped_path,
                Path(mapped_path.name),
                Path("IMG") / mapped_path,
                Path("IMG") / mapped_path.name,
            )
            for relative in relative_candidates:
                if path := _find_relative(root, relative):
                    return path
        return None

    wanted = reference.filename.casefold()
    for root in _roots(configured_directory, executable):
        for folder in _candidate_directories(root):
            try:
                for entry in folder.iterdir():
                    if entry.is_file() and entry.name.casefold() == wanted:
                        return entry
            except OSError:
                continue
    return None


def load_diagram_assets(
    details: str,
    directory: str | os.PathLike[str] | None = None,
    executable: Path | None = None,
    programmer: str = "",
) -> tuple[DiagramAsset, ...]:
    """Resolve and decode valid images, omitting missing or corrupt files."""

    assets: list[DiagramAsset] = []
    mapping = _catalog_for(programmer) if programmer else {}
    configured_directory = _configured_directory(directory)
    for reference in parse_diagram_references(details):
        path = resolve_diagram(reference, directory, executable, programmer)
        if path is None:
            continue
        try:
            with PILImage.open(path) as opened:
                opened.load()
                image = opened.convert("RGBA")
        except (
            OSError,
            ValueError,
            UnidentifiedImageError,
            PILImage.DecompressionBombError,
            PILImage.DecompressionBombWarning,
        ):
            continue
        origin = "local"
        title = ""
        if programmer and mapping.get(reference.filename.casefold()) is not None:
            mapped_path, title = mapping[reference.filename.casefold()]
            if configured_directory is None and path.is_relative_to(
                _catalog_path().parent
            ):
                origin = "bundled"
            elif path.name.casefold() == mapped_path.name.casefold():
                origin = "local"
        assets.append(DiagramAsset(reference, path, image, origin, title, programmer))
    return tuple(assets)
