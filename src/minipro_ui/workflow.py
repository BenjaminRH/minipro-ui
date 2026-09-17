"""Reviewable jobs, immutable input snapshots, and atomic backup publication."""

from __future__ import annotations

import asyncio
import hashlib
import tempfile
from dataclasses import dataclass, field, replace
from pathlib import Path

from .backend import Backend, BackendError
from .file_io import FileChoice, publish_file
from .models import DeviceInfo, Job, Operation
from .process import OutputCallback, Result


def file_digest(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


@dataclass(frozen=True)
class PreparedJob:
    job: Job
    programmer: str
    info: DeviceInfo
    digest: str | None
    execution_job: Job
    command: tuple[str, ...]
    staging: tempfile.TemporaryDirectory[str] | None = field(repr=False, compare=False)

    def close(self) -> None:
        """Release the reserved command path on cancellation or completion."""
        if self.staging is not None:
            self.staging.cleanup()


class Workflow:
    """Serialize jobs and keep hardware-independent checks outside the adapter."""

    def __init__(self, backend: Backend) -> None:
        self.backend = backend
        self._lock = asyncio.Lock()

    async def prepare(self, programmer: str, job: Job) -> PreparedJob:
        info = await self.backend.device_info(programmer, job.device)
        if (info.operations is None or info.regions is None) and not info.logic:
            raise BackendError(
                "Device capability metadata is unavailable; refusing to run "
                "an unverified operation."
            )
        job.validate(info)
        digest = None
        if job.path and job.operation in (Operation.WRITE, Operation.VERIFY):
            # Structured formats have address records: let minipro decode them.
            if (
                job.memory == "code"
                and info.code_bytes is not None
                and job.path.suffix.lower() == ".bin"
                and job.advanced.size_policy == "exact"
                and job.path.stat().st_size != info.code_bytes
            ):
                raise ValueError(
                    f"Binary file is {job.path.stat().st_size:,} bytes; the chip's "
                    f"code region is {info.code_bytes:,} bytes. "
                    "No padding or truncation is applied. "
                    "Prepare an exact-size image first."
                )
            digest = await asyncio.to_thread(file_digest, job.path)
        staging = None
        execution_job = job
        if job.path is not None:
            parent = job.path.parent if job.operation == Operation.READ else None
            staging = tempfile.TemporaryDirectory(prefix="minipro-ui-", dir=parent)
            execution_job = replace(job, path=Path(staging.name) / job.path.name)
        try:
            return PreparedJob(
                job,
                programmer,
                info,
                digest,
                execution_job,
                tuple(self.backend.command(execution_job)),
                staging,
            )
        except Exception:
            if staging is not None:
                staging.cleanup()
            raise

    async def prepare_retry(self, prepared: PreparedJob, result: Result) -> PreparedJob:
        """Prepare a fresh reviewed job with minipro's explicit ID override.

        The first execution always releases its staging directory. Rebuilding
        from the reviewed job also gives the normal preparation and execution
        checks a chance to reject changed files, destinations, or metadata.
        """
        if result.returncode <= 0 or not result.retry_with_id_override:
            raise ValueError("This result does not allow an ID-mismatch retry.")
        if prepared.job.continue_on_id_mismatch:
            raise ValueError("The reviewed job already uses the ID-mismatch override.")

        job = prepared.job
        execution_job = prepared.execution_job
        if replace(execution_job, path=job.path) != job:
            raise ValueError("The reviewed job changed. Review it again.")
        if tuple(self.backend.command(execution_job)) != prepared.command:
            raise BackendError("The command changed after review. Review again.")

        retry: PreparedJob | None = None
        try:
            retry = await self.prepare(
                prepared.programmer,
                replace(job, continue_on_id_mismatch=True),
            )
            if retry.info != prepared.info:
                raise BackendError("Device information changed. Review the job again.")
            if retry.digest != prepared.digest:
                raise ValueError(
                    "The input changed after review. Review the job again."
                )
            return retry
        except Exception:
            if retry is not None:
                retry.close()
            raise

    async def execute(self, prepared: PreparedJob, output: OutputCallback) -> Result:
        """Never expose a partial read as a backup, or write an unreviewed input.

        The staging directory is on the destination filesystem. A hard link
        publishes new reads exclusively. Explicitly confirmed replacements use
        atomic rename after rechecking the destination identity.
        """
        async with self._lock:
            try:
                job = prepared.job
                job.validate(prepared.info)
                connected = await self.backend.connected()
                if connected != [prepared.programmer]:
                    raise BackendError(
                        f"Expected one {prepared.programmer}; "
                        f"detected {connected or 'none'}. "
                        "Connect only the intended programmer before running a job."
                    )
                execution_job = prepared.execution_job
                if tuple(self.backend.command(execution_job)) != prepared.command:
                    raise BackendError(
                        "The command changed after review. Review again."
                    )
                snapshot = execution_job.path
                if (
                    job.path is not None
                    and snapshot is not None
                    and job.operation != Operation.READ
                ):
                    await asyncio.to_thread(
                        self._snapshot, job.path, snapshot, prepared.digest
                    )
                result = await self.backend.execute(execution_job, output)
                if (
                    result.returncode == 0
                    and job.operation == Operation.READ
                    and job.path is not None
                    and snapshot is not None
                ):
                    if not snapshot.is_file() or snapshot.stat().st_size == 0:
                        raise BackendError(
                            "minipro exited successfully but produced no backup data."
                        )
                    if (
                        job.memory == "code"
                        and job.advanced.read_format == "bin"
                        and prepared.info.code_bytes is not None
                        and snapshot.stat().st_size != prepared.info.code_bytes
                    ):
                        raise BackendError(
                            "Backup size does not match the code region."
                        )
                    publish_file(snapshot, FileChoice(job.path, job.replacement))
                    output(f"Saved {job.path} ({job.path.stat().st_size:,} bytes)")
                    output(f"SHA-256: {await asyncio.to_thread(file_digest, job.path)}")
                return result
            finally:
                prepared.close()

    @staticmethod
    def _snapshot(source: Path, destination: Path, expected: str | None) -> None:
        digest = hashlib.sha256()
        with source.open("rb") as incoming, destination.open("xb") as outgoing:
            while chunk := incoming.read(1024 * 1024):
                digest.update(chunk)
                outgoing.write(chunk)
        if digest.hexdigest() != expected:
            raise ValueError("The input changed after review. Review the job again.")
