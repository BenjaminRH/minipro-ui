"""Generate independently authored programmer diagrams from factual data.

The manifest is deliberately data-only.  It may contain facts checked against
an image supplied by a programmer vendor, but this module never copies or
embeds that artwork.  SVG is the editable source and a same-basename JPEG is
produced for terminals that can display raster images.

The small drawing language in this module is shared by the SVG and JPEG
renderers.  Keeping the geometry in one place makes the two deliverables
visually equivalent and keeps the output deterministic.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

_FILENAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*\.jpe?g$", re.IGNORECASE)
_WIDTH = 1400
_HEIGHT = 900
_MARGIN = 48
_INK = "#17202a"
_MUTED = "#536273"
_ACCENT = "#1d5f8f"
_PALE = "#eaf3f8"
_LINE = "#9db2c2"
_WHITE = "#ffffff"

_ASCII_REPLACEMENTS = str.maketrans(
    {
        "–": "-",
        "—": "-",
        "‑": "-",
        "−": "-",
        "→": "->",
        "←": "<-",
        "↔": "<->",
        "µ": "u",
        "μ": "u",
        "Ω": "ohm",
        "Ω": "ohm",
        "·": " / ",
        "×": "x",
        "≤": "<=",
        "≥": ">=",
        " ": " ",
        "“": '"',
        "”": '"',
        "‘": "'",
        "’": "'",
    }
)


def _ascii_text(value: str) -> str:
    """Convert source text to glyphs supported by the bundled Pillow font."""

    return (
        value.translate(_ASCII_REPLACEMENTS).encode("ascii", "replace").decode("ascii")
    )


class DiagramValidationError(ValueError):
    """Raised when a manifest could encode an ambiguous or unsafe diagram."""


@dataclass(frozen=True)
class Connection:
    pins: tuple[int, ...]
    signal: str
    target: str
    direction: str = ""
    optional: bool = False


@dataclass(frozen=True)
class Diagram:
    id: str
    filename: str
    asset_group: str
    kind: str
    title: str
    programmers: tuple[str, ...]
    connection_heading: str
    models: tuple[str, ...]
    metadata_refs: tuple[str, ...]
    connector: Mapping[str, Any] | None
    connections: tuple[Connection, ...]
    facts: tuple[str, ...]
    notes: tuple[str, ...]
    schematic: Mapping[str, Any] | None
    adapter_visual: Mapping[str, Any] | None


def _string(value: Any, field: str, *, required: bool = True) -> str:
    if not isinstance(value, str) or (required and not value.strip()):
        raise DiagramValidationError(f"{field} must be a non-empty string")
    return value.strip()


def _strings(
    value: Any,
    field: str,
    *,
    required: bool = False,
    unique: bool = False,
) -> tuple[str, ...]:
    if value is None and not required:
        return ()
    if not isinstance(value, list):
        raise DiagramValidationError(f"{field} must be a list of strings")
    result = tuple(
        _string(item, f"{field}[{index}]") for index, item in enumerate(value)
    )
    if unique:
        seen: set[str] = set()
        deduplicated: list[str] = []
        for item in result:
            key = item.casefold()
            if key not in seen:
                seen.add(key)
                deduplicated.append(item)
        result = tuple(deduplicated)
    return result


def _pins(value: Any, field: str) -> tuple[int, ...]:
    if not isinstance(value, list) or not value:
        raise DiagramValidationError(f"{field} must be a non-empty list of integers")
    result = tuple(value)
    if any(
        not isinstance(pin, int) or isinstance(pin, bool) or pin < 1 for pin in result
    ):
        raise DiagramValidationError(f"{field} must contain positive integers")
    if len(set(result)) != len(result):
        raise DiagramValidationError(f"{field} contains duplicate pins")
    return result


def _safe_relative(value: Any, field: str, *, default: str = "") -> str:
    """Accept a portable relative group/path, never an absolute filesystem path."""

    if value is None and default:
        return default
    text = _string(value, field, required=bool(default))
    path = Path(text)
    if (
        "\\" in text
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise DiagramValidationError(f"{field} must be a safe relative path")
    return "/".join(path.parts)


def _parse_connector(value: Any, field: str) -> Mapping[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise DiagramValidationError(f"{field} must be an object")
    pin_count = value.get("pin_count")
    if not isinstance(pin_count, int) or isinstance(pin_count, bool) or pin_count < 1:
        raise DiagramValidationError(f"{field}.pin_count must be a positive integer")
    orientation = _string(value.get("orientation"), f"{field}.orientation")
    rows = value.get("rows")
    if not isinstance(rows, list) or not rows:
        raise DiagramValidationError(f"{field}.rows must be a non-empty list")
    row_pins: list[int] = []
    clean_rows: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        if isinstance(row, Mapping):
            pins = _pins(row.get("pins"), f"{field}.rows[{index}].pins")
            label = _string(
                row.get("label", ""), f"{field}.rows[{index}].label", required=False
            )
            clean_rows.append({"pins": list(pins), "label": label})
        else:
            pins = _pins(row, f"{field}.rows[{index}]")
            clean_rows.append({"pins": list(pins), "label": ""})
        row_pins.extend(pins)
    if len(set(row_pins)) != len(row_pins):
        raise DiagramValidationError(f"{field}.rows contains duplicate pins")
    if any(pin > pin_count for pin in row_pins):
        raise DiagramValidationError(f"{field}.rows contains a pin outside pin_count")
    return {"pin_count": pin_count, "rows": clean_rows, "orientation": orientation}


def _parse_connections(
    value: Any, connector: Mapping[str, Any] | None
) -> tuple[Connection, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise DiagramValidationError("connections must be a list")
    result: list[Connection] = []
    by_pin: dict[int, list[tuple[str, str, str]]] = {}
    pin_count = connector.get("pin_count") if connector else None
    for index, item in enumerate(value):
        if not isinstance(item, Mapping):
            raise DiagramValidationError(f"connections[{index}] must be an object")
        pins = _pins(item.get("pins"), f"connections[{index}].pins")
        if pin_count is not None and any(pin > pin_count for pin in pins):
            raise DiagramValidationError(
                f"connections[{index}] contains a pin outside pin_count"
            )
        connection = Connection(
            pins=pins,
            signal=_string(item.get("signal"), f"connections[{index}].signal"),
            target=_string(item.get("target"), f"connections[{index}].target"),
            direction=_string(
                item.get("direction", ""),
                f"connections[{index}].direction",
                required=False,
            ),
            optional=bool(item.get("optional", False)),
        )
        identity = (connection.signal, connection.target, connection.direction)
        for pin in pins:
            prior_connections = by_pin.setdefault(pin, [])
            if any(
                prior[0] == identity[0] and prior != identity
                for prior in prior_connections
            ):
                raise DiagramValidationError(f"pin {pin} has conflicting connections")
            if identity not in prior_connections:
                prior_connections.append(identity)
        result.append(connection)
    return tuple(result)


def _parse_schematic(value: Any, field: str) -> Mapping[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise DiagramValidationError(f"{field} must be an object")
    kind = _string(value.get("kind"), f"{field}.kind")
    if kind != "signals":
        raise DiagramValidationError(f"{field}.kind must be 'signals'")
    ports = value.get("ports")
    if not isinstance(ports, list) or not ports:
        raise DiagramValidationError(f"{field}.ports must be a non-empty list")
    clean_ports: list[dict[str, Any]] = []
    for index, port in enumerate(ports):
        if not isinstance(port, Mapping):
            raise DiagramValidationError(f"{field}.ports[{index}] must be an object")
        connected = bool(port.get("connected", True))
        clean_ports.append(
            {
                "source": _string(port.get("source"), f"{field}.ports[{index}].source"),
                "target": _string(
                    port.get("target", ""),
                    f"{field}.ports[{index}].target",
                    required=connected,
                ),
                "connected": connected,
                "optional": bool(port.get("optional", False)),
            }
        )
    return {
        "kind": "signals",
        "source_label": _string(value.get("source_label"), f"{field}.source_label"),
        "target_label": _string(value.get("target_label"), f"{field}.target_label"),
        "input_label": _string(value.get("input_label"), f"{field}.input_label"),
        "ports": clean_ports,
        "external_power": _string(
            value.get("external_power", ""),
            f"{field}.external_power",
            required=False,
        ),
        "optional_note": _string(
            value.get("optional_note", ""), f"{field}.optional_note", required=False
        ),
    }


def _parse_adapter_visual(value: Any, field: str) -> Mapping[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise DiagramValidationError(f"{field} must be an object")
    package_pins = value.get("package_pins")
    if package_pins is not None and (
        not isinstance(package_pins, int)
        or isinstance(package_pins, bool)
        or package_pins < 1
    ):
        raise DiagramValidationError(f"{field}.package_pins must be a positive integer")
    package_shape = _string(
        value.get("package_shape", "dual"),
        f"{field}.package_shape",
        required=False,
    ).casefold()
    if package_shape not in {"dual", "quad", "block"}:
        raise DiagramValidationError(
            f"{field}.package_shape must be dual, quad, or block"
        )
    result: dict[str, Any] = {
        "kind": "adapter",
        "package_shape": package_shape,
        "package_label": _string(value.get("package_label"), f"{field}.package_label"),
        "adapter_label": _string(value.get("adapter_label"), f"{field}.adapter_label"),
        "programmer_label": _string(
            value.get("programmer_label"), f"{field}.programmer_label"
        ),
        "pin1_label": _string(value.get("pin1_label"), f"{field}.pin1_label"),
        "placement_note": _string(
            value.get("placement_note"), f"{field}.placement_note"
        ),
        "interface_label": _string(
            value.get("interface_label", ""),
            f"{field}.interface_label",
            required=False,
        ),
    }
    if package_pins is not None:
        result["package_pins"] = package_pins
    for key in ("interface_labels_left", "interface_labels_right"):
        labels = value.get(key)
        if labels is not None:
            if not isinstance(labels, list) or any(
                not isinstance(label, str) for label in labels
            ):
                raise DiagramValidationError(f"{field}.{key} must be a list of strings")
            # Empty entries reserve a physical contact without asserting a
            # signal name; this is useful when only some package-side pins are
            # established by the source facts.
            result[key] = [label.strip() for label in labels]
    interface_position = _string(
        value.get("interface_position", ""),
        f"{field}.interface_position",
        required=False,
    ).casefold()
    if interface_position and interface_position not in {
        "top",
        "bottom",
        "left",
        "right",
    }:
        raise DiagramValidationError(
            f"{field}.interface_position must be top, bottom, left, or right"
        )
    if interface_position:
        result["interface_position"] = interface_position
    interface_pin_count = value.get("interface_pin_count")
    if interface_pin_count is not None:
        if (
            not isinstance(interface_pin_count, int)
            or isinstance(interface_pin_count, bool)
            or interface_pin_count < 1
        ):
            raise DiagramValidationError(
                f"{field}.interface_pin_count must be a positive integer"
            )
        result["interface_pin_count"] = interface_pin_count
    return result


def validate_manifest(manifest: Mapping[str, Any]) -> tuple[Diagram, ...]:
    """Validate and normalize a version-one diagram manifest.

    A missing connector is meaningful: it says the source did not establish a
    connector layout, so the renderer will produce a connection table or
    technical reference card without inventing one.
    """

    if manifest.get("schema_version") != 1:
        raise DiagramValidationError("schema_version must be 1")
    raw_diagrams = manifest.get("diagrams")
    if not isinstance(raw_diagrams, list):
        raise DiagramValidationError("diagrams must be a list")
    result: list[Diagram] = []
    seen: set[str] = set()
    seen_ids: set[str] = set()
    seen_mappings: set[tuple[str, str]] = set()
    for index, raw in enumerate(raw_diagrams):
        if not isinstance(raw, Mapping):
            raise DiagramValidationError(f"diagrams[{index}] must be an object")
        diagram_id = _string(raw.get("id"), f"diagrams[{index}].id")
        id_key = diagram_id.casefold()
        if id_key in seen_ids:
            raise DiagramValidationError(f"duplicate diagram id: {diagram_id}")
        seen_ids.add(id_key)
        filename = _string(raw.get("filename"), f"diagrams[{index}].filename")
        if Path(filename).name != filename or not _FILENAME.fullmatch(filename):
            raise DiagramValidationError(f"invalid diagram filename: {filename!r}")
        asset_group = _safe_relative(
            raw.get("asset_group"), f"diagrams[{index}].asset_group", default="default"
        )
        key = f"{asset_group.casefold()}::{filename.casefold()}"
        if key in seen:
            raise DiagramValidationError(
                f"duplicate diagram filename in asset group {asset_group}: {filename}"
            )
        seen.add(key)
        kind = _string(raw.get("kind"), f"diagrams[{index}].kind").casefold()
        if kind not in {"wiring", "adapter"}:
            raise DiagramValidationError(f"unsupported diagram kind: {kind}")
        programmers = _strings(
            raw.get("programmers"),
            f"diagrams[{index}].programmers",
            required=True,
            unique=True,
        )
        models = _strings(raw.get("models"), f"diagrams[{index}].models")
        connection_heading = _string(
            raw.get("connection_heading", ""),
            f"diagrams[{index}].connection_heading",
            required=False,
        )
        if not connection_heading:
            connection_heading = (
                "Programmer pin mapping" if kind == "wiring" else "Adapter pin mapping"
            )
        metadata_refs = _strings(
            raw.get("metadata_refs"), f"diagrams[{index}].metadata_refs"
        ) or (filename,)
        if any(
            Path(reference).name != reference or not _FILENAME.fullmatch(reference)
            for reference in metadata_refs
        ):
            raise DiagramValidationError(
                f"diagrams[{index}].metadata_refs must be bare JPEG basenames"
            )
        for programmer in programmers:
            for reference in metadata_refs:
                mapping_key = (programmer.casefold(), reference.casefold())
                if mapping_key in seen_mappings:
                    raise DiagramValidationError(
                        "duplicate programmer and metadata reference mapping: "
                        f"{programmer} / {reference}"
                    )
                seen_mappings.add(mapping_key)
        connector = _parse_connector(
            raw.get("connector"), f"diagrams[{index}].connector"
        )
        connections = _parse_connections(raw.get("connections", []), connector)
        schematic = _parse_schematic(
            raw.get("schematic"), f"diagrams[{index}].schematic"
        )
        adapter_visual = _parse_adapter_visual(
            raw.get("adapter_visual", raw.get("visual")),
            f"diagrams[{index}].adapter_visual",
        )
        result.append(
            Diagram(
                id=diagram_id,
                filename=filename,
                asset_group=asset_group,
                kind=kind,
                title=_string(raw.get("title"), f"diagrams[{index}].title"),
                programmers=programmers,
                connection_heading=connection_heading,
                models=models,
                metadata_refs=metadata_refs,
                connector=connector,
                connections=connections,
                facts=_strings(raw.get("facts"), f"diagrams[{index}].facts"),
                notes=_strings(raw.get("notes"), f"diagrams[{index}].notes"),
                schematic=schematic,
                adapter_visual=adapter_visual,
            )
        )
    return tuple(result)


def validate_reference_directory(
    diagrams: Sequence[Diagram], directory: str | Path
) -> None:
    """Optionally assert that an external reference set has matching names."""

    root = Path(directory).expanduser().resolve()
    if not root.is_dir():
        raise DiagramValidationError(
            f"reference image directory is not a directory: {root}"
        )
    for diagram in diagrams:
        group_path = root / diagram.asset_group / diagram.filename
        direct_path = root / diagram.filename
        if not group_path.is_file() and not direct_path.is_file():
            raise DiagramValidationError(
                f"reference image is missing: {diagram.asset_group}/{diagram.filename}"
            )


def _wrap_pixels(text: str, width: int, size: int, bold: bool = False) -> list[str]:
    """Wrap text using the same bundled Pillow font used by the JPEG renderer."""

    text = _ascii_text(text)
    words = text.split()
    if not words:
        return [""]
    font = _font(size, bold)
    draw = ImageDraw.Draw(Image.new("RGB", (1, 1)))

    def fits(value: str) -> bool:
        return draw.textlength(value, font=font) <= width

    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if current and not fits(candidate):
            lines.append(current)
            current = word
        elif not current and not fits(word):
            chunk = ""
            for character in word:
                if chunk and not fits(chunk + character):
                    lines.append(chunk)
                    chunk = character
                else:
                    chunk += character
            current = chunk
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def _measure_pixels(text: str, size: int, bold: bool = False) -> int:
    font = _font(size, bold)
    draw = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    return int(draw.textlength(_ascii_text(text), font=font))


def _text_bbox(op: _Op) -> tuple[float, float, float, float]:
    x, y, value, size, _fill, bold = op.values
    font = _font(size, bold)
    draw = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    left, top, right, bottom = draw.textbbox((x, y - size), str(value), font=font)
    return left, top, right, bottom


def _validate_scene_bounds(scene: _Scene) -> None:
    """Assert that every primitive fits the final canvas and its outer margin."""

    for op in scene.ops:
        if op.kind == "rect":
            x, y, width, height, _fill, _stroke = op.values
            bounds = (x, y, x + width, y + height)
        elif op.kind == "line":
            x1, y1, x2, y2, _stroke, _width, _dashed = op.values
            bounds = (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))
        elif op.kind == "circle":
            x, y, radius, _fill, _stroke = op.values
            bounds = (x - radius, y - radius, x + radius, y + radius)
        else:
            bounds = _text_bbox(op)
        if (
            bounds[0] < 0
            or bounds[1] < 0
            or bounds[2] > _WIDTH
            or bounds[3] > scene.height
        ):
            raise DiagramValidationError(
                f"rendered primitive is outside canvas: {op.kind}"
            )


@dataclass(frozen=True)
class _Op:
    kind: str
    values: tuple[Any, ...]


class _Scene:
    def __init__(self, height: int = _HEIGHT) -> None:
        self.height = height
        self.ops: list[_Op] = []

    def rect(
        self,
        x: int,
        y: int,
        width: int,
        height: int,
        fill: str = _WHITE,
        stroke: str = _LINE,
    ) -> None:
        self.ops.append(_Op("rect", (x, y, width, height, fill, stroke)))

    def line(
        self,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        stroke: str = _LINE,
        width: int = 2,
        dashed: bool = False,
    ) -> None:
        self.ops.append(_Op("line", (x1, y1, x2, y2, stroke, width, dashed)))

    def circle(
        self, x: int, y: int, radius: int, fill: str = _PALE, stroke: str = _ACCENT
    ) -> None:
        self.ops.append(_Op("circle", (x, y, radius, fill, stroke)))

    def text(
        self,
        x: int,
        y: int,
        value: str,
        size: int = 20,
        fill: str = _INK,
        bold: bool = False,
    ) -> None:
        self.ops.append(_Op("text", (x, y, _ascii_text(value), size, fill, bold)))


def _scene_for(diagram: Diagram) -> _Scene:
    scene = _Scene()
    title_lines = _wrap_pixels(diagram.title, _WIDTH - 2 * _MARGIN, 34, True)
    for index, line in enumerate(title_lines):
        scene.text(_MARGIN, 54 + index * 40, line, 34, _INK, True)
    reference_y = 91 + (len(title_lines) - 1) * 40
    reference_lines = _wrap_pixels(
        f"{diagram.kind.title()} reference · {diagram.filename}",
        _WIDTH - 2 * _MARGIN,
        18,
    )
    for index, line in enumerate(reference_lines):
        scene.text(_MARGIN, reference_y + index * 24, line, 18, _MUTED)
    compatibility = "Programmer compatibility: " + ", ".join(diagram.programmers)
    compatibility_y = reference_y + len(reference_lines) * 24 + 10
    compatibility_lines = _wrap_pixels(compatibility, _WIDTH - 2 * _MARGIN, 20, True)
    for index, line in enumerate(compatibility_lines):
        scene.text(_MARGIN, compatibility_y + index * 26, line, 20, _ACCENT, True)
    content_y = compatibility_y + len(compatibility_lines) * 26 + 20
    if diagram.kind == "wiring":
        _wiring_scene(scene, diagram, content_y)
    else:
        _adapter_scene(scene, diagram, content_y)
    scene.ops.insert(0, _Op("rect", (0, 0, _WIDTH, scene.height, _WHITE, _WHITE)))
    _validate_scene_bounds(scene)
    return scene


def _connector_scene(
    scene: _Scene, connector: Mapping[str, Any], x: int, y: int, width: int, height: int
) -> None:
    scene.rect(x, y, width, height, _PALE, _ACCENT)
    scene.text(x + 20, y + 36, "Connector", 24, _INK, True)
    orientation_lines = _wrap_pixels(str(connector["orientation"]), width - 40, 14)
    scene.text(
        x + 20,
        y + 67,
        f"{connector['pin_count']} pins",
        16,
        _MUTED,
    )
    for index, line in enumerate(orientation_lines):
        scene.text(x + 20, y + 89 + index * 20, line, 14, _MUTED)
    rows = connector["rows"]
    row_start = y + 112 + (len(orientation_lines) - 1) * 20
    row_height = max(34, min(50, (height - (row_start - y) - 20) // max(1, len(rows))))
    for row_index, row in enumerate(rows):
        pins = row["pins"]
        cy = row_start + row_index * row_height
        spacing = max(24, (width - 80) // max(1, len(pins)))
        radius = min(15, max(9, spacing // 3))
        pin_size = 14 if radius >= 13 else 10
        for pin_index, pin in enumerate(pins):
            cx = x + 36 + pin_index * spacing
            scene.circle(cx, cy, radius, _WHITE, _ACCENT)
            scene.text(
                cx - (pin_size // 3),
                cy + (pin_size // 2),
                str(pin),
                pin_size,
                _INK,
                pin == 1,
            )
        if row.get("label"):
            scene.text(x + 20, cy + 30, row["label"], 14, _MUTED)


def _connector_height(connector: Mapping[str, Any]) -> int:
    orientation_lines = _wrap_pixels(str(connector["orientation"]), 510, 14)
    row_height = 44 if len(connector["rows"]) <= 8 else 36
    return (
        112
        + (len(orientation_lines) - 1) * 20
        + row_height * len(connector["rows"])
        + 28
    )


def _connection_table(
    scene: _Scene,
    connections: Sequence[Connection],
    x: int,
    y: int,
    width: int,
    heading: str,
) -> int:
    pin_labels: list[str] = []
    labels: list[str] = []
    for connection in connections or (
        Connection((), "No pin mapping", "recorded", "", False),
    ):
        pin_label = (
            ", ".join(str(pin) for pin in connection.pins) if connection.pins else "-"
        )
        suffix = " (optional)" if connection.optional else ""
        direction = f" [{connection.direction}]" if connection.direction else ""
        pin_labels.append(pin_label)
        labels.append(f"{connection.signal} -> {connection.target}{direction}{suffix}")
    # Keep a real gutter between the pin list and the description.  The pin
    # column grows to fit ordinary lists, but remains bounded so an unusually
    # long list wraps inside its own column instead of covering the signal.
    longest_pin = max(
        (_measure_pixels(label, 16, True) for label in pin_labels), default=0
    )
    pin_width = min(220, max(110, longest_pin + 10))
    signal_x = x + 18 + pin_width + 20
    signal_width = max(120, width - pin_width - 56)
    rows: list[tuple[list[str], list[str]]] = [
        (
            _wrap_pixels(pin_label, pin_width, 16, True),
            _wrap_pixels(label, signal_width, 16),
        )
        for pin_label, label in zip(pin_labels, labels, strict=True)
    ]
    row_height = [
        max(len(pin_lines), len(signal_lines), 1) * 22 + 14
        for pin_lines, signal_lines in rows
    ]
    table_height = 70 + sum(row_height)
    scene.rect(x, y, width, table_height, _WHITE, _LINE)
    scene.text(x + 18, y + 34, heading, 23, _INK, True)
    scene.text(x + 18, y + 61, "Pin", 14, _MUTED, True)
    scene.text(signal_x, y + 61, "Signal -> target", 14, _MUTED, True)
    cursor = y + 91
    for (pin_lines, signal_lines), height in zip(rows, row_height, strict=True):
        for line_index, line in enumerate(pin_lines):
            scene.text(x + 18, cursor + line_index * 22, line, 16, _INK, True)
        for line_index, line in enumerate(signal_lines):
            scene.text(signal_x, cursor + line_index * 22, line, 16, _INK)
        cursor += height
    return y + table_height


def _facts(scene: _Scene, diagram: Diagram, x: int, y: int, width: int) -> int:
    scene.text(x, y, "Connections and notes", 20, _INK, True)
    lines: list[str] = []
    for fact in diagram.facts:
        wrapped = _wrap_pixels(fact, max(120, width - 40), 16)
        lines.append("* " + wrapped[0])
        lines.extend("  " + line for line in wrapped[1:])
    for note in diagram.notes:
        wrapped = _wrap_pixels(note, max(120, width - 40), 16)
        lines.append("Note: " + wrapped[0])
        lines.extend("       " + line for line in wrapped[1:])
    if not lines:
        lines.append("No additional facts recorded.")
    for index, line in enumerate(lines):
        scene.text(x, y + 30 + index * 25, line, 16, _MUTED)
    return y + 30 + len(lines) * 25


def _signals_scene(
    scene: _Scene, diagram: Diagram, schematic: Mapping[str, Any], y: int
) -> None:
    ports = schematic["ports"]
    box_height = 100 + len(ports) * 34 + (48 if schematic["external_power"] else 0)
    source_x, target_x, box_width = _MARGIN, 922, 430
    scene.rect(source_x, y, box_width, box_height, _PALE, _ACCENT)
    scene.rect(target_x, y, box_width, box_height, _PALE, _ACCENT)
    scene.text(source_x + 20, y + 34, schematic["source_label"], 22, _INK, True)
    scene.text(source_x + 20, y + 64, schematic["input_label"], 16, _MUTED)
    scene.text(target_x + 20, y + 34, schematic["target_label"], 22, _INK, True)
    scene.text(target_x + 20, y + 64, "Target-side named terminals", 16, _MUTED)
    for index, port in enumerate(ports):
        cy = y + 102 + index * 34
        suffix = " (optional)" if port["optional"] else ""
        scene.text(source_x + 20, cy, f"{port['source']}{suffix}", 16, _INK, True)
        if port["connected"]:
            scene.text(target_x + 20, cy, port["target"], 16, _INK, True)
            scene.line(
                source_x + box_width,
                cy - 6,
                target_x,
                cy - 6,
                _ACCENT,
                2,
                bool(port["optional"]),
            )
        else:
            scene.text(source_x + box_width - 52, cy, "NC", 14, _MUTED, True)
            scene.line(
                source_x + box_width,
                cy - 6,
                source_x + box_width + 25,
                cy - 6,
                _MUTED,
                2,
            )
            scene.line(
                source_x + box_width + 18,
                cy - 14,
                source_x + box_width + 32,
                cy + 2,
                _MUTED,
                2,
            )
            scene.line(
                source_x + box_width + 32,
                cy - 14,
                source_x + box_width + 18,
                cy + 2,
                _MUTED,
                2,
            )
    note_y = y + box_height + 24
    if schematic["external_power"]:
        node_x, node_y = 500, note_y
        scene.rect(node_x, node_y, 380, 64, _PALE, _ACCENT)
        scene.text(node_x + 20, node_y + 28, "External power", 18, _INK, True)
        scene.text(node_x + 190, node_y + 28, schematic["external_power"], 16, _MUTED)
        # Route the separate supply to a labelled terminal inside the target
        # box.  The line must visibly terminate on that terminal rather than
        # ending below the box as if the supply were unconnected.
        target_terminal_y = y + box_height - 28
        scene.circle(target_x, target_terminal_y - 6, 7, _WHITE, _ACCENT)
        scene.text(
            target_x + 18, target_terminal_y, "external VCC terminal", 15, _MUTED
        )
        scene.line(node_x + 380, node_y + 32, target_x - 24, node_y + 32, _ACCENT, 2)
        scene.line(
            target_x - 24, node_y + 32, target_x - 24, target_terminal_y - 6, _ACCENT, 2
        )
        scene.line(
            target_x - 24,
            target_terminal_y - 6,
            target_x,
            target_terminal_y - 6,
            _ACCENT,
            2,
        )
        note_y += 90
    if schematic["optional_note"]:
        note_lines = _wrap_pixels(
            "Connection note: " + schematic["optional_note"],
            _WIDTH - 2 * _MARGIN,
            16,
        )
        for index, line in enumerate(note_lines):
            scene.text(_MARGIN, note_y + index * 24, line, 16, _MUTED)
        note_y += len(note_lines) * 24
    facts_end = _facts(scene, diagram, _MARGIN, note_y + 10, _WIDTH - 2 * _MARGIN)
    scene.height = max(_HEIGHT, facts_end + _MARGIN)


def _pin_distribution(pin_count: int, shape: str) -> list[int]:
    """Distribute every established package contact around its outline."""

    sides = 2 if shape == "dual" else 4
    base, extra = divmod(pin_count, sides)
    return [base + (1 if index < extra else 0) for index in range(sides)]


def _lead_positions(start: int, length: int, count: int) -> list[int]:
    if count <= 0:
        return []
    if count == 1:
        return [start + length // 2]
    return [start + index * length // (count - 1) for index in range(count)]


def _package_contacts(
    scene: _Scene,
    body_x: int,
    body_y: int,
    body_width: int,
    body_height: int,
    shape: str,
    pin_count: int | None,
) -> None:
    """Draw one pad for every known package pin without inventing numbering."""

    if not pin_count:
        return
    distribution = _pin_distribution(pin_count, shape)
    side_length_y = body_height - 32
    side_length_x = body_width - 32
    # dual: left and right. quad/block: left, right, top, bottom.
    for side, count in enumerate(distribution):
        if side < 2:
            for lead_y in _lead_positions(body_y + 16, side_length_y, count):
                if side == 0:
                    scene.line(body_x - 24, lead_y, body_x, lead_y, _ACCENT, 3)
                else:
                    scene.line(
                        body_x + body_width,
                        lead_y,
                        body_x + body_width + 24,
                        lead_y,
                        _ACCENT,
                        3,
                    )
        else:
            for lead_x in _lead_positions(body_x + 16, side_length_x, count):
                if side == 2:
                    scene.line(lead_x, body_y - 24, lead_x, body_y, _ACCENT, 3)
                else:
                    scene.line(
                        lead_x,
                        body_y + body_height,
                        lead_x,
                        body_y + body_height + 24,
                        _ACCENT,
                        3,
                    )


def _interface_count(visual: Mapping[str, Any]) -> int:
    explicit = visual.get("interface_pin_count")
    if isinstance(explicit, int):
        return explicit
    labels = list(visual.get("interface_labels_left", [])) + list(
        visual.get("interface_labels_right", [])
    )
    if labels:
        return sum(bool(_interface_display_label(label)) for label in labels)
    match = re.search(r"(\d+)\s*-?pin", str(visual.get("interface_label", "")), re.I)
    if match:
        return int(match.group(1))
    return 0


def _interface_display_label(value: str) -> str:
    """Treat reserved unlabeled slots as contacts without a signal caption."""

    return "" if value.strip().casefold() in {"unlabeled", "unlabelled"} else value


def _draw_interface(
    scene: _Scene,
    visual: Mapping[str, Any],
    adapter_x: int,
    adapter_y: int,
    adapter_width: int,
    adapter_height: int,
    socket_x: int,
    socket_y: int,
    socket_width: int,
    socket_height: int,
) -> None:
    label = str(visual.get("interface_label", ""))
    left_labels = list(visual.get("interface_labels_left", []))
    right_labels = list(visual.get("interface_labels_right", []))
    count = _interface_count(visual)
    if not label and not count:
        return
    position = str(visual.get("interface_position", "bottom"))
    if position in {"top", "bottom"}:
        if left_labels or right_labels:
            # The KB901x adapter has a compact, paired SPI contact block.  It
            # is separate from the large package footprint: each side keeps
            # the source-established top-to-bottom order, including reserved
            # (blank) positions.
            connector_width, connector_height = 282, 140
            connector_x = adapter_x + 235
            if position == "top":
                connector_y = adapter_y + 56
            else:
                connector_y = adapter_y + adapter_height - 170
            scene.rect(
                connector_x,
                connector_y,
                connector_width,
                connector_height,
                _WHITE,
                _ACCENT,
            )
            if label:
                scene.text(connector_x + 10, connector_y + 20, label, 13, _INK, True)
            pair_count = max(len(left_labels), len(right_labels))
            pair_positions = _lead_positions(
                connector_y + 45, connector_height - 60, pair_count
            )
            for index, py in enumerate(pair_positions):
                scene.line(
                    connector_x + 84,
                    py,
                    connector_x + 104,
                    py,
                    _ACCENT,
                    4,
                )
                scene.line(
                    connector_x + 170,
                    py,
                    connector_x + 190,
                    py,
                    _ACCENT,
                    4,
                )
                left_label = (
                    _interface_display_label(left_labels[index])
                    if index < len(left_labels)
                    else ""
                )
                right_label = (
                    _interface_display_label(right_labels[index])
                    if index < len(right_labels)
                    else ""
                )
                if left_label:
                    scene.text(
                        connector_x + 8,
                        py + 5,
                        left_label,
                        13,
                        _MUTED,
                    )
                if right_label:
                    scene.text(
                        connector_x + 204,
                        py + 5,
                        right_label,
                        13,
                        _MUTED,
                    )
            return
        if position == "top":
            connector_height = 94
            connector_y = adapter_y + 56
        else:
            connector_height = 46
            connector_y = adapter_y + adapter_height - 64
        connector_x, connector_width = adapter_x + 56, adapter_width - 112
        scene.rect(
            connector_x, connector_y, connector_width, connector_height, _WHITE, _ACCENT
        )
        if label:
            label_x = connector_x + (260 if position == "top" else 12)
            scene.text(label_x, connector_y + 22, label, 14, _INK, True)
        pad_count = max(count, 1)
        usable = connector_width - 24
        for index in range(pad_count):
            px = connector_x + 14 + index * usable // max(1, pad_count - 1)
            scene.line(
                px,
                connector_y + connector_height - 12,
                px,
                connector_y + connector_height,
                _ACCENT,
                3,
            )
        for index, text in enumerate(left_labels):
            if text:
                label_y = (
                    connector_y + 22 + index * 18
                    if position == "top"
                    else connector_y - 4 - index * 18
                )
                scene.text(connector_x + 12, label_y, text, 13, _MUTED)
        for index, text in enumerate(right_labels):
            if text:
                label_y = (
                    connector_y + 22 + index * 18
                    if position == "top"
                    else connector_y - 4 - index * 18
                )
                scene.text(
                    connector_x + connector_width - 110,
                    label_y,
                    text,
                    13,
                    _MUTED,
                )
        return
    # Side-positioned interfaces are represented as a short ribbon with the
    # established labels beside each contact; no board traces are implied.
    connector_width, connector_height = 100, max(70, 24 * max(count, 1))
    connector_x = (
        adapter_x + 16
        if position == "left"
        else adapter_x + adapter_width - connector_width - 16
    )
    connector_y = adapter_y + (adapter_height - connector_height) // 2
    scene.rect(
        connector_x, connector_y, connector_width, connector_height, _WHITE, _ACCENT
    )
    if label:
        scene.text(connector_x + 8, connector_y + 18, label, 12, _INK, True)
    for index in range(max(count, 1)):
        py = connector_y + 34 + index * (connector_height - 42) // max(1, count - 1)
        if position == "left":
            scene.line(
                connector_x + connector_width - 10,
                py,
                connector_x + connector_width,
                py,
                _ACCENT,
                3,
            )
        else:
            scene.line(connector_x, py, connector_x + 10, py, _ACCENT, 3)


def _adapter_visual_scene(
    scene: _Scene, diagram: Diagram, visual: Mapping[str, Any], y: int
) -> None:
    package_x, package_y, package_width = _MARGIN, y + 25, 470
    adapter_x, adapter_width = 600, 752
    pins = int(visual["package_pins"]) if visual.get("package_pins") else None
    shape = str(visual["package_shape"])
    side_counts = _pin_distribution(pins, shape) if pins else [0, 0]
    max_side = max(side_counts, default=0)
    body_height = max(170, max_side * 9 + 42)
    body_width = 270
    package_body_x, body_y = package_x + 100, package_y + 78
    placement_lines = _wrap_pixels(visual["placement_note"], package_width - 40, 15)
    programmer_lines = _wrap_pixels(visual["programmer_label"], adapter_width - 40, 15)
    package_height = 120 + body_height + len(placement_lines) * 20
    interface_position = str(visual.get("interface_position", "bottom"))
    adapter_height = max(
        package_height,
        230 + body_height,
        120 + body_height + len(programmer_lines) * 20 + 60,
    )
    if interface_position == "top":
        adapter_height = max(adapter_height, 260 + body_height)
    if visual.get("interface_labels_left") or visual.get("interface_labels_right"):
        adapter_height = max(
            adapter_height,
            (380 if interface_position == "top" else 360) + body_height,
        )
    scene.rect(package_x, package_y, package_width, package_height, _WHITE, _ACCENT)
    scene.text(package_x + 20, package_y + 36, visual["package_label"], 22, _INK, True)
    scene.rect(package_body_x, body_y, body_width, body_height, _PALE, _ACCENT)
    _package_contacts(
        scene, package_body_x, body_y, body_width, body_height, shape, pins
    )
    scene.circle(package_body_x + 18, body_y + 18, 8, _ACCENT, _ACCENT)
    scene.text(package_body_x + 34, body_y + 26, visual["pin1_label"], 14, _MUTED)
    if pins:
        scene.text(
            package_body_x + 20,
            body_y + body_height // 2,
            f"{pins}-pin package",
            18,
            _INK,
        )
    placement_y = body_y + body_height + 44
    for index, line in enumerate(placement_lines):
        scene.text(package_x + 20, placement_y + index * 20, line, 15, _MUTED)

    scene.rect(adapter_x, package_y, adapter_width, adapter_height, _PALE, _ACCENT)
    scene.text(adapter_x + 20, package_y + 36, visual["adapter_label"], 22, _INK, True)
    socket_x = adapter_x + 115
    socket_y = package_y + (220 if interface_position == "top" else 108)
    socket_width, socket_height = 520, body_height + 52
    scene.rect(socket_x, socket_y, socket_width, socket_height, _WHITE, _ACCENT)
    slot_x, slot_y = socket_x + 145, socket_y + 26
    slot_width, slot_height = 230, body_height
    scene.rect(slot_x, slot_y, slot_width, slot_height, _PALE, _ACCENT)
    _package_contacts(scene, slot_x, slot_y, slot_width, slot_height, shape, pins)
    scene.circle(slot_x + 18, slot_y + 18, 8, _ACCENT, _ACCENT)
    scene.text(slot_x + 34, slot_y + 26, visual["pin1_label"], 14, _MUTED)
    for index, line in enumerate(
        _wrap_pixels("Package footprint / contact pads", 112, 14)
    ):
        scene.text(socket_x + 14, socket_y + 40 + index * 18, line, 14, _MUTED)
    scene.line(
        package_x + package_width,
        package_y + 150,
        adapter_x,
        package_y + 150,
        _ACCENT,
        2,
    )
    _draw_interface(
        scene,
        visual,
        adapter_x,
        package_y,
        adapter_width,
        adapter_height,
        socket_x,
        socket_y,
        socket_width,
        socket_height,
    )
    programmer_y = package_y + (
        adapter_height - 16
        if visual.get("interface_labels_left") or visual.get("interface_labels_right")
        else adapter_height - 48
    )
    for index, line in enumerate(programmer_lines):
        scene.text(adapter_x + 20, programmer_y + index * 20, line, 15, _MUTED)
    facts_y = package_y + max(package_height, adapter_height) + 36
    facts_end = _facts(scene, diagram, _MARGIN, facts_y, _WIDTH - 2 * _MARGIN)
    scene.height = max(_HEIGHT, facts_end + _MARGIN)


def _wiring_scene(scene: _Scene, diagram: Diagram, y: int) -> None:
    if diagram.schematic is not None:
        _signals_scene(scene, diagram, diagram.schematic, y)
        return
    connector = diagram.connector
    if connector is None:
        if not diagram.connections:
            facts_end = _facts(scene, diagram, _MARGIN, y, _WIDTH - 2 * _MARGIN)
            scene.height = max(_HEIGHT, facts_end + _MARGIN)
            return
        end = _connection_table(
            scene,
            diagram.connections,
            _MARGIN,
            y,
            _WIDTH - 2 * _MARGIN,
            diagram.connection_heading,
        )
        facts_end = _facts(scene, diagram, _MARGIN, end + 36, _WIDTH - 2 * _MARGIN)
        scene.height = max(_HEIGHT, facts_end + _MARGIN)
        return
    connector_height = _connector_height(connector)
    _connector_scene(scene, connector, _MARGIN, y, 550, connector_height)
    end = _connection_table(
        scene, diagram.connections, 650, y, 702, diagram.connection_heading
    )
    facts_end = _facts(
        scene,
        diagram,
        _MARGIN,
        max(y + connector_height, end) + 36,
        _WIDTH - 2 * _MARGIN,
    )
    scene.height = max(_HEIGHT, facts_end + _MARGIN)


def _adapter_scene(scene: _Scene, diagram: Diagram, y: int) -> None:
    if diagram.adapter_visual is not None:
        _adapter_visual_scene(scene, diagram, diagram.adapter_visual, y)
        return
    connector = diagram.connector
    if connector is not None:
        connector_height = _connector_height(connector)
        _connector_scene(scene, connector, _MARGIN, y, 550, connector_height)
        end = _connection_table(
            scene, diagram.connections, 650, y, 702, diagram.connection_heading
        )
        facts_end = _facts(
            scene,
            diagram,
            _MARGIN,
            max(y + connector_height, end) + 36,
            _WIDTH - 2 * _MARGIN,
        )
        scene.height = max(_HEIGHT, facts_end + _MARGIN)
        return
    facts_end = _facts(scene, diagram, _MARGIN, y, _WIDTH - 2 * _MARGIN)
    scene.height = max(_HEIGHT, facts_end + _MARGIN)


def _svg(scene: _Scene) -> str:
    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" '
        f'width="{_WIDTH}" height="{scene.height}" '
        f'viewBox="0 0 {_WIDTH} {scene.height}">'
    ]
    for op in scene.ops:
        values = op.values
        if op.kind == "rect":
            x, y, width, height, fill, stroke = values
            parts.append(
                f'<rect x="{x}" y="{y}" width="{width}" height="{height}" '
                f'fill="{fill}" stroke="{stroke}" stroke-width="2" rx="8"/>'
            )
        elif op.kind == "line":
            x1, y1, x2, y2, stroke, width, dashed = values
            dash = ' stroke-dasharray="8 6"' if dashed else ""
            parts.append(
                f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" '
                f'stroke="{stroke}" stroke-width="{width}"{dash}/>'
            )
        elif op.kind == "circle":
            x, y, radius, fill, stroke = values
            parts.append(
                f'<circle cx="{x}" cy="{y}" r="{radius}" fill="{fill}" '
                f'stroke="{stroke}" stroke-width="2"/>'
            )
        else:
            x, y, value, size, fill, bold = values
            weight = "700" if bold else "400"
            parts.append(
                f'<text x="{x}" y="{y}" font-family="sans-serif" '
                f'font-size="{size}px" font-weight="{weight}" fill="{fill}">'
                f"{html.escape(str(value))}</text>"
            )
    parts.append("</svg>\n")
    return "".join(parts)


def _font(size: int, bold: bool) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    # The default Pillow font is bundled and deterministic across installations.
    return ImageFont.load_default(size=max(10, size))


def _jpg(scene: _Scene) -> Image.Image:
    image = Image.new("RGB", (_WIDTH, scene.height), _WHITE)
    draw = ImageDraw.Draw(image)
    for op in scene.ops:
        values = op.values
        if op.kind == "rect":
            x, y, width, height, fill, stroke = values
            draw.rounded_rectangle(
                (x, y, x + width, y + height),
                radius=8,
                fill=fill,
                outline=stroke,
                width=2,
            )
        elif op.kind == "line":
            x1, y1, x2, y2, stroke, width, dashed = values
            if not dashed:
                draw.line((x1, y1, x2, y2), fill=stroke, width=width)
            else:
                # Pillow has no portable dashed-line primitive.  Segment the
                # vector deterministically so JPEG and SVG retain the same
                # conditional-connection cue.
                import math

                dx, dy = x2 - x1, y2 - y1
                length = math.hypot(dx, dy)
                if length:
                    ux, uy = dx / length, dy / length
                    cursor = 0.0
                    while cursor < length:
                        end = min(cursor + 8.0, length)
                        draw.line(
                            (
                                x1 + ux * cursor,
                                y1 + uy * cursor,
                                x1 + ux * end,
                                y1 + uy * end,
                            ),
                            fill=stroke,
                            width=width,
                        )
                        cursor += 14.0
        elif op.kind == "circle":
            x, y, radius, fill, stroke = values
            draw.ellipse(
                (x - radius, y - radius, x + radius, y + radius),
                fill=fill,
                outline=stroke,
                width=2,
            )
        else:
            x, y, value, size, fill, bold = values
            draw.text((x, y - size), str(value), fill=fill, font=_font(size, bold))
    return image


def render_diagram(diagram: Diagram) -> tuple[str, Image.Image]:
    """Return deterministic SVG text and a matching RGB Pillow image."""

    scene = _scene_for(diagram)
    return _svg(scene), _jpg(scene)


def generate_diagrams(
    manifest: Mapping[str, Any],
    output: str | Path,
    *,
    reference_directory: str | Path | None = None,
) -> dict[str, Any]:
    """Write SVG/JPEG pairs from factual data and return a file listing."""

    diagrams = validate_manifest(manifest)
    if reference_directory is not None:
        validate_reference_directory(diagrams, reference_directory)
    root = Path(output).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    report_entries: list[dict[str, Any]] = []
    catalog_assets: list[dict[str, Any]] = []
    catalog_mappings: list[dict[str, Any]] = []
    for diagram in diagrams:
        svg, image = render_diagram(diagram)
        output_root = (
            root / diagram.asset_group if diagram.asset_group != "default" else root
        )
        output_root.mkdir(parents=True, exist_ok=True)
        svg_path = output_root / f"{Path(diagram.filename).stem}.svg"
        jpg_path = output_root / diagram.filename
        svg_path.write_text(svg, encoding="utf-8", newline="\n")
        image.save(
            jpg_path,
            format="JPEG",
            quality=92,
            optimize=False,
            progressive=False,
            subsampling=0,
        )
        entry: dict[str, Any] = {
            "id": diagram.id,
            "filename": diagram.filename,
            "asset_group": diagram.asset_group,
            "svg": svg_path.name,
            "jpg": jpg_path.name,
        }
        relative_jpg = jpg_path.relative_to(root).as_posix()
        relative_svg = svg_path.relative_to(root).as_posix()
        catalog_assets.append(
            {
                "id": diagram.id,
                "asset_group": diagram.asset_group,
                "filename": diagram.filename,
                "title": diagram.title,
                "path": relative_jpg,
                "svg_path": relative_svg,
            }
        )
        for programmer in diagram.programmers:
            for metadata_ref in diagram.metadata_refs:
                catalog_mappings.append(
                    {
                        "programmer": programmer,
                        "models": list(diagram.models),
                        "metadata_ref": metadata_ref,
                        "path": relative_jpg,
                        "svg_path": relative_svg,
                    }
                )
        report_entries.append(entry)
    report = {"schema_version": 1, "diagrams": report_entries}
    assets = {str(asset.pop("path")): asset for asset in catalog_assets}
    mappings: dict[str, dict[str, str]] = {}
    for mapping in catalog_mappings:
        programmer = str(mapping.pop("programmer"))
        reference = str(mapping.pop("metadata_ref"))
        mappings.setdefault(programmer, {})[reference] = str(mapping.pop("path"))
    catalog = {"schema_version": 1, "assets": assets, "mappings": mappings}
    (root / "catalog.json").write_text(
        json.dumps(catalog, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return report


def _load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DiagramValidationError(
            f"cannot read JSON manifest {path}: {exc}"
        ) from exc


def _cli(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--reference-dir",
        type=Path,
        help=(
            "external reference image folder; names are checked but files are not "
            "copied"
        ),
    )
    args = parser.parse_args(argv)
    try:
        manifest = _load_json(args.manifest)
        if not isinstance(manifest, Mapping):
            raise DiagramValidationError("manifest root must be an object")
        report = generate_diagrams(
            manifest, args.output, reference_directory=args.reference_dir
        )
    except DiagramValidationError as exc:
        parser.error(str(exc))
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


def main() -> int:
    return _cli(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main())
