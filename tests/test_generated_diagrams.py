from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from minipro_ui.generate_diagrams import (
    DiagramValidationError,
    generate_diagrams,
    render_diagram,
    validate_manifest,
    validate_reference_directory,
)


def _manifest() -> dict[str, object]:
    return {
        "schema_version": 1,
        "diagrams": [
            {
                "id": "t48-icp002",
                "filename": "ICP002.JPG",
                "kind": "wiring",
                "title": "T48 ICSP wiring",
                "programmers": ["T48"],
                "connector": {
                    "pin_count": 6,
                    "rows": [{"pins": [1, 2, 3]}, {"pins": [4, 5, 6]}],
                    "orientation": "pin 1 at the upper left",
                },
                "connections": [
                    {"pins": [1], "signal": "VCC", "target": "target VCC"},
                    {"pins": [2], "signal": "GND", "target": "target GND"},
                    {"pins": [3], "signal": "MOSI", "target": "target MOSI"},
                ],
                "facts": ["Use a 6-pin ICSP header."],
                "notes": ["Confirm the target voltage before connecting."],
            }
        ],
    }


def test_manifest_validation_rejects_duplicate_and_conflicting_pins() -> None:
    manifest = _manifest()
    diagram = manifest["diagrams"][0]  # type: ignore[index]
    diagram["connections"] = [  # type: ignore[index]
        {"pins": [1], "signal": "VCC", "target": "target VCC"},
        {"pins": [1], "signal": "VCC", "target": "target GND"},
    ]
    with pytest.raises(DiagramValidationError, match="conflicting"):
        validate_manifest(manifest)

    duplicate = _manifest()
    duplicate["diagrams"] = [duplicate["diagrams"][0], duplicate["diagrams"][0]]  # type: ignore[index]
    with pytest.raises(DiagramValidationError, match="duplicate diagram id"):
        validate_manifest(duplicate)


def test_unknown_orientation_does_not_get_invented() -> None:
    manifest = _manifest()
    diagram = manifest["diagrams"][0]  # type: ignore[index]
    diagram["connector"] = None  # type: ignore[index]
    normalized = validate_manifest(manifest)[0]
    assert normalized.connector is None


def test_programmer_specific_reference_basename_is_supported() -> None:
    manifest = _manifest()
    diagram = manifest["diagrams"][0]  # type: ignore[index]
    diagram["filename"] = "T56T48ICP013.jpg"  # type: ignore[index]
    assert validate_manifest(manifest)[0].filename == "T56T48ICP013.jpg"


def test_connection_heading_describes_pin_domain() -> None:
    manifest = _manifest()
    diagram = manifest["diagrams"][0]  # type: ignore[index]
    diagram["kind"] = "adapter"  # type: ignore[index]
    diagram["connection_heading"] = "Flash pin mapping"  # type: ignore[index]
    svg, _image = render_diagram(validate_manifest(manifest)[0])
    assert "Flash pin mapping" in svg
    assert "Programmer pin mapping" not in svg


def test_named_terminal_schematic_draws_declared_ports() -> None:
    manifest = _manifest()
    diagram = manifest["diagrams"][0]  # type: ignore[index]
    diagram["connector"] = None  # type: ignore[index]
    diagram["connections"] = []  # type: ignore[index]
    diagram["schematic"] = {  # type: ignore[index]
        "kind": "signals",
        "source_label": "T48 eMMC ISP driver",
        "target_label": "eMMC",
        "input_label": "T48 16-pin ISP",
        "ports": [
            {"source": "VCC", "target": "VCC", "optional": True},
            {"source": "CLK", "target": "CLK"},
            {"source": "NC", "target": "unused", "connected": False},
        ],
        "optional_note": "Disconnect VCC when external power is used.",
        "external_power": "Target VCC",
    }
    svg, image = render_diagram(validate_manifest(manifest)[0])
    assert "T48 eMMC ISP driver" in svg
    assert "VCC (optional)" in svg
    assert "CLK" in svg
    assert "NC" in svg
    assert "unused" not in svg
    assert "External power" in svg
    assert "<line" in svg
    assert image.height == 900


