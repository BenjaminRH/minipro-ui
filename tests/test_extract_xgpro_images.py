"""Safety and compatibility checks for the optional Xgpro image importer."""

from __future__ import annotations

import os
import subprocess
import zipfile
from pathlib import Path

import pytest

from minipro_ui import xgpro_images
from minipro_ui.xgpro_images import (
    ExtractionError,
    _archive_tool,
    _find_rar_signature,
    _safe_member_name,
    extract_images,
)

JPEG_A = b"\xff\xd8\xff" + b"a tiny test image"
JPEG_B = b"\xff\xd8\xff" + b"a different image"


def _zip(path: Path, members: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return path


@pytest.fixture
def archive_tool() -> None:
    """Skip archive integration tests when optional tools are unavailable."""
    try:
        _archive_tool()
    except ExtractionError as exc:
        pytest.skip(str(exc))


@pytest.mark.parametrize(
    "member",
    ["../outside.jpg", "/absolute.jpg", "C:/outside.jpg", "IMG/../outside.jpg"],
)
def test_archive_paths_are_rejected(member: str) -> None:
    assert _safe_member_name(member) is None


def test_empty_archive_path_is_rejected() -> None:
    assert _safe_member_name("") is None
    assert _safe_member_name(".") is None


def test_sfx_rar_signature_is_discovered_without_an_offset_constant(
    tmp_path: Path,
) -> None:
    path = tmp_path / "installer.exe"
    prefix = b"self extracting program\0" * 100
    path.write_bytes(prefix + b"Rar!\x1a\x07\x01\x00payload")
    assert _find_rar_signature(path) == len(prefix)


def test_missing_archive_tool_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(xgpro_images.shutil, "which", lambda _name: None)
    with pytest.raises(ExtractionError, match="install bsdtar"):
        _archive_tool()


def test_libarchive_tar_is_preferred_over_7z(monkeypatch: pytest.MonkeyPatch) -> None:
    def which(name: str) -> str | None:
        return {"tar": "/usr/bin/tar", "7zz": "/usr/bin/7zz"}.get(name)

    monkeypatch.setattr(xgpro_images.shutil, "which", which)
    monkeypatch.setattr(
        xgpro_images.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            ["tar"], 0, stdout=b"bsdtar 3.6.2", stderr=b""
        ),
    )
    assert _archive_tool() == ("/usr/bin/tar", "bsdtar")


def test_extracts_only_img_jpegs_and_flattens_names(
    tmp_path: Path, archive_tool: None
) -> None:
    source = _zip(
        tmp_path / "xgpro.zip",
        {
            "IMG/ICP002.JPG": JPEG_A,
            "nested/IMG/Adapter001.JPEG": JPEG_B,
            "manual/cover.jpg": JPEG_B,
            "IMG/readme.txt": b"not an image",
        },
    )
    output = tmp_path / "images"
    result = extract_images(source, output)
    assert result.written == 2
    assert result.skipped == 0
    assert (output / "ICP002.JPG").read_bytes() == JPEG_A
    assert (output / "Adapter001.JPEG").read_bytes() == JPEG_B
    assert not (output / "cover.jpg").exists()


def test_relative_paths_are_resolved_in_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, archive_tool: None
) -> None:
    _zip(tmp_path / "relative.zip", {"IMG/Photo.JPG": JPEG_A})
    monkeypatch.chdir(tmp_path)
    result = extract_images("relative.zip", "images")
    assert result.output == (tmp_path / "images").resolve()
    assert (tmp_path / "images/Photo.JPG").exists()


def test_archive_without_reference_images_is_an_error(
    tmp_path: Path, archive_tool: None
) -> None:
    source = _zip(tmp_path / "no-images.zip", {"manual/readme.txt": b"text"})
    output = tmp_path / "images"
    with pytest.raises(ExtractionError, match="no JPG/JPEG"):
        extract_images(source, output)
    assert not output.exists()


def test_conflicting_flattened_names_fail_before_writing(
    tmp_path: Path, archive_tool: None
) -> None:
    source = _zip(
        tmp_path / "conflict.zip",
        {"IMG/Photo.JPG": JPEG_A, "other/IMG/photo.jpg": JPEG_B},
    )
    output = tmp_path / "images"
    with pytest.raises(ExtractionError, match="conflicting images"):
        extract_images(source, output)
    assert not output.exists()


def test_existing_identical_file_is_skipped_and_different_file_requires_overwrite(
    tmp_path: Path, archive_tool: None
) -> None:
    source = _zip(tmp_path / "images.zip", {"img/Photo.JPG": JPEG_A})
    output = tmp_path / "images"
    output.mkdir()
    (output / "Photo.JPG").write_bytes(JPEG_A)
    assert extract_images(source, output).skipped == 1

    (output / "Photo.JPG").write_bytes(JPEG_B)
    with pytest.raises(ExtractionError, match="--overwrite"):
        extract_images(source, output)
    assert extract_images(source, output, overwrite=True).written == 1
    assert (output / "Photo.JPG").read_bytes() == JPEG_A


def test_existing_conflict_is_checked_before_any_image_is_written(
    tmp_path: Path, archive_tool: None
) -> None:
    source = _zip(
        tmp_path / "two-images.zip",
        {"IMG/First.JPG": JPEG_A, "IMG/Second.JPG": JPEG_B},
    )
    output = tmp_path / "images"
    output.mkdir()
    (output / "Second.JPG").write_bytes(b"old")
    with pytest.raises(ExtractionError, match="Second.JPG"):
        extract_images(source, output)
    assert not (output / "First.JPG").exists()


@pytest.mark.skipif(
    not os.environ.get("XGPRO_REFERENCE_INSTALLER"),
    reason="set XGPRO_REFERENCE_INSTALLER for a local installer smoke test",
)
def test_local_xgpro_installer_smoke_test(tmp_path: Path, archive_tool: None) -> None:
    result = extract_images(
        os.environ["XGPRO_REFERENCE_INSTALLER"], tmp_path / "images"
    )
    assert result.written > 0
