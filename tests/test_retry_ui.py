"""UI coverage for the explicit minipro ID-mismatch retry."""

from __future__ import annotations

import asyncio
import shlex
from pathlib import Path
from typing import Any

import pytest
from textual.widgets import Button, Static

from minipro_ui.app import MiniproApp
from minipro_ui.demo import DemoBackend
from minipro_ui.models import Job, Operation
from minipro_ui.process import OutputCallback, Result
from minipro_ui.screens import RunScreen
from minipro_ui.settings import Settings
from minipro_ui.workflow import Workflow


class RetryBackend(DemoBackend):
    def __init__(
        self,
        *,
        result: Result | None = None,
        retry_result: Result | None = None,
        retry_gate: asyncio.Event | None = None,
    ) -> None:
        super().__init__()
        self.initial_result = result or Result(
            1,
            "Device ID mismatch; use '-y' to continue anyway at your own risk",
            retry_with_id_override=True,
        )
        self.retry_result = retry_result or Result(0, "override completed")
        self.retry_gate = retry_gate
        self.calls: list[Job] = []

    async def execute(self, job: Job, output: OutputCallback) -> Result:
        self.calls.append(job)
        if len(self.calls) == 1:
            output(self.initial_result.output)
            if self.initial_result.returncode == 0 and job.path is not None:
                Path(job.path).write_bytes(b"\xff" * 32768)
            return self.initial_result
        assert job.continue_on_id_mismatch
        assert job.path is not None
        if self.retry_gate is not None:
            await self.retry_gate.wait()
        output(self.retry_result.output)
        if self.retry_result.returncode != 0:
            return self.retry_result
        Path(job.path).write_bytes(b"\xff" * 32768)
        return self.retry_result


async def mount_run(
    pilot: Any, app: MiniproApp, backend: RetryBackend, destination: Path
) -> RunScreen:
    await pilot.pause(0.5)
    workflow = Workflow(backend)
    prepared = await workflow.prepare(
        "T48", Job(Operation.READ, "W25Q32JV@SOIC8", destination)
    )
    app.operation_started(prepared.job)
    app.push_screen(RunScreen(workflow, prepared))
    for _ in range(100):
        await pilot.pause(0.05)
        if isinstance(app.screen, RunScreen) and not app.screen.running:
            return app.screen
    raise AssertionError("run did not finish")


@pytest.mark.parametrize("size", [(80, 24), (120, 40)])
async def test_explicit_id_override_retry_stays_in_run_screen(
    size: tuple[int, int], tmp_path: Path
) -> None:
    backend = RetryBackend()
    app = MiniproApp(demo=True, backend=backend, settings=Settings())
    destination = tmp_path / "read.bin"
    async with app.run_test(size=size) as pilot:
        screen = await mount_run(pilot, app, backend, destination)
        assert isinstance(screen, RunScreen)
        assert screen.result is not None
        assert screen.result.retry_with_id_override
        assert screen.query_one("#retry", Button).display
        assert not screen.query_one("#retry", Button).disabled
        assert screen.query_one("#retry-explanation", Static).display
        assert screen.query_one("#done", Button).has_focus
        assert screen.query_one("#retry", Button).region.right <= size[0]
        assert screen.query_one("#done", Button).region.bottom <= size[1]
        assert not app.configuration_locked

        await pilot.click("#retry")
        for _ in range(100):
            await pilot.pause(0.05)
            if not screen.running:
                break
        assert len(backend.calls) == 2
        assert screen.result is not None and screen.result.returncode == 0
        assert screen.prepared.job.continue_on_id_mismatch
        assert screen.prepared.command.count("-y") == 1
        assert destination.read_bytes() == b"\xff" * 32768
        assert "Retry approved" in "\n".join(screen.transcript)
        assert "$ " + shlex.join(screen.prepared.command) in "\n".join(
            screen.transcript
        )
        assert app.operation_state == "IDLE"
        assert not app.configuration_locked
        assert not screen.query_one("#retry", Button).display
        assert app.current_job().continue_on_id_mismatch is False
        assert not any(
            command.name == "Continue at your own risk" for command in app.commands()
        )


@pytest.mark.parametrize(
    "result",
    [
        Result(0, "success", retry_with_id_override=True),
        Result(1, "generic error", retry_with_id_override=False),
    ],
)
async def test_success_and_generic_failure_hide_override(
    tmp_path: Path, result: Result
) -> None:
    backend = RetryBackend(result=result)
    app = MiniproApp(demo=True, backend=backend, settings=Settings())
    async with app.run_test() as pilot:
        screen = await mount_run(pilot, app, backend, tmp_path / "read.bin")
        assert not screen.retry_available
        assert not screen.query_one("#retry", Button).display


async def test_cancel_does_not_offer_override(tmp_path: Path) -> None:
    gate = asyncio.Event()
    backend = RetryBackend(retry_gate=gate)
    app = MiniproApp(demo=True, backend=backend, settings=Settings())
    async with app.run_test() as pilot:
        screen = await mount_run(pilot, app, backend, tmp_path / "read.bin")
        # First refusal offers a retry; keep the approved retry blocked long
        # enough to inspect the synchronous lock and prevent a second start.
        assert screen.retry_available
        await pilot.click("#retry")
        await pilot.pause(0.1)
        assert screen.running
        assert app.operation_state == "READING"
        assert app.configuration_locked
        assert screen.query_one("#done", Button).disabled
        assert screen.query_one("#export", Button).disabled
        assert len(backend.calls) == 2
        screen.retry_operation()
        assert len(backend.calls) == 2
        await pilot.click("#stop")
        await pilot.pause(0.2)
        finished_screen = app.screen
        assert isinstance(finished_screen, RunScreen)
        assert not finished_screen.running
        assert not finished_screen.retry_available
        assert app.operation_state == "IDLE"
        assert not app.configuration_locked


async def test_preparation_rejection_does_not_run_again(tmp_path: Path) -> None:
    backend = RetryBackend()
    app = MiniproApp(demo=True, backend=backend, settings=Settings())
    destination = tmp_path / "read.bin"
    async with app.run_test() as pilot:
        screen = await mount_run(pilot, app, backend, destination)
        destination.write_bytes(b"keep")
        await pilot.click("#retry")
        for _ in range(100):
            await pilot.pause(0.05)
            if not screen.running:
                break
        assert len(backend.calls) == 1
        assert destination.read_bytes() == b"keep"
        assert app.operation_state == "IDLE"
        assert not app.configuration_locked
        assert not screen.retry_available


async def test_retry_result_cannot_offer_a_third_attempt(tmp_path: Path) -> None:
    backend = RetryBackend(
        retry_result=Result(
            1,
            "still mismatched; use '-y' to continue anyway at your own risk",
            retry_with_id_override=True,
        )
    )
    app = MiniproApp(demo=True, backend=backend, settings=Settings())
    async with app.run_test() as pilot:
        screen = await mount_run(pilot, app, backend, tmp_path / "read.bin")
        await pilot.click("#retry")
        for _ in range(100):
            await pilot.pause(0.05)
            if not screen.running:
                break
        assert len(backend.calls) == 2
        assert screen.result is not None and screen.result.retry_with_id_override
        assert not screen.retry_available
        assert not screen.query_one("#retry", Button).display
