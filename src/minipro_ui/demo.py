"""Explicit, deterministic demonstration backend; never accesses USB."""

from __future__ import annotations

import asyncio
from pathlib import Path

from .backend import BackendVersion, CliAdapter
from .models import DeviceInfo, Job, Operation, TuningOption
from .process import OutputCallback, Result
from .session_log import SessionLog


def _encoded_read(format_name: str, size: int = 32768) -> str:
    data = b"\xff" * size
    lines: list[str] = []
    for address in range(0, size, 16):
        chunk = data[address : address + 16]
        if format_name == "ihex":
            payload = bytes((len(chunk), address >> 8, address & 0xFF, 0)) + chunk
            checksum = (-sum(payload)) & 0xFF
            lines.append(f":{payload.hex().upper()}{checksum:02X}")
        elif format_name == "srec":
            payload = bytes((len(chunk) + 3, address >> 8, address & 0xFF)) + chunk
            checksum = (~sum(payload)) & 0xFF
            lines.append(f"S1{payload.hex().upper()}{checksum:02X}")
        else:
            raise ValueError(f"Unsupported encoded format: {format_name}")
    if format_name == "ihex":
        lines.append(":00000001FF")
    else:
        payload = bytes((3, 0, 0))
        lines.append(f"S9{payload.hex().upper()}{(~sum(payload)) & 0xFF:02X}")
    return "\n".join(lines) + "\n"


class DemoBackend:
    """Let users explore the interface before installing or connecting hardware."""

    NAMES = ["AT28C256@DIP28", "W25Q32JV@SOIC8", "ATMEGA328P@DIP28", "7400"]
    PROGRAMMERS = {"tl866a", "tl866ii", "t48", "t56", "t76"}

    def __init__(self, log: SessionLog | None = None) -> None:
        self.log = log

    def record(self, arguments: list[str], text: str, stream: str = "stdout") -> None:
        """Make demo history useful without pretending a subprocess was executed."""
        if self.log:
            self.log.command(["minipro", *arguments], simulated=True)
            self.log.output(stream, text + "\n")
            self.log.finished(0)

    async def version(self) -> BackendVersion:
        self.record(["-V"], "minipro demo backend; no hardware access", "stderr")
        return BackendVersion("demo", "", "DEMO — no programmer is accessed")

    async def programmers(self) -> list[str]:
        models = ["TL866A", "TL866II", "T48", "T56", "T76"]
        self.record(["-Q"], "\n".join(models))
        return models

    async def connected(self) -> list[str]:
        return ["T48"]

    async def devices(self, programmer: str) -> list[str]:
        self.record(["-q", programmer, "-l"], "\n".join(self.NAMES))
        return self.NAMES.copy()

    async def device_info(self, programmer: str, name: str) -> DeviceInfo:
        if name not in self.NAMES:
            raise ValueError("Unknown demo device")
        recognized_programmer = programmer.casefold() in self.PROGRAMMERS
        logic = name == "7400"
        detail = "Vector count: 4" if logic else "Memory: 32768 Bytes"
        text = f"Name: {name}\n{detail}\nDEMO metadata"
        self.record(["-q", programmer, "-d", name], text)
        tunings: tuple[TuningOption, ...]
        if logic and recognized_programmer:
            tunings = (TuningOption("vcc", values=("5", "3.3", "2.5", "1.8")),)
        elif (
            recognized_programmer
            and name.startswith("W25")
            and programmer.casefold() == "t48"
        ):
            tunings = (TuningOption("speed", values=("3", "7.5", "15", "30")),)
        else:
            tunings = ()
        config_sections = ("fuses", "lock") if name.startswith("ATMEGA") else ()
        interfaces: tuple[str, ...]
        if not recognized_programmer:
            interfaces = ()
        elif logic or name.startswith("AT28") or name.startswith("W25"):
            interfaces = ("zif",)
        elif name.startswith("ATMEGA"):
            interfaces = ("zif", "icsp", "external")
        else:
            interfaces = ("zif",)
        return DeviceInfo(
            name,
            text,
            None if logic else 32768,
            logic,
            (Operation.TEST,)
            if logic
            else tuple(
                op
                for op in Operation
                if op != Operation.TEST
                and (op != Operation.ID or name != self.NAMES[0])
            ),
            ()
            if logic
            else ("code", "data", "config", "calibration")
            if name.startswith("ATMEGA")
            else ("code",),
            tunings,
            False,
            False,
            False,
            interfaces,
            config_sections,
            not logic,
            not logic,
        )

    def command(self, job: Job) -> list[str]:
        """Show the equivalent CLI command while keeping demo execution simulated."""
        return ["minipro", *CliAdapter.arguments(job)]

    async def execute(self, job: Job, output: OutputCallback) -> Result:
        if self.log:
            self.log.command(self.command(job), simulated=True)
        try:
            for percent in (0, 25, 50, 75, 100):
                line = f"DEMO {job.operation}: {percent}%"
                output(line)
                if self.log:
                    self.log.output("stderr", line + "\n")
                await asyncio.sleep(0.08)
            if job.operation == Operation.READ and job.path:
                if job.advanced.read_format == "ihex":
                    Path(job.path).write_text(_encoded_read("ihex"))
                elif job.advanced.read_format == "srec":
                    Path(job.path).write_text(_encoded_read("srec"))
                elif job.advanced.read_format == "bin":
                    Path(job.path).write_bytes(b"\xff" * 32768)
                else:
                    raise ValueError(
                        f"Unsupported read format: {job.advanced.read_format}"
                    )
            if self.log:
                self.log.finished(0)
            return Result(0, "DEMO completed; no hardware was accessed.")
        except asyncio.CancelledError:
            if self.log:
                self.log.finished(-15)
            raise
