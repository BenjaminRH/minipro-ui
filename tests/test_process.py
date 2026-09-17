"""Exercise real subprocess boundaries, including cancellation and pipe pressure."""

import asyncio
import sys

import pytest

from minipro_ui.process import run_process


async def test_separate_streams_and_carriage_return_progress():
    lines = []
    result = await run_process(
        [
            sys.executable,
            "-c",
            "import sys; print('catalog'); sys.stderr.write('Read 0%\\rRead 100%\\n')",
        ],
        output=lines.append,
    )
    assert result.stdout == "catalog\n"
    assert "Read 0%" in lines and "Read 100%" in lines
    assert result.returncode == 0


async def test_large_output_does_not_deadlock():
    result = await run_process(
        [
            sys.executable,
            "-c",
            "import sys; sys.stdout.write('a'*100000); sys.stderr.write('b'*100000)",
        ],
        timeout=5,
    )
    assert len(result.stdout) == 100000
    assert len(result.output) == 200000


async def test_timeout_reaps_process():
    with pytest.raises(TimeoutError):
        await run_process(
            [sys.executable, "-c", "import time; time.sleep(60)"], timeout=0.05
        )


async def test_cancellation_reaps_process(tmp_path):
    import os

    pidfile = tmp_path / "pid"
    task = asyncio.create_task(
        run_process(
            [
                sys.executable,
                "-c",
                (
                    "import os,time,pathlib,sys; "
                    "pathlib.Path(sys.argv[1]).write_text(str(os.getpid())); "
                    "time.sleep(60)"
                ),
                str(pidfile),
            ]
        )
    )
    for _ in range(100):
        if pidfile.exists():
            break
        await asyncio.sleep(0.01)
    assert pidfile.exists()
    pid = int(pidfile.read_text())
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


async def test_stdin_closed_and_non_utf8_output():
    result = await run_process(
        [
            sys.executable,
            "-c",
            (
                "import sys; assert not sys.stdin.read(); "
                "sys.stdout.buffer.write(b'\\xff'); sys.exit(3)"
            ),
        ],
        timeout=2,
    )
    assert result.returncode == 3 and "\ufffd" in result.output
