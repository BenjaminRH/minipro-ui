"""File integrity, review invariants, and failure behavior at the workflow boundary."""

from dataclasses import replace

import pytest

from minipro_ui.backend import BackendError
from minipro_ui.models import DeviceInfo, Job, Operation
from minipro_ui.workflow import Workflow


@pytest.mark.parametrize("operation", list(Operation))
async def test_every_operation_through_workflow(
    adapter, tmp_path, operation, monkeypatch
):
    path = tmp_path / "image.bin"
    if operation in (Operation.WRITE, Operation.VERIFY):
        path.write_bytes(b"\xff" * 32768)
    name = "7400" if operation == Operation.TEST else "AT28C256@DIP28"
    flow = Workflow(adapter)
    prepared = await flow.prepare(
        "T48", Job(operation, name, path if operation.needs_file else None)
    )
    commands = []
    execute = adapter.execute

    async def record_command(job, output):
        commands.append(tuple(adapter.command(job)))
        return await execute(job, output)

    monkeypatch.setattr(adapter, "execute", record_command)
    assert (await flow.execute(prepared, lambda _: None)).returncode == 0
    assert commands == [prepared.command]
    if prepared.staging is not None:
        assert not prepared.execution_job.path.parent.exists()
    if operation == Operation.READ:
        assert path.read_bytes() == b"\xff" * 32768


async def test_failed_read_leaves_no_backup(adapter, tmp_path, monkeypatch):
    flow = Workflow(adapter)
    path = tmp_path / "backup.bin"
    prepared = await flow.prepare("T48", Job(Operation.READ, "AT28C256@DIP28", path))
    await adapter.version()
    # Fail the operation, not the connection check.
    original = adapter.execute

    async def fail(job, output):
        monkeypatch.setenv("MINIPRO_TEST_MODE", "failure")
        return await original(job, output)

    adapter.execute = fail
    assert (await flow.execute(prepared, lambda _: None)).returncode == 7
    assert not path.exists()
    assert not list(tmp_path.glob("minipro-ui-*"))


async def test_zero_exit_without_read_data_fails(adapter, tmp_path, monkeypatch):
    flow = Workflow(adapter)
    path = tmp_path / "backup.bin"
    prepared = await flow.prepare("T48", Job(Operation.READ, "AT28C256@DIP28", path))
    monkeypatch.setenv("MINIPRO_TEST_MODE", "missing_output")
    with pytest.raises(BackendError, match="no backup data"):
        await flow.execute(prepared, lambda _: None)
    assert not path.exists()


async def test_file_changed_after_review_is_rejected(adapter, tmp_path):
    path = tmp_path / "image.bin"
    path.write_bytes(b"\xff" * 32768)
    flow = Workflow(adapter)
    prepared = await flow.prepare("T48", Job(Operation.WRITE, "AT28C256@DIP28", path))
    path.write_bytes(b"\x00" * 32768)
    with pytest.raises(ValueError, match="changed after review"):
        await flow.execute(prepared, lambda _: None)


async def test_read_does_not_overwrite_a_concurrently_created_file(adapter, tmp_path):
    path = tmp_path / "backup.bin"
    flow = Workflow(adapter)
    prepared = await flow.prepare("T48", Job(Operation.READ, "AT28C256@DIP28", path))
    original = adapter.execute

    async def race(job, output):
        path.write_bytes(b"keep me")
        return await original(job, output)

    adapter.execute = race
    with pytest.raises(FileExistsError):
        await flow.execute(prepared, lambda _: None)
    assert path.read_bytes() == b"keep me"


async def test_binary_size_mismatch_is_rejected(adapter, tmp_path):
    path = tmp_path / "small.bin"
    path.write_bytes(b"1234")
    with pytest.raises(ValueError, match="32,768 bytes"):
        await Workflow(adapter).prepare(
            "T48", Job(Operation.WRITE, "AT28C256@DIP28", path)
        )


