"""Non-interactive subprocess execution with streaming, bounded diagnostics."""

from __future__ import annotations

import asyncio
import codecs
import contextlib
import os
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from .session_log import SessionLog

OutputCallback = Callable[[str], None]
ANSI = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
LIMIT = 2_000_000


@dataclass(frozen=True)
class Result:
    returncode: int
    output: str
    stdout: str = ""
    retry_with_id_override: bool = False


async def run_process(
    arguments: Sequence[str],
    *,
    output: OutputCallback | None = None,
    cwd: Path | None = None,
    timeout: float | None = None,
    log: SessionLog | None = None,
) -> Result:
    """Drain both streams without a shell; terminate and reap on cancellation.

    stdin is closed so unexpected CLI prompts cannot hang invisibly. stdout is
    retained separately for catalogs. Diagnostic text is bounded, and never used
    as a substitute for the exit code when determining success.
    """
    if log:
        log.command(arguments)
    process = await asyncio.create_subprocess_exec(
        *arguments,
        cwd=cwd,
        env={**os.environ, "LC_ALL": "C", "NO_COLOR": "1"},
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    transcript = ""

    async def drain(stream: asyncio.StreamReader | None, name: str) -> str:
        nonlocal transcript
        assert stream is not None
        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        pending = ""
        captured = ""
        while chunk := await stream.read(4096):
            decoded = decoder.decode(chunk)
            if log:
                log.output(name, decoded)
            captured = (captured + decoded)[-LIMIT:]
            transcript = (transcript + decoded)[-LIMIT:]
            parts = re.split(r"[\r\n]", pending + decoded)
            pending = parts.pop()[-LIMIT:]
            for line in parts:
                if output and line.strip():
                    output(ANSI.sub("", line))
        tail = decoder.decode(b"", final=True)
        pending += tail
        if log:
            log.output(name, tail)
        if output and pending.strip():
            output(ANSI.sub("", pending))
        return ANSI.sub("", captured)

    drains = asyncio.gather(
        drain(process.stdout, "stdout"), drain(process.stderr, "stderr")
    )
    try:
        async with asyncio.timeout(timeout):
            stdout, _ = await asyncio.shield(drains)
            return Result(await process.wait(), ANSI.sub("", transcript), stdout)
    except BaseException:
        if process.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=3)
            except TimeoutError:
                process.kill()
                await process.wait()
        await drains
        raise
    finally:
        if log:
            log.finished(process.returncode)
