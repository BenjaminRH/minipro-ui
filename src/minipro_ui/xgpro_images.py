#!/usr/bin/env python3
"""Extract the device reference JPEGs from an Xgpro installer.

Xgpro installers are Windows self-extracting archives.  This utility treats
them as data: it never launches the installer.  It uses ``bsdtar`` (or
``7z``/``7zz``) to inspect the archive and copies only JPG/JPEG files below an
``IMG`` directory into a flat, user-selected image directory.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import cast

MAX_ARCHIVE_DEPTH = 4
MAX_MEMBER_BYTES = 100 * 1024 * 1024
RAR4_SIGNATURE = b"Rar!\x1a\x07\x00"
RAR5_SIGNATURE = b"Rar!\x1a\x07\x01\x00"


class ExtractionError(RuntimeError):
    """A user-fixable archive or output error."""


class NoImagesFoundError(ExtractionError):
    """The selected file did not contain any device reference JPEGs."""


@dataclass(frozen=True)
class ExtractionResult:
    """Counts produced by :func:`extract_images`."""

    written: int
    skipped: int
    output: Path


def _find_rar_signature(path: Path) -> int | None:
    """Return the first RAR signature offset, including in an SFX executable."""

    signatures = (RAR5_SIGNATURE, RAR4_SIGNATURE)
    overlap = max(len(signature) for signature in signatures) - 1
    carry = b""
    try:
        with path.open("rb") as source:
            offset = 0
            while chunk := source.read(1024 * 1024):
                data = carry + chunk
                for signature in signatures:
                    index = data.find(signature)
                    if index >= 0:
                        return offset - len(carry) + index
                offset += len(chunk)
                carry = data[-overlap:]
    except OSError as exc:
        raise ExtractionError(f"cannot read installer {path}: {exc}") from exc
    return None


def _archive_tool() -> tuple[str, str]:
    """Return (tool, kind), preferring libarchive's bsdtar."""

    if executable := shutil.which("bsdtar"):
        return executable, "bsdtar"
    if executable := shutil.which("tar"):
        try:
            version = subprocess.run(
                [executable, "--version"],
                stdin=subprocess.DEVNULL,
                capture_output=True,
                check=False,
            )
        except OSError:
            version = None
        if version is not None and any(
            marker in (version.stdout + version.stderr).lower()
            for marker in (b"bsdtar", b"libarchive")
        ):
            return executable, "bsdtar"
    for name in ("7zz", "7z"):
        if executable := shutil.which(name):
            return executable, "7z"
    raise ExtractionError(
        "cannot find an archive tool; install bsdtar (libarchive) or 7z/7zz "
        "and try again"
    )


def _run_archive(tool: str, args: Sequence[str]) -> subprocess.CompletedProcess[bytes]:
    command = [tool, *args]
    try:
        return subprocess.run(
            command,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            check=False,
        )
    except OSError as exc:
        raise ExtractionError(f"could not run archive tool {tool}: {exc}") from exc


def _list_members(path: Path, tool: str, kind: str) -> list[str] | None:
    args = ["-tf", str(path)] if kind == "bsdtar" else ["l", "-slt", str(path)]
    result = _run_archive(tool, args)
    if result.returncode != 0:
        return None
    if kind == "bsdtar":
        lines = result.stdout.decode("utf-8", "surrogateescape").splitlines()
    else:
        # Technical listing emits one unambiguous ``Path =`` line per member.
        lines = []
        in_members = False
        for line in result.stdout.decode("utf-8", "replace").splitlines():
            if line.strip() == "----------":
                in_members = True
                continue
            if in_members and line.startswith("Path = "):
                lines.append(line.removeprefix("Path = "))
    return [line for line in lines if line]