async def test_structured_input_is_not_checked_as_raw_capacity(adapter, tmp_path):
    path = tmp_path / "image.hex"
    path.write_text(":00000001FF\n")
    prepared = await Workflow(adapter).prepare(
        "T48", Job(Operation.WRITE, "AT28C256@DIP28", path)
    )
    assert prepared.digest


@pytest.mark.parametrize(
    "change, message",
    [
        ({"device": ""}, "Choose a device"),
        ({"memory": "bad"}, "Unknown memory"),
        ({"interface": "bad"}, "Unknown connection"),
        ({"operation": Operation.WRITE, "memory": "calibration"}, "read-only"),
    ],
)
def test_invalid_requests(change, message):
    with pytest.raises(ValueError, match=message):
        replace(Job(Operation.ID, "chip"), **change).validate(DeviceInfo("chip", ""))


@pytest.mark.parametrize(
    "operation", [Operation.READ, Operation.WRITE, Operation.VERIFY]
)
def test_missing_file_is_rejected(operation):
    with pytest.raises(ValueError, match="Choose a file"):
        Job(operation, "chip").validate(DeviceInfo("chip", ""))


def test_unknown_device_capabilities_are_rejected_after_request_checks(tmp_path):
    image = tmp_path / "image.bin"
    image.write_bytes(b"image")
    with pytest.raises(ValueError, match="capability metadata"):
        Job(Operation.WRITE, "chip", image).validate(DeviceInfo("chip", ""))


def test_read_rejects_existing_file_and_dangling_symlink(tmp_path):
    path = tmp_path / "backup"
    path.symlink_to(tmp_path / "missing")
    with pytest.raises(ValueError, match="already exists"):
        Job(Operation.READ, "chip", path).validate(DeviceInfo("chip", ""))


async def test_wrong_programmer_blocks_operation(adapter, tmp_path):
    flow = Workflow(adapter)
    prepared = await flow.prepare("T56", Job(Operation.ID, "AT28C256@DIP28"))
    with pytest.raises(BackendError, match="Expected one T56"):
        await flow.execute(prepared, lambda _: None)


async def test_two_identical_programmers_are_not_collapsed(adapter, monkeypatch):
    flow = Workflow(adapter)
    prepared = await flow.prepare("T48", Job(Operation.ID, "AT28C256@DIP28"))
    monkeypatch.setenv("MINIPRO_TEST_MODE", "multiple")
    with pytest.raises(BackendError, match="Expected one"):
        await flow.execute(prepared, lambda _: None)


async def test_partial_read_is_not_published(adapter, tmp_path):
    from minipro_ui.process import Result

    async def incomplete(job, output):
        job.path.write_bytes(b"partial")
        return Result(0, "done")

    adapter.execute = incomplete
    flow = Workflow(adapter)
    destination = tmp_path / "backup.bin"
    prepared = await flow.prepare(
        "T48", Job(Operation.READ, "AT28C256@DIP28", destination)
    )
    with pytest.raises(BackendError, match="Backup size"):
        await flow.execute(prepared, lambda _: None)
    assert not destination.exists()


async def test_id_mismatch_retry_rebuilds_staging_and_succeeds(
    adapter, tmp_path, monkeypatch
):
    image = tmp_path / "image.bin"
    image.write_bytes(b"\xff" * 32768)
    monkeypatch.setenv("MINIPRO_TEST_MODE", "id_mismatch")
    flow = Workflow(adapter)
    prepared = await flow.prepare("T48", Job(Operation.WRITE, "AT28C256@DIP28", image))
    refused = await flow.execute(prepared, lambda _: None)
    assert refused.returncode == 1 and refused.retry_with_id_override
    assert prepared.execution_job.path is not None
    assert not prepared.execution_job.path.parent.exists()

    retry = await flow.prepare_retry(prepared, refused)
    assert retry.job.continue_on_id_mismatch
    assert retry.command.count("-y") == 1
    assert retry.staging is not None
    retry_path = retry.execution_job.path
    succeeded = await flow.execute(retry, lambda _: None)
    assert succeeded.returncode == 0 and not succeeded.retry_with_id_override
    assert retry_path is not None and not retry_path.parent.exists()


