"""Operation descriptions and immutable requests, independent of Textual."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from .file_io import FileStamp


class Operation(StrEnum):
    READ = "Read / back up"
    WRITE = "Program"
    VERIFY = "Verify"
    BLANK = "Blank check"
    ERASE = "Erase"
    ID = "Read chip ID"
    TEST = "Test logic / RAM"

    @property
    def needs_file(self) -> bool:
        return self in (self.READ, self.WRITE, self.VERIFY)

    @property
    def destructive(self) -> bool:
        return self in (self.WRITE, self.ERASE, self.TEST)


DESCRIPTIONS = {
    Operation.READ: "Save one memory region to a new file. The chip is unchanged.",
    Operation.WRITE: (
        "Program the selected memory region. minipro controls device-specific erase "
        "and write behavior, then verifies the result. Existing chip data may be lost."
    ),
    Operation.VERIFY: (
        "Compare the selected memory region against a file without writing."
    ),
    Operation.BLANK: "Check whether the selected memory region is in its erased state.",
    Operation.ERASE: (
        "Erase the device. This may affect more than the selected memory region. "
        "UV EPROMs require an external eraser; minipro decides device support."
    ),
    Operation.ID: (
        "Read the electronic identifier using the selected device algorithm. "
        "Not all chips have an ID; this is not universal chip identification."
    ),
    Operation.TEST: (
        "Run minipro's test vectors for a supported logic or RAM IC. "
        "Select the exact test device from the catalog first. RAM contents may change."
    ),
}


@dataclass(frozen=True)
class TuningOption:
    """A device-provided allowlist for one minipro ``-o`` control."""

    name: str
    values: tuple[str, ...] = ()
    minimum: int | None = None
    maximum: int | None = None


@dataclass(frozen=True)
class AdvancedOptions:
    """Optional native minipro controls, with safe defaults matching the CLI."""

    read_format: str = "bin"
    erase_mode: str = "default"
    verify_after_write: bool = True
    unprotect_before_write: bool = False
    protect_after_write: bool = False
    check_pins: bool = False
    skip_id: bool = False
    size_policy: str = "exact"
    config_sections: tuple[str, ...] = ()
    tuning: tuple[tuple[str, str], ...] = ()

    def validate(self, job: Job, info: DeviceInfo) -> None:
        if self.read_format not in {"bin", "ihex", "srec"}:
            raise ValueError("Unknown read format.")
        if self.erase_mode not in {"default", "skip", "force"}:
            raise ValueError("Unknown erase mode.")
        if self.size_policy not in {"exact", "warn", "silent"}:
            raise ValueError("Unknown size policy.")

        if self.read_format != "bin" and (
            job.operation != Operation.READ
            or job.memory not in {"code", "data", "user"}
        ):
            raise ValueError(
                "Encoded output is available only for code, data, or user reads."
            )
        if self.read_format != "bin" and not info.supports_read_format:
            raise ValueError("Encoded output is unavailable for this device.")
        if self.erase_mode != "default" and job.operation != Operation.WRITE:
            raise ValueError("Erase mode is available only when programming.")
        if (
            self.erase_mode != "default"
            and Operation.ERASE not in info.available_operations
        ):
            raise ValueError("This device does not support the requested erase mode.")
        if not self.verify_after_write and job.operation != Operation.WRITE:
            raise ValueError("Verify-after-write is available only when programming.")
        if self.unprotect_before_write and job.operation != Operation.WRITE:
            raise ValueError(
                "Unprotect-before-write is available only when programming."
            )
        if self.protect_after_write and job.operation != Operation.WRITE:
            raise ValueError("Protect-after-write is available only when programming.")
        if self.unprotect_before_write and not info.supports_unprotect:
            raise ValueError("This device does not support unprotect-before-write.")
        if self.protect_after_write and not info.supports_protect:
            raise ValueError("This device does not support protect-after-write.")
        if self.check_pins and (job.interface != "zif" or not info.supports_pin_check):
            raise ValueError("Pin checking is available only on supported ZIF jobs.")
        if self.skip_id and job.operation != Operation.READ:
            raise ValueError("Skipping the ID check is available only when reading.")
        if self.skip_id and Operation.ID not in info.available_operations:
            raise ValueError("This device does not support skipping its ID check.")
        if self.size_policy != "exact" and job.operation not in (
            Operation.WRITE,
            Operation.VERIFY,
        ):
            raise ValueError(
                "Size policy is available only when programming or verifying."
            )
        if self.size_policy != "exact" and job.memory == "config":
            raise ValueError("Size policy is unavailable for config memory.")
        if self.size_policy != "exact" and not info.supports_size_override:
            raise ValueError("This device does not support size policy overrides.")

        valid_sections = {"fuses", "uid", "lock"}
        if any(section not in valid_sections for section in self.config_sections):
            raise ValueError("Unknown configuration section.")
        if len(set(self.config_sections)) != len(self.config_sections):
            raise ValueError("Configuration sections may not be repeated.")
        if any(
            section not in info.supported_config_sections
            for section in self.config_sections
        ):
            raise ValueError(
                "This device does not support the requested config section."
            )
        if self.config_sections and (
            job.memory != "config"
            or job.operation
            not in (
                Operation.READ,
                Operation.WRITE,
                Operation.VERIFY,
                Operation.BLANK,
            )
        ):
            raise ValueError("Configuration sections require a config operation.")

        available = {option.name: option for option in info.tuning_options}
        allowed_names = {"vpp", "vdd", "vcc", "pulse", "speed"}
        names: set[str] = set()
        for item in self.tuning:
            if not isinstance(item, tuple) or len(item) != 2:
                raise ValueError("Tuning controls must be name/value pairs.")
            name, value = item
            if (
                not isinstance(name, str)
                or not isinstance(value, str)
                or name not in allowed_names
                or re.fullmatch(r"[A-Za-z0-9_.+-]+", value) is None
            ):
                raise ValueError("Tuning controls contain an unsafe name or value.")
            if name in names:
                raise ValueError("Tuning controls may not be repeated.")
            names.add(name)
            option = available.get(name)
            if option is None:
                raise ValueError(f"Unsupported tuning control: {name}.")
            if not option.values and option.minimum is None and option.maximum is None:
                raise ValueError(f"Unsupported tuning control: {name}.")
            if name in {"vpp", "vdd", "pulse"} and job.operation != Operation.WRITE:
                raise ValueError(
                    f"Tuning control {name} is available only when programming."
                )
            if name == "vcc" and job.operation not in (Operation.WRITE, Operation.TEST):
                raise ValueError(
                    "Tuning control vcc is available only when programming or testing."
                )
            if name == "speed" and (
                job.operation in (Operation.ID, Operation.ERASE, Operation.TEST)
                or job.memory == "config"
            ):
                raise ValueError(
                    "Tuning control speed is unavailable for this operation or memory."
                )
            if option.values:
                if value not in option.values:
                    raise ValueError(f"Unsupported value for tuning control: {name}.")
            elif option.minimum is not None or option.maximum is not None:
                try:
                    numeric = int(value, 10)
                except ValueError as error:
                    raise ValueError(
                        f"Tuning value for {name} must be a finite integer."
                    ) from error
                if option.minimum is not None and numeric < option.minimum:
                    raise ValueError(f"Tuning value for {name} is below its minimum.")
                if option.maximum is not None and numeric > option.maximum:
                    raise ValueError(f"Tuning value for {name} is above its maximum.")


@dataclass(frozen=True)
class DeviceInfo:
    """Preserve upstream details; parse only fields whose meaning is unambiguous."""

    name: str
    details: str
    code_bytes: int | None = None
    logic: bool = False
    operations: tuple[Operation, ...] | None = None
    regions: tuple[str, ...] | None = None
    tuning_options: tuple[TuningOption, ...] = ()
    supports_pin_check: bool = False
    supports_unprotect: bool = False
    supports_protect: bool = False
    supported_interfaces: tuple[str, ...] = ("zif",)
    supported_config_sections: tuple[str, ...] = ()
    supports_read_format: bool = False
    supports_size_override: bool = False

    @property
    def available_operations(self) -> tuple[Operation, ...]:
        if self.operations is not None:
            return self.operations
        return (Operation.TEST,) if self.logic else ()

    def available_regions(self, operation: Operation) -> tuple[str, ...]:
        if operation in (Operation.ID, Operation.ERASE, Operation.TEST):
            return ()
        if self.regions is None:
            return ()
        regions = self.regions
        return tuple(
            region
            for region in regions
            if region != "calibration" or operation == Operation.READ
        )


@dataclass(frozen=True)
class Job:
    operation: Operation
    device: str
    path: Path | None = None
    memory: str = "code"
    interface: str = "zif"
    replacement: FileStamp | None = None
    continue_on_id_mismatch: bool = False
    advanced: AdvancedOptions = field(default_factory=AdvancedOptions)

    def validate(self, info: DeviceInfo) -> None:
        if not self.device or self.device != info.name:
            raise ValueError("Choose a device from the minipro catalog first.")
        if self.memory not in {"code", "data", "config", "user", "calibration"}:
            raise ValueError("Unknown memory region.")
        if self.interface not in {"zif", "icsp", "external"}:
            raise ValueError("Unknown connection interface.")
        if self.interface not in info.supported_interfaces:
            raise ValueError("This connection interface is unavailable for the device.")
        if self.memory == "calibration" and self.operation == Operation.WRITE:
            raise ValueError("Calibration memory is read-only.")
        if info.logic and self.operation != Operation.TEST:
            raise ValueError("This is a logic test device. Choose Test logic / RAM.")
        if (
            info.operations is not None
            and self.operation not in info.available_operations
        ):
            raise ValueError("This operation is unavailable for the selected device.")
        if (
            info.regions is not None
            and self.operation not in (Operation.ID, Operation.ERASE, Operation.TEST)
            and self.memory not in info.available_regions(self.operation)
        ):
            raise ValueError(
                "This memory region is unavailable for the selected device."
            )
        capabilities_known = info.logic or (
            info.operations is not None and info.regions is not None
        )
        self.advanced.validate(self, info)
        if not self.operation.needs_file:
            if not capabilities_known:
                raise ValueError(
                    "Device capability metadata is unavailable; refusing to run "
                    "an unverified operation."
                )
            return
        if self.path is None:
            raise ValueError("Choose a file first.")
        if self.operation == Operation.READ:
            if self.replacement is not None:
                self.replacement.check(self.path)
            elif self.path.exists() or self.path.is_symlink():
                raise ValueError(
                    "That file already exists. Use Browse to confirm replacement."
                )
            if not self.path.parent.is_dir():
                raise ValueError("The destination directory does not exist.")
        elif not self.path.is_file():
            raise ValueError("The input file does not exist or is not a regular file.")
        elif self.path.stat().st_size == 0:
            raise ValueError("The input file is empty.")
        if not capabilities_known:
            raise ValueError(
                "Device capability metadata is unavailable; refusing to run "
                "an unverified operation."
            )