def _prepare_archive(path: Path, workspace: Path, tool: str, kind: str) -> Path:
    """Use *path* directly or strip an SFX prefix into *workspace*."""

    if _list_members(path, tool, kind) is not None:
        return path
    offset = _find_rar_signature(path)
    if offset is None or offset == 0:
        raise ExtractionError(
            f"{path.name} is not a readable ZIP/RAR archive or Xgpro installer"
        )
    stripped = workspace / f"sfx-{len(tuple(workspace.iterdir()))}.rar"
    try:
        with path.open("rb") as source, stripped.open("wb") as target:
            source.seek(offset)
            shutil.copyfileobj(source, target, length=1024 * 1024)
    except OSError as exc:
        raise ExtractionError(f"cannot prepare installer archive: {exc}") from exc
    if _list_members(stripped, tool, kind) is None:
        raise ExtractionError(f"the RAR payload in {path.name} could not be read")
    return stripped


def _safe_member_name(member: str) -> PurePosixPath | None:
    """Normalize an archive name and reject traversal or platform paths."""

    if not member or "\x00" in member or "\n" in member or "\r" in member:
        return None
    normalized = member.replace("\\", "/")
    path = PurePosixPath(normalized)
    if not path.parts or path.is_absolute() or ":" in path.parts[0]:
        return None
    parts = tuple(part for part in path.parts if part not in {"."})
    if not parts or any(part == ".." for part in parts):
        return None
    return PurePosixPath(*parts)


def _is_image_member(member: str) -> bool:
    safe = _safe_member_name(member)
    if safe is None or not any(part.casefold() == "img" for part in safe.parts[:-1]):
        return False
    return safe.suffix.casefold() in {".jpg", ".jpeg"}


