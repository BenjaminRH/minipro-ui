"""Confirmed replacements preserve old files until successful publication."""

import pytest

from minipro_ui.demo import DemoBackend
from minipro_ui.file_io import FileChoice, FileStamp, nearest_directory, save_text
from minipro_ui.models import Job, Operation
from minipro_ui.process import Result
from minipro_ui.workflow import Workflow


def test_nearest_existing_directory(tmp_path):
    directory = tmp_path / "folder"
    directory.mkdir()
    assert nearest_directory(str(directory / "partial" / "file.bin")) == directory
    path = directory / "existing.bin"
    path.write_bytes(b"old")
    assert nearest_directory(str(path)) == directory


def test_confirmed_log_replacement_is_atomic(tmp_path):
    path = tmp_path / "log.txt"
    path.write_text("old log")
    with pytest.raises(FileExistsError):
        save_text(FileChoice(path), "new log")
    assert path.read_text() == "old log"
    choice = FileChoice(path, FileStamp.capture(path))
    save_text(choice, "new log")
    assert path.read_text() == "new log"
    assert not list(tmp_path.glob("minipro-ui-*"))


def test_modified_replacement_requires_new_confirmation(tmp_path):
    path = tmp_path / "log.txt"
    path.write_text("old log")
    choice = FileChoice(path, FileStamp.capture(path))
    path.write_text("changed meanwhile")
    with pytest.raises(ValueError, match="changed"):
        save_text(choice, "new log")
    assert path.read_text() == "changed meanwhile"


async def test_confirmed_read_replaces_only_after_success(tmp_path):
    path = tmp_path / "backup.bin"
    path.write_bytes(b"previous backup")
    job = Job(
        Operation.READ, "AT28C256@DIP28", path, replacement=FileStamp.capture(path)
    )
    flow = Workflow(DemoBackend())
    prepared = await flow.prepare("T48", job)
    assert path.read_bytes() == b"previous backup"
    await flow.execute(prepared, lambda _: None)
    assert path.read_bytes() == b"\xff" * 32768


async def test_failed_read_preserves_confirmed_destination(tmp_path):
    class Fails(DemoBackend):
        async def execute(self, job, output):
            job.path.write_bytes(b"partial")
            return Result(1, "failed")

    path = tmp_path / "backup.bin"
    path.write_bytes(b"keep this")
    flow = Workflow(Fails())
    job = Job(
        Operation.READ, "AT28C256@DIP28", path, replacement=FileStamp.capture(path)
    )
    prepared = await flow.prepare("T48", job)
    await flow.execute(prepared, lambda _: None)
    assert path.read_bytes() == b"keep this"
    path.write_bytes(b"changed")
    with pytest.raises(ValueError, match="changed"):
        await flow.execute(prepared, lambda _: None)
