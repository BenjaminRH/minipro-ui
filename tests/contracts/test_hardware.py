"""The same operation contracts for every binary, with explicit lab scenarios.

A profile describes physical test setup, not a version's expected text output.
Omitted cases remain UNTESTED. No hardware test is enabled by default.
"""

import hashlib
import json
from pathlib import Path

import pytest

from minipro_ui.models import Job, Operation
from minipro_ui.workflow import Workflow

pytestmark = pytest.mark.hardware


@pytest.mark.parametrize("operation", list(Operation))
async def test_operation(candidate, pytestconfig, tmp_path, operation):
    profile_name = pytestconfig.getoption("--hardware-profile")
    if not profile_name:
        pytest.skip("UNTESTED: no hardware profile supplied")
    profile_path = Path(profile_name).resolve()
    profile = json.loads(profile_path.read_text())
    cases = [case for case in profile["cases"] if case["operation"] == operation.name]
    if not cases:
        pytest.skip(f"UNTESTED: profile has no {operation.name} scenario")
    if (
        operation.destructive or operation == Operation.TEST
    ) and not pytestconfig.getoption("--allow-destructive"):
        pytest.skip("UNTESTED: requires --allow-destructive")
    for index, case in enumerate(cases):
        if operation == Operation.READ:
            assert case.get("expected_sha256"), (
                "Read tests require independently known data"
            )
            path = tmp_path / f"read-{index}.bin"
        elif operation.needs_file:
            path = (profile_path.parent / case["input"]).resolve()
        else:
            path = None
        job = Job(
            operation,
            case["device"],
            path,
            case.get("memory", "code"),
            case.get("interface", "zif"),
        )
        flow = Workflow(candidate)
        prepared = await flow.prepare(profile["programmer"], job)
        result = await flow.execute(prepared, lambda _: None)
        assert result.returncode == case.get("expected_exit", 0), result.output
        if operation == Operation.READ and result.returncode == 0:
            assert (
                hashlib.sha256(path.read_bytes()).hexdigest() == case["expected_sha256"]
            )
        if operation == Operation.WRITE and result.returncode == 0:
            verification = Job(
                Operation.VERIFY, job.device, path, job.memory, job.interface
            )
            checked = await flow.execute(
                await flow.prepare(profile["programmer"], verification), lambda _: None
            )
            assert checked.returncode == 0, checked.output
