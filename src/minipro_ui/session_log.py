"""Raw subprocess history with optional, incremental session-file persistence."""

from __future__ import annotations

import shlex
from collections.abc import Callable, Sequence
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from typing import TextIO

DEFAULT_LOG_PATH = "/tmp/minipro_ui_%Y-%m-%d_%H-%M-%S.log"


class SessionLog:
    """Record argv and unmodified decoded streams, independently of UI messages.

    A session uses one timestamp. Enabling persistence also saves commands already
    recorded in memory. File errors are reported without interrupting chip access.
    Existing files are preserved by selecting a numbered sibling on collision.
    """

    def __init__(self, on_error: Callable[[str], None] | None = None) -> None:
        self.started = datetime.now().astimezone()
        self.parts: list[str] = []
        self.path: Path | None = None
        self._file: TextIO | None = None
        self._stream: str | None = None
        self._on_error = on_error

    @property
    def text(self) -> str:
        return "".join(self.parts)

    def configure(self, enabled: bool, pattern: str) -> None:
        self.close()
        self.path = None
        if not enabled:
            return
        destination = Path(
            self.started.strftime(pattern or DEFAULT_LOG_PATH)
        ).expanduser()
        try:
            for index in range(1000):
                candidate = (
                    destination
                    if index == 0
                    else destination.with_name(
                        f"{destination.stem}_{index}{destination.suffix}"
                    )
                )
                try:
                    self._file = candidate.open("x", encoding="utf-8", newline="")
                    self.path = candidate
                    break
                except FileExistsError:
                    continue
            else:
                raise OSError("Too many existing log files with this session name")
            self._file.write(self.text)
            self._file.flush()
        except (OSError, ValueError) as error:
            self._failed(error)

    def command(self, arguments: Sequence[str], *, simulated: bool = False) -> None:
        self._stream = None
        marker = "\n[DEMO simulation — command not executed]" if simulated else ""
        self._append(marker + "\n$ " + shlex.join(arguments) + "\n")

    def output(self, stream: str, text: str) -> None:
        if not text:
            return
        if self._stream != stream:
            self._append(f"\n[{stream}]\n")
            self._stream = stream
        self._append(text)

    def finished(self, returncode: int | None) -> None:
        self._append(f"\n[exit {returncode}]\n")
        self._stream = None

    def _append(self, text: str) -> None:
        self.parts.append(text)
        if self._file is not None:
            try:
                self._file.write(text)
                self._file.flush()
            except (OSError, ValueError) as error:
                self._failed(error)

    def _failed(self, error: Exception) -> None:
        self.close()
        if self._on_error:
            self._on_error(f"Could not save minipro log: {error}")

    def close(self) -> None:
        if self._file is not None:
            with suppress(OSError):
                self._file.close()
            self._file = None
