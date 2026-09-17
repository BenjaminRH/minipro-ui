"""CLI contract: every exposed operation uses the same dialect and runner."""

import json
from dataclasses import replace

import pytest

from minipro_ui.backend import BackendError, CliAdapter
from minipro_ui.models import Job, Operation
from minipro_ui.process import Result

# Independent expectations make changing implementation flags break the contract.
FLAGS = {
    Operation.READ: "-r",
    Operation.WRITE: "-w",
    Operation.VERIFY: "-m",
    Operation.BLANK: "-b",
    Operation.ERASE: "-E",
    Operation.ID: "-D",
    Operation.TEST: "-T",
}


@pytest.mark.parametrize("operation", list(Operation))
@pytest.mark.parametrize(
    "interface,flag", [("zif", None), ("icsp", "-i"), ("external", "-I")]
)
@pytest.mark.parametrize("memory", ["code", "data", "config", "user", "calibration"])
async def test_operation_contract(
    adapter, tmp_path, monkeypatch, operation, interface, flag, memory
):
    capture = tmp_path / "argv.jsonl"
    monkeypatch.setenv("MINIPRO_TEST_ARGV", str(capture))
    file = tmp_path / "image with spaces;$(touch forbidden).bin"
    file.write_bytes(b"\xff" * 32768)
    job = Job(
        operation,
        "AT28C256@DIP28",
        file if operation.needs_file else None,
        memory,
        interface,
    )
    output = []
    result = await adapter.execute(job, output.append)
    assert result.returncode == 0
    assert "100%" in result.output
    argv = json.loads(capture.read_text().splitlines()[-1])
    expected = ["-p", "AT28C256@DIP28"]
    if flag:
        expected.append(flag)
    if operation in (
        Operation.READ,
        Operation.WRITE,
        Operation.VERIFY,
        Operation.BLANK,
    ):
        expected += ["-c", memory]
    expected += [FLAGS[operation]]
    if operation.needs_file:
        expected.append(str(file))
    assert argv == expected
    assert not (tmp_path / "forbidden").exists()


async def test_metadata_contract(adapter):
    version = await adapter.version()
    assert version.release == "0.7.4" and version.recognized
    assert await adapter.programmers() == ["TL866A", "TL866II", "T48", "T56"]
    assert await adapter.connected() == ["T48"]
    names = await adapter.devices("T48")
    assert names == ["AT28C256@DIP28", "W25Q32JV@SOIC8", "7400", "82HS641B"]
    assert "warning" not in " ".join(names)
    info = await adapter.device_info("T48", names[0])
    assert info.code_bytes == 32768 and not info.logic
    assert (await adapter.device_info("T48", "7400")).logic


async def test_unsupported_programmer_is_explicit(adapter):
    with pytest.raises(BackendError, match="does not support T76"):
        await adapter.devices("T76")


async def test_unknown_version_can_be_inspected_but_not_run(adapter, monkeypatch):
    monkeypatch.setenv("MINIPRO_TEST_VERSION", "0.8.0")
    assert (await adapter.version()).release == "0.8.0"
    assert await adapter.devices("T48")
    with pytest.raises(BackendError, match="Unverified"):
        await adapter.execute(Job(Operation.ID, "chip"), lambda _: None)
    candidate = CliAdapter(adapter.executable, allow_unverified=True)
    assert (
        await candidate.execute(Job(Operation.ID, "chip"), lambda _: None)
    ).returncode == 0


async def test_unknown_revision_is_not_a_release(adapter):
    version = await adapter.version()
    assert not replace(version, revision="newdevelopmentcommit").recognized


@pytest.mark.parametrize("operation", list(Operation))
async def test_failure_never_becomes_success(adapter, monkeypatch, operation, tmp_path):
    await adapter.version()
    monkeypatch.setenv("MINIPRO_TEST_MODE", "failure")
    result = await adapter.execute(
        Job(operation, "chip", tmp_path / "file"), lambda _: None
    )
    assert result.returncode == 7
    assert "Verification OK" in result.output
    assert "Unexpected device failure" in result.output


async def test_malformed_metadata_fails_closed(adapter, monkeypatch):
    monkeypatch.setenv("MINIPRO_TEST_MODE", "bad_metadata")
    with pytest.raises(BackendError, match="Unrecognized device information"):
        await adapter.device_info("T48", "chip")


async def test_missing_executable_is_actionable(tmp_path):
    with pytest.raises(BackendError, match="Cannot run"):
        await CliAdapter(tmp_path / "missing").version()


async def test_disconnected_is_not_a_connected_programmer(adapter, monkeypatch):
    monkeypatch.setenv("MINIPRO_TEST_MODE", "disconnected")
    assert await adapter.connected() == []


async def test_id_mismatch_retry_is_marked_and_y_is_added_once(
    adapter, monkeypatch, tmp_path
):
    capture = tmp_path / "argv.jsonl"
    monkeypatch.setenv("MINIPRO_TEST_ARGV", str(capture))
    monkeypatch.setenv("MINIPRO_TEST_MODE", "id_mismatch")

    refused = await adapter.execute(Job(Operation.ID, "chip"), lambda _: None)
    assert refused.returncode == 1
    assert refused.retry_with_id_override
    assert "-y" not in json.loads(capture.read_text().splitlines()[-1])

    continued = await adapter.execute(
        Job(Operation.ID, "chip", continue_on_id_mismatch=True), lambda _: None
    )
    assert continued.returncode == 0
    assert not continued.retry_with_id_override
    argv = json.loads(capture.read_text().splitlines()[-1])
    assert argv.count("-y") == 1


@pytest.mark.parametrize(
    "returncode, output",
    [
        (0, "(use '-y' to continue anyway at your own risk)"),
        (-15, "(use '-y' to continue anyway at your own risk)"),
        (1, "chip ID mismatch"),
        (1, "usage: minipro [-y]"),
        (1, "(use '-Y' to continue anyway at your own risk)"),
    ],
)
async def test_id_mismatch_retry_requires_positive_exit_and_explicit_lowercase_hint(
    adapter, returncode, output
):
    await adapter.version()

    async def fake_run(arguments, output_callback=None, *, timeout=None):
        return Result(returncode, output)

    adapter._run = fake_run
    result = await adapter.execute(Job(Operation.ID, "chip"), lambda _: None)
    assert not result.retry_with_id_override


async def test_id_mismatch_hint_allows_case_and_whitespace_variants(adapter):
    await adapter.version()

    async def fake_run(arguments, output_callback=None, *, timeout=None):
        return Result(
            1,
            '(\n USE "-y"\n TO  CONTINUE ANYWAY at your own risk \n)',
        )

    adapter._run = fake_run
    result = await adapter.execute(Job(Operation.ID, "chip"), lambda _: None)
    assert result.retry_with_id_override


async def test_id_mismatch_hint_is_not_sticky_for_overridden_job(adapter):
    await adapter.version()

    async def fake_run(arguments, output_callback=None, *, timeout=None):
        return Result(1, "(use '-y' to continue anyway at your own risk)")

    adapter._run = fake_run
    result = await adapter.execute(
        Job(Operation.ID, "chip", continue_on_id_mismatch=True), lambda _: None
    )
    assert not result.retry_with_id_override