def test_adapter_visual_draws_package_and_socket_without_fake_map() -> None:
    manifest = _manifest()
    diagram = manifest["diagrams"][0]  # type: ignore[index]
    diagram["kind"] = "adapter"  # type: ignore[index]
    diagram["connector"] = None  # type: ignore[index]
    diagram["connections"] = []  # type: ignore[index]
    diagram["adapter_visual"] = {  # type: ignore[index]
        "kind": "adapter",
        "package_shape": "quad",
        "package_label": "TQFP128",
        "package_pins": 128,
        "adapter_label": "ZIF adapter",
        "programmer_label": "T56 programmer ZIF",
        "pin1_label": "Package pin 1",
        "placement_note": "Align the package marker with the socket marker.",
        "interface_label": "Parallel interface",
    }
    svg, _image = render_diagram(validate_manifest(manifest)[0])
    assert "TQFP128" in svg
    assert "128-pin package" in svg
    assert "ZIF adapter" in svg
    assert "T56 programmer ZIF" in svg
    assert "pin 1" in svg
    assert "Socket / adapter placement area" not in svg
    assert svg.count("<line") >= 4


def test_paired_interface_keeps_label_order_and_reserved_contacts() -> None:
    manifest = _manifest()
    diagram = manifest["diagrams"][0]  # type: ignore[index]
    diagram["kind"] = "adapter"  # type: ignore[index]
    diagram["connector"] = None  # type: ignore[index]
    diagram["connections"] = []  # type: ignore[index]
    diagram["adapter_visual"] = {  # type: ignore[index]
        "kind": "adapter",
        "package_shape": "quad",
        "package_label": "LQFP128",
        "package_pins": 128,
        "adapter_label": "KB adapter",
        "programmer_label": "T56",
        "pin1_label": "PIN1",
        "placement_note": "Align PIN1.",
        "interface_label": "SPI",
        "interface_labels_left": ["/CS", "MISO", "unlabeled", "GND"],
        "interface_labels_right": ["VCC", "unlabeled", "CLK", "MOSI"],
        "interface_position": "bottom",
        "interface_pin_count": 8,
    }
    svg, _image = render_diagram(validate_manifest(manifest)[0])
    assert svg.index("/CS") < svg.index("MISO") < svg.index("GND")
    assert svg.index("VCC") < svg.index("CLK") < svg.index("MOSI")
    assert "unlabeled" not in svg


def test_disconnected_named_port_may_omit_target() -> None:
    manifest = _manifest()
    diagram = manifest["diagrams"][0]  # type: ignore[index]
    diagram["connector"] = None  # type: ignore[index]
    diagram["connections"] = []  # type: ignore[index]
    diagram["schematic"] = {  # type: ignore[index]
        "kind": "signals",
        "source_label": "Driver",
        "target_label": "Target",
        "input_label": "Header",
        "ports": [{"source": "NC", "connected": False}],
    }
    svg, _image = render_diagram(validate_manifest(manifest)[0])
    assert "NC" in svg
    assert "not connected" not in svg


def test_long_pin_table_expands_canvas_without_clipping() -> None:
    manifest = _manifest()
    diagram = manifest["diagrams"][0]  # type: ignore[index]
    diagram["connector"] = {  # type: ignore[index]
        "pin_count": 40,
        "rows": [list(range(1, 21)), list(range(21, 41))],
        "orientation": (
            "Two rows viewed from the mating side; pin 1 is the upper-left position "
            "and numbering continues according to the source-established orientation."
        ),
    }
    diagram["connections"] = [  # type: ignore[index]
        {
            "pins": [pin],
            "signal": f"signal-{pin}",
            "target": "a deliberately long target-side terminal description",
            "direction": "bidirectional",
        }
        for pin in range(1, 41)
    ]
    svg, image = render_diagram(validate_manifest(manifest)[0])
    assert image.height > 900
    height = int(svg.split('height="', 1)[1].split('"', 1)[0])
    assert height == image.height


def test_long_pin_list_and_target_use_separate_wrapped_columns() -> None:
    manifest = _manifest()
    diagram = manifest["diagrams"][0]  # type: ignore[index]
    diagram["connector"] = {  # type: ignore[index]
        "pin_count": 17,
        "rows": [list(range(1, 10)), list(range(10, 18))],
        "orientation": "pin 1 at the upper left",
    }
    diagram["connections"] = [  # type: ignore[index]
        {
            "pins": list(range(1, 18)),
            "signal": "PB/PD",
            "target": "a long target description that must wrap independently",
        }
    ]
    svg, image = render_diagram(validate_manifest(manifest)[0])
    assert ", ".join(str(pin) for pin in range(1, 18)) not in svg
    assert "PB/PD" in svg
    assert "target description" in svg
    assert image.height == int(svg.split('height="', 1)[1].split('"', 1)[0])


