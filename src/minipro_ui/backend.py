"""Version-aware CLI boundary. No Textual types or presentation decisions live here.

Only this module knows minipro's switches and text formats. Backends return typed
metadata and preserve diagnostics; a future library can implement the same protocol.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Protocol

from .capabilities import (
    advanced_capabilities,
    device_capabilities,
    filter_supported_devices,
)
from .models import DeviceInfo, Job, Operation
from .process import OutputCallback, Result, run_process
from .session_log import SessionLog

ID_MISMATCH_OVERRIDE_HINT = re.compile(
    r"\(\s*(?i:use)\s+['\"]?-y['\"]?\s+"
    r"(?i:to\s+continue\s+anyway\s+at\s+your\s+own\s+risk)\s*\)"
)


class BackendError(RuntimeError):
    """A failed command or an output format we cannot interpret safely."""


@dataclass(frozen=True)
class BackendVersion:
    release: str
    revision: str
    details: str

    @property
    def recognized(self) -> bool:
        # Release strings alone do not distinguish master from a tagged build.
        return self.release == "0.7.4" and (
            not self.revision or self.revision.startswith("3808aecb6a1d")
        )


class Backend(Protocol):
    """The stable application-facing contract, implemented by CLI and demo backends."""

    async def version(self) -> BackendVersion: ...
    async def programmers(self) -> list[str]: ...
    async def connected(self) -> list[str]: ...
    async def devices(self, programmer: str) -> list[str]: ...
    async def device_info(self, programmer: str, name: str) -> DeviceInfo: ...
    def command(self, job: Job) -> list[str]: ...
    async def execute(self, job: Job, output: OutputCallback) -> Result: ...


class CliAdapter:
    """The 0.7.4 CLI dialect, also usable explicitly to evaluate candidate versions.

    Unknown revisions may be inspected, but hardware commands require explicit
    opt-in. Compatibility tests use this same implementation against new binaries.
    """

    def __init__(
        self,
        executable: Path,
        *,
        allow_unverified: bool = False,
        log: SessionLog | None = None,
    ) -> None:
        self.log = log
        self.executable = executable.resolve()
        self.allow_unverified = allow_unverified
        self._version: BackendVersion | None = None
        self._lock = asyncio.Lock()

    async def _run(
        self,
        arguments: list[str],
        output: OutputCallback | None = None,
        *,
        timeout: float | None = 30,
    ) -> Result:
        async with self._lock:
            try:
                return await run_process(
                    [str(self.executable), *arguments],
                    output=output,
                    timeout=timeout,
                    log=self.log if arguments != ["-k"] else None,
                )
            except TimeoutError as error:
                raise BackendError(
                    "minipro timed out; the child process was stopped."
                ) from error
            except OSError as error:
                raise BackendError(f"Cannot run {self.executable}: {error}") from error

    @staticmethod
    def _require_success(result: Result) -> str:
        if result.returncode:
            raise BackendError(
                result.output.strip() or f"minipro exited {result.returncode}."
            )
        return result.output

    async def version(self) -> BackendVersion:
        if self._version is None:
            text = self._require_success(await self._run(["-V"]))
            match = re.search(r"minipro version\s+(\S+)", text, re.I)
            if not match:
                raise BackendError(f"Unrecognized version output:\n{text}")
            revision = re.search(r"^Git commit:\s*(\S+)", text, re.M)
            self._version = BackendVersion(
                match[1], revision[1] if revision else "", text
            )
        return self._version

    @staticmethod
    def parse_programmers(text: str) -> list[str]:
        aliases = {
            "tl866a": "TL866A",
            "tl866ii": "TL866II",
            "t48": "T48",
            "t56": "T56",
            "t76": "T76",
        }
        return [
            aliases[match[1].lower()]
            for match in re.finditer(
                r"^(tl866a|tl866ii|t48|t56|t76):", text, re.M | re.I
            )
        ]

    async def programmers(self) -> list[str]:
        text = self._require_success(await self._run(["-Q"]))
        models = self.parse_programmers(text)
        if not models:
            raise BackendError(f"Unrecognized programmer list:\n{text}")
        return models

    async def connected(self) -> list[str]:
        text = self._require_success(await self._run(["-k"]))
        models = self.parse_programmers(text)
        if text.strip() in {"[No programmer found]", "No programmer found."}:
            return []
        if not models and text.strip():
            raise BackendError(f"Cannot identify a connected programmer:\n{text}")
        return models

    async def devices(self, programmer: str) -> list[str]:
        if programmer not in await self.programmers():
            raise BackendError(f"This minipro build does not support {programmer}.")
        result = await self._run(["-q", programmer, "-l"])
        self._require_success(result)
        # Catalog entries are emitted on stdout; warnings belong to stderr.
        # Upstream decorates custom entries for display, but -p/-d require
        # the undecorated database key (verified against the 0.7.4 binary).
        names = [
            line.strip().removesuffix("(custom)")
            for line in result.stdout.splitlines()
            if line.strip()
        ]
        if not names:
            raise BackendError("minipro returned an empty device catalog.")
        return await asyncio.to_thread(
            filter_supported_devices,
            self.executable,
            programmer,
            list(dict.fromkeys(names)),
        )

    async def device_info(self, programmer: str, name: str) -> DeviceInfo:
        text = self._require_success(await self._run(["-q", programmer, "-d", name]))
        found = re.search(r"^Name:\s*(.+)$", text, re.M)
        if not found or found[1].strip().casefold() != name.casefold():
            raise BackendError(f"Unrecognized device information for {name}:\n{text}")
        capacity = re.search(r"^Memory:\s*(\d+) Bytes\b", text, re.M)
        capabilities = await asyncio.to_thread(
            device_capabilities, self.executable, programmer, name
        )
        advanced = await asyncio.to_thread(
            advanced_capabilities, self.executable, programmer, name, text
        )
        return DeviceInfo(
            found[1].strip(),
            text,
            code_bytes=int(capacity[1]) if capacity else None,
            logic=bool(re.search(r"^Vector count:", text, re.M)),
            operations=capabilities[0] if capabilities else None,
            regions=capabilities[1] if capabilities else None,
            tuning_options=advanced.tuning_options,
            supports_pin_check=advanced.supports_pin_check,
            supports_unprotect=advanced.supports_unprotect,
            supports_protect=advanced.supports_protect,
            supported_interfaces=(
                advanced.supported_interfaces
                or (("zif",) if re.search(r"^Vector count:", text, re.M) else ())
            ),
            supported_config_sections=advanced.supported_config_sections,
            supports_read_format=advanced.supports_read_format,
            supports_size_override=advanced.supports_size_override,
        )

    @staticmethod
    def arguments(job: Job) -> list[str]:
        """Keep all CLI-specific policy at the boundary, never in the UI."""
        flags = {
            Operation.READ: "-r",
            Operation.WRITE: "-w",
            Operation.VERIFY: "-m",
            Operation.BLANK: "-b",
            Operation.ERASE: "-E",
            Operation.ID: "-D",
            Operation.TEST: "-T",
        }
        arguments = ["-p", job.device]
        if job.interface != "zif":
            arguments.append("-i" if job.interface == "icsp" else "-I")
        if job.operation in (
            Operation.READ,
            Operation.WRITE,
            Operation.VERIFY,
            Operation.BLANK,
        ):
            arguments.extend(["-c", job.memory])
        arguments.append(flags[job.operation])
        if job.operation.needs_file:
            if job.path is None:
                raise ValueError("Choose a file first.")
            arguments.append(str(job.path.resolve()))
        advanced = job.advanced
        if (
            job.operation == Operation.READ
            and job.memory in {"code", "data", "user"}
            and advanced.read_format != "bin"
        ):
            arguments.extend(["-f", advanced.read_format])
        if job.operation == Operation.WRITE:
            if advanced.erase_mode == "skip":
                arguments.append("-e")
            elif advanced.erase_mode == "force":
                arguments.append("-E")
            if not advanced.verify_after_write:
                arguments.append("-v")
            if advanced.unprotect_before_write:
                arguments.append("-u")
            if advanced.protect_after_write:
                arguments.append("-P")
        if advanced.check_pins:
            arguments.append("-z")
        if advanced.skip_id:
            arguments.append("-x")
        if job.operation in (Operation.WRITE, Operation.VERIFY):
            if advanced.size_policy == "warn":
                arguments.append("-s")
            elif advanced.size_policy == "silent":
                arguments.append("-S")
        if job.memory == "config":
            arguments.extend(f"--{section}" for section in advanced.config_sections)
        for name, value in advanced.tuning:
            arguments.extend(["-o", f"{name}={value}"])
        if job.continue_on_id_mismatch:
            arguments.append("-y")
        return arguments

    def command(self, job: Job) -> list[str]:
        """Return the exact executable and arguments used for an operation."""
        return [str(self.executable), *self.arguments(job)]

    async def execute(self, job: Job, output: OutputCallback) -> Result:
        version = await self.version()
        if not version.recognized and not self.allow_unverified:
            raise BackendError(
                f"Unverified minipro build {version.release} ({version.revision}). "
                "Run compatibility tests first; --allow-unverified enables evaluation."
            )
        result = await self._run(self.command(job)[1:], output, timeout=None)
        if (
            result.returncode > 0
            and not job.continue_on_id_mismatch
            and ID_MISMATCH_OVERRIDE_HINT.search(result.output) is not None
        ):
            return replace(result, retry_with_id_override=True)
        return result
