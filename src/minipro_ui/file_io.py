"""Explicit replacement approval and atomic publication of saved files."""

from __future__ import annotations

import os
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class FileStamp:
    """Identify the regular file for which replacement was confirmed."""

    device: int
    inode: int
    size: int
    modified_ns: int

    @classmethod
    def capture(cls, path: Path) -> FileStamp:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode):
            raise ValueError(
                "Choose a regular file; directories and links cannot be replaced."
            )
        return cls(info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)

    def check(self, path: Path) -> None:
        try:
            current = self.capture(path)
        except (OSError, ValueError) as error:
            raise ValueError(
                "The destination changed. Choose it and confirm replacement again."
            ) from error
        if current != self:
            raise ValueError(
                "The destination changed. Choose it and confirm replacement again."
            )


@dataclass(frozen=True)
class FileChoice:
    """A dialog result, with approval tied to one destination's current identity."""

    path: Path
    replacement: FileStamp | None = None


def publish_file(staged: Path, choice: FileChoice) -> None:
    """Publish only complete output; new destinations retain no-clobber semantics."""
    if choice.replacement is None:
        os.link(staged, choice.path)
    else:
        choice.replacement.check(choice.path)
        os.replace(staged, choice.path)


def save_text(choice: FileChoice, text: str) -> None:
    with tempfile.TemporaryDirectory(
        prefix="minipro-ui-", dir=choice.path.parent
    ) as folder:
        staged = Path(folder) / "log"
        with staged.open("x", encoding="utf-8", newline="") as target:
            target.write(text)
        publish_file(staged, choice)


def nearest_directory(value: str) -> Path:
    """Return the closest existing directory ancestor of a partial path."""
    candidate = Path(value).expanduser().absolute() if value.strip() else Path.cwd()
    while not candidate.is_dir() and candidate != candidate.parent:
        candidate = candidate.parent
    return candidate