async def test_id_mismatch_retry_rejects_changed_input_and_cleans_staging(
    adapter, tmp_path, monkeypatch
):
    image = tmp_path / "image.bin"
    image.write_bytes(b"\xff" * 32768)
    monkeypatch.setenv("MINIPRO_TEST_MODE", "id_mismatch")
    flow = Workflow(adapter)
    prepared = await flow.prepare("T48", Job(Operation.WRITE, "AT28C256@DIP28", image))
    refused = await flow.execute(prepared, lambda _: None)
    image.write_bytes(b"\x00" * 32768)

    original_prepare = flow.prepare
    retry_staging: list = []

    async def record_prepare(programmer, job):
        fresh = await original_prepare(programmer, job)
        assert fresh.execution_job.path is not None
        retry_staging.append(fresh.execution_job.path.parent)
        return fresh

    monkeypatch.setattr(flow, "prepare", record_prepare)
    with pytest.raises(ValueError, match="changed after review"):
        await flow.prepare_retry(prepared, refused)
    assert retry_staging and not retry_staging[0].exists()


async def test_id_mismatch_retry_rechecks_read_destination(
    adapter, tmp_path, monkeypatch
):
    destination = tmp_path / "backup.bin"
    monkeypatch.setenv("MINIPRO_TEST_MODE", "id_mismatch")
    flow = Workflow(adapter)
    prepared = await flow.prepare(
        "T48", Job(Operation.READ, "AT28C256@DIP28", destination)
    )
    refused = await flow.execute(prepared, lambda _: None)
    destination.write_bytes(b"keep me")
    with pytest.raises(ValueError, match="already exists"):
        await flow.prepare_retry(prepared, refused)
    assert destination.read_bytes() == b"keep me"
    assert not list(tmp_path.glob("minipro-ui-*"))


async def test_id_mismatch_retry_rejects_changed_command(
    adapter, tmp_path, monkeypatch
):
    image = tmp_path / "image.bin"
    image.write_bytes(b"\xff" * 32768)
    monkeypatch.setenv("MINIPRO_TEST_MODE", "id_mismatch")
    flow = Workflow(adapter)
    prepared = await flow.prepare("T48", Job(Operation.WRITE, "AT28C256@DIP28", image))
    refused = await flow.execute(prepared, lambda _: None)
    original_command = adapter.command

    def changed_command(job):
        return [*original_command(job), "-Y"]

    monkeypatch.setattr(adapter, "command", changed_command)
    with pytest.raises(BackendError, match="command changed"):
        await flow.prepare_retry(prepared, refused)
    assert prepared.execution_job.path is not None
    assert not prepared.execution_job.path.parent.exists()


async def test_id_mismatch_retry_rejects_changed_device_info(
    adapter, tmp_path, monkeypatch
):
    image = tmp_path / "image.bin"
    image.write_bytes(b"\xff" * 32768)
    monkeypatch.setenv("MINIPRO_TEST_MODE", "id_mismatch")
    flow = Workflow(adapter)
    prepared = await flow.prepare("T48", Job(Operation.WRITE, "AT28C256@DIP28", image))
    refused = await flow.execute(prepared, lambda _: None)
    original_info = prepared.info
    changed_info = replace(original_info, details=original_info.details + " changed")

    async def changed_device_info(programmer, name):
        return changed_info

    monkeypatch.setattr(adapter, "device_info", changed_device_info)

    original_prepare = flow.prepare
    retry_staging: list = []

    async def record_prepare(programmer, job):
        fresh = await original_prepare(programmer, job)
        assert fresh.execution_job.path is not None
        retry_staging.append(fresh.execution_job.path.parent)
        return fresh

    monkeypatch.setattr(flow, "prepare", record_prepare)
    with pytest.raises(BackendError, match="Device information changed"):
        await flow.prepare_retry(prepared, refused)
    assert retry_staging and not retry_staging[0].exists()