def test_generator_emits_same_basename_pairs_and_deterministic_output(
    tmp_path: Path,
) -> None:
    manifest = _manifest()
    first = generate_diagrams(manifest, tmp_path / "first")
    second = generate_diagrams(manifest, tmp_path / "second")

    for output in (tmp_path / "first", tmp_path / "second"):
        assert (output / "ICP002.svg").is_file()
        assert (output / "ICP002.JPG").is_file()
        assert (output / "catalog.json").is_file()
        with Image.open(output / "ICP002.JPG") as image:
            assert image.size == (1400, 900)
            assert image.format == "JPEG"
        assert "T48" in (output / "ICP002.svg").read_text(encoding="utf-8")
        assert "target VCC" in (output / "ICP002.svg").read_text(encoding="utf-8")
    assert first == second
    catalog = json.loads((tmp_path / "first" / "catalog.json").read_text())
    assert catalog["mappings"]["T48"]["ICP002.JPG"] == "ICP002.JPG"
    assert catalog["assets"]["ICP002.JPG"]["id"] == "t48-icp002"
    assert "status" not in catalog["assets"]["ICP002.JPG"]


def test_reference_directory_checks_names_without_copying(tmp_path: Path) -> None:
    reference = tmp_path / "reference"
    (reference / "default").mkdir(parents=True)
    (reference / "default" / "ICP002.JPG").write_bytes(b"reference")
    validate_reference_directory(validate_manifest(_manifest()), reference)

    (reference / "default" / "ICP002.JPG").unlink()
    with pytest.raises(DiagramValidationError, match="missing"):
        validate_reference_directory(validate_manifest(_manifest()), reference)


def test_canonical_manifest_all_render_bounds() -> None:
    manifest_path = Path(__file__).parents[1] / "tools" / "diagram_data.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    diagrams = validate_manifest(manifest)
    assert len(diagrams) == 79
    for diagram in diagrams:
        svg, image = render_diagram(diagram)
        svg_height = int(svg.split('height="', 1)[1].split('"', 1)[0])
        assert image.size == (1400, svg_height)


def test_raster_unsafe_symbols_are_normalized_and_empty_table_is_omitted() -> None:
    manifest = _manifest()
    diagram = manifest["diagrams"][0]  # type: ignore[index]
    diagram["connector"] = None  # type: ignore[index]
    diagram["connections"] = []  # type: ignore[index]
    diagram["facts"] = ["Keep 5–10 cm spacing; input is 1.8 µF at 3 Ω → target."]  # type: ignore[index]
    svg, _image = render_diagram(validate_manifest(manifest)[0])
    assert "5-10 cm" in svg
    assert "1.8 uF" in svg
    assert "3 ohm -&gt; target" in svg
    assert "No pin mapping" not in svg
    assert not any(char in svg for char in "–—‑−→←↔µμΩΩ·×≤≥ “”‘’")


def test_same_basename_can_be_kept_in_separate_asset_groups(tmp_path: Path) -> None:
    manifest = _manifest()
    second = json.loads(json.dumps(manifest["diagrams"][0]))
    second["id"] = "legacy-icp002"
    second["asset_group"] = "legacy"
    second["programmers"] = ["TL866A"]
    manifest["diagrams"] = [manifest["diagrams"][0], second]

    diagrams = validate_manifest(manifest)
    assert [diagram.id for diagram in diagrams] == ["t48-icp002", "legacy-icp002"]
    generate_diagrams(manifest, tmp_path / "generated")
    assert (tmp_path / "generated" / "ICP002.JPG").is_file()
    assert (tmp_path / "generated" / "legacy" / "ICP002.JPG").is_file()


def test_same_programmer_reference_mapping_cannot_be_ambiguous() -> None:
    manifest = _manifest()
    second = json.loads(json.dumps(manifest["diagrams"][0]))
    second["id"] = "current-icp002"
    second["asset_group"] = "current"
    manifest["diagrams"] = [manifest["diagrams"][0], second]
    with pytest.raises(DiagramValidationError, match="mapping"):
        validate_manifest(manifest)


def test_reference_directory_requires_matching_group(tmp_path: Path) -> None:
    reference = tmp_path / "reference"
    reference.mkdir()
    manifest = _manifest()
    with pytest.raises(DiagramValidationError, match="missing"):
        validate_reference_directory(validate_manifest(manifest), reference)