def _read_member(archive: Path, member: str, tool: str, kind: str) -> bytes:
    args = (
        ["-xOf", str(archive), member]
        if kind == "bsdtar"
        else ["e", "-so", str(archive), member]
    )
    try:
        process = subprocess.Popen(
            [tool, *args],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        raise ExtractionError(f"could not run archive tool {tool}: {exc}") from exc
    assert process.stdout is not None
    data = cast(bytes, process.stdout.read(MAX_MEMBER_BYTES + 1))
    if len(data) > MAX_MEMBER_BYTES:
        process.kill()
        process.communicate()
        raise ExtractionError(f"archive member {member!r} is too large")
    process.communicate()
    if process.returncode != 0:
        raise ExtractionError(f"could not extract archive member {member!r}")
    return data


def _looks_like_jpeg(data: bytes) -> bool:
    return len(data) >= 3 and data[:3] == b"\xff\xd8\xff"


def _nested_archive(member: str) -> bool:
    safe = _safe_member_name(member)
    return safe is not None and safe.suffix.casefold() in {".exe", ".rar", ".zip"}


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def extract_images(
    installer: str | os.PathLike[str],
    output: str | os.PathLike[str],
    *,
    overwrite: bool = False,
    progress: Callable[[str], None] | None = None,
) -> ExtractionResult:
    """Extract JPG/JPEG files below ``IMG`` from an Xgpro archive.

    The output is flattened because minipro's device metadata refers to image
    basenames.  A duplicate basename with different bytes is rejected.  An
    existing differing file requires ``overwrite=True``; identical files are
    skipped automatically.
    """

    source = Path(installer).expanduser().resolve()
    destination = Path(output).expanduser().resolve()
    if not source.is_file():
        raise ExtractionError(f"installer file does not exist: {source}")
    tool, kind = _archive_tool()
    if progress is not None:
        progress(f"Using {Path(tool).name} to inspect the installer")
    images: dict[str, tuple[str, bytes, str]] = {}
    queued: set[tuple[int, str]] = set()
    with tempfile.TemporaryDirectory(prefix="minipro-ui-xgpro-") as temporary:
        workspace = Path(temporary)
        pending: list[tuple[Path, int]] = [(source, 0)]
        while pending:
            current, depth = pending.pop()
            if depth > MAX_ARCHIVE_DEPTH:
                raise ExtractionError(
                    f"archive nesting exceeds the limit of {MAX_ARCHIVE_DEPTH} levels"
                )
            archive = _prepare_archive(current, workspace, tool, kind)
            if progress is not None:
                progress(f"Reading {current.name}")
            members = _list_members(archive, tool, kind)
            if members is None:
                raise ExtractionError(f"could not list archive {archive}")
            image_members = {member for member in members if _is_image_member(member)}
            for member in members:
                safe = _safe_member_name(member)
                if safe is None:
                    continue
                if _is_image_member(member):
                    data = _read_member(archive, member, tool, kind)
                    if not _looks_like_jpeg(data):
                        raise ExtractionError(
                            f"archive member {member!r} is not a JPEG"
                        )
                    name = safe.name
                    key = name.casefold()
                    digest = _digest(data)
                    prior = images.get(key)
                    if prior is not None and prior[0] != digest:
                        raise ExtractionError(
                            f"archive contains conflicting images named {name!r}"
                        )
                    images.setdefault(key, (digest, data, name))
                    if progress is not None:
                        progress(f"Found {name}")
                elif not image_members and _nested_archive(member):
                    identity = (depth + 1, member.casefold())
                    if identity in queued:
                        continue
                    queued.add(identity)
                    nested = workspace / f"nested-{len(queued)}{safe.suffix.casefold()}"
                    try:
                        nested.write_bytes(_read_member(archive, member, tool, kind))
                    except ExtractionError:
                        # A self-extracting package can contain helper
                        # executables using a compression method unavailable
                        # to the selected fallback tool.  They are unrelated
                        # unless they can be opened as an archive.
                        continue
                    # Installers often bundle unrelated .exe files.  Probe a
                    # nested candidate before queueing it so those payloads
                    # are ignored instead of making the whole import fail.
                    try:
                        _prepare_archive(nested, workspace, tool, kind)
                    except ExtractionError:
                        continue
                    pending.append((nested, depth + 1))

    if not images:
        raise NoImagesFoundError(
            "no JPG/JPEG files were found below an IMG directory "
            "in the selected installer"
        )

    try:
        destination.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ExtractionError(
            f"cannot create output directory {destination}: {exc}"
        ) from exc

    written = skipped = 0
    try:
        existing_by_name: dict[str, Path] = {}
        for entry in destination.iterdir():
            key = entry.name.casefold()
            if key in existing_by_name:
                raise ExtractionError(
                    "output directory contains case-conflicting files "
                    f"{existing_by_name[key].name!r} and {entry.name!r}"
                )
            existing_by_name[key] = entry
    except OSError as exc:
        raise ExtractionError(
            f"cannot inspect output directory {destination}: {exc}"
        ) from exc
    actions: list[tuple[str, bytes, Path]] = []
    for digest, data, name in images.values():
        target = existing_by_name.get(name.casefold(), destination / name)
        try:
            existing = target.read_bytes() if target.is_file() else None
        except OSError as exc:
            raise ExtractionError(
                f"cannot read existing output {target}: {exc}"
            ) from exc
        if target.exists() and not target.is_file():
            raise ExtractionError(f"output path {target} is not a regular file")
        if existing is not None and _digest(existing) == digest:
            skipped += 1
            continue
        if existing is not None and not overwrite:
            raise ExtractionError(
                f"output file {target} already exists with different contents; "
                "rerun with --overwrite to replace it"
            )
        actions.append((name, data, target))

    for name, data, target in actions:
        temporary_target: Path | None = None
        try:
            fd, temporary_name = tempfile.mkstemp(
                prefix=f".{name}.", suffix=".minipro-ui.tmp", dir=destination
            )
            temporary_target = Path(temporary_name)
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
            os.replace(temporary_target, target)
        except OSError as exc:
            with contextlib.suppress(OSError):
                if temporary_target is not None:
                    temporary_target.unlink(missing_ok=True)
            raise ExtractionError(f"cannot write {target}: {exc}") from exc
        existing_by_name[name.casefold()] = target
        written += 1
        if progress is not None:
            progress(f"Wrote {name}")
    return ExtractionResult(written, skipped, destination)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Extract Xgpro IMG/*.JPG device images without executing the "
            "Windows installer."
        )
    )
    parser.add_argument(
        "installer", type=Path, help="local Xgpro .exe, .rar, or .zip file"
    )
    parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="directory to receive the flattened JPG/JPEG image set",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="replace output files whose contents differ",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = extract_images(args.installer, args.output, overwrite=args.overwrite)
    except ExtractionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    print(
        f"Extracted {result.written} image(s) to {result.output}"
        + (f"; skipped {result.skipped} identical file(s)" if result.skipped else "")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
