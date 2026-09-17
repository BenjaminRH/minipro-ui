"""Raw logging retains stream content and never puts UI messages in history."""

import asyncio
import json
import sys

import pytest

from minipro_ui.backend import BackendError, CliAdapter
from minipro_ui.demo import DemoBackend
from minipro_ui.models import Job, Operation
from minipro_ui.process import run_process
from minipro_ui.session_log import SessionLog
from minipro_ui.settings import Settings


async def test_raw_streams_command_and_incremental_file(tmp_path):
    log = SessionLog()
    log.configure(True, str(tmp_path / "minipro_%Y-%m-%d_%H-%M-%S.log"))
    await run_process(
        [
            sys.executable,
            "-c",
            "import sys; print('raw out'); sys.stderr.write('raw\\rerr\\n')",
        ],
        log=log,
    )
    assert "[stdout]\nraw out\n" in log.text
    assert "[stderr]\nraw\rerr\n" in log.text
    assert sys.executable in log.text
    assert "[exit 0]" in log.text
    assert log.path is not None
    assert "%Y" not in log.path.name
    with log.path.open(newline="") as saved:
        assert saved.read() == log.text
    log.close()


async def test_real_adapter_omits_repeated_presence_checks_from_logs(
    fake_executable, tmp_path, monkeypatch
):
    capture = tmp_path / "argv.jsonl"
    monkeypatch.setenv("MINIPRO_TEST_ARGV", str(capture))
    log = SessionLog()
    log.configure(True, str(tmp_path / "session.log"))
    backend = CliAdapter(fake_executable, log=log)
    await backend.version()
    after_metadata = log.text
    await backend.connected()
    await backend.connected()
    assert log.text == after_metadata
    assert log.path is not None
    with log.path.open(newline="") as saved:
        assert saved.read() == after_metadata
    assert [json.loads(line) for line in capture.read_text().splitlines()] == [
        ["-V"],
        ["-k"],
        ["-k"],
    ]

    await backend.execute(Job(Operation.ID, "chip"), lambda _: None)
    assert log.text != after_metadata
    assert "-D" in log.text and "Operation completed" in log.text
    with log.path.open(newline="") as saved:
        assert saved.read() == log.text

    before_failed_presence = log.text
    monkeypatch.setenv("MINIPRO_TEST_MODE", "failure")
    with pytest.raises(BackendError, match="Unexpected device failure"):
        await backend.connected()
    assert log.text == before_failed_presence
    with log.path.open(newline="") as saved:
        assert saved.read() == before_failed_presence
    assert [json.loads(line) for line in capture.read_text().splitlines()][-1] == ["-k"]


async def test_demo_presence_checks_are_not_logged():
    log = SessionLog()
    backend = DemoBackend(log=log)
    assert await backend.connected() == ["T48"]
    assert log.text == ""
    await backend.version()
    after_version = log.text
    await backend.connected()
    assert log.text == after_version


def test_opt_in_backfills_and_never_overwrites(tmp_path):
    log = SessionLog()
    path = tmp_path / "session.log"
    log.command(["minipro", "-p", "chip with spaces", "-r", "backup.bin"])
    assert not path.exists()
    path.write_text("existing")
    log.configure(True, str(path))
    assert path.read_text() == "existing"
    assert log.path != path
    assert "'chip with spaces'" in log.path.read_text()
    saved = log.path
    log.configure(False, str(path))
    log.output("stdout", "later")
    assert "later" not in saved.read_text()


def test_file_failure_does_not_lose_memory_history(tmp_path):
    errors = []
    log = SessionLog(errors.append)
    log.configure(True, str(tmp_path / "missing" / "session.log"))
    log.command(["minipro", "-V"])
    log.output("stderr", "version")
    assert errors and "version" in log.text


async def test_cancel_retains_partial_output(tmp_path):
    log = SessionLog()
    task = asyncio.create_task(
        run_process(
            [
                sys.executable,
                "-u",
                "-c",
                "import time; print('before cancel'); time.sleep(60)",
            ],
            log=log,
        )
    )
    for _ in range(100):
        if "[stdout]\nbefore cancel" in log.text:
            break
        await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert "[stdout]\nbefore cancel" in log.text
    assert "[exit" in log.text


def test_logging_settings_default_off_and_roundtrip(tmp_path):
    assert not Settings().save_log
    settings = Settings(save_log=True, log_path=str(tmp_path / "%Y.log"))
    destination = tmp_path / "settings.json"
    settings.save(destination)
    assert Settings.load(destination) == settings
