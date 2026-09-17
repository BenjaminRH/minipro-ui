"""Native minipro advanced options stay allowlisted and reviewable."""

import pytest

from minipro_ui.backend import CliAdapter
from minipro_ui.demo import DemoBackend
from minipro_ui.models import (
    AdvancedOptions,
    DeviceInfo,
    Job,
    Operation,
    TuningOption,
)
from minipro_ui.workflow import Workflow


def info() -> DeviceInfo:
    return DeviceInfo(
        "chip",
        "metadata",
        code_bytes=32768,
        operations=tuple(Operation),
        regions=("code", "data", "config", "user"),
        tuning_options=(
            TuningOption("pulse", minimum=0, maximum=65535),
            TuningOption("speed", values=("3", "7.5", "15", "30")),
        ),
        supports_pin_check=True,
        supports_unprotect=True,
        supports_protect=True,
        supported_config_sections=("fuses", "uid", "lock"),
        supports_read_format=True,
        supports_size_override=True,
    )


def test_default_command_is_unchanged_and_advanced_flags_are_native(tmp_path):
    plain = Job(Operation.ID, "chip")
    assert CliAdapter.arguments(plain) == ["-p", "chip", "-D"]

    image = tmp_path / "image.bin"
    options = AdvancedOptions(
        erase_mode="force",
        verify_after_write=False,
        unprotect_before_write=True,
        protect_after_write=True,
        check_pins=True,
        size_policy="silent",
        tuning=(("pulse", "100"),),
    )
    arguments = CliAdapter.arguments(
        Job(Operation.WRITE, "chip", image, advanced=options)
    )
    assert arguments == [
        "-p",
        "chip",
        "-c",
        "code",
        "-w",
        str(image),
        "-E",
        "-v",
        "-u",
        "-P",
        "-z",
        "-S",
        "-o",
        "pulse=100",
    ]


@pytest.mark.parametrize(
    "job, message",
    [
        (
            Job(
                Operation.WRITE,
                "chip",
                advanced=AdvancedOptions(read_format="ihex"),
            ),
            "Encoded output",
        ),
        (
            Job(
                Operation.READ,
                "chip",
                advanced=AdvancedOptions(skip_id=True),
            ),
            "Choose a file",
        ),
        (
            Job(
                Operation.ID,
                "chip",
                advanced=AdvancedOptions(config_sections=("fuses",)),
            ),
            "config operation",
        ),
        (
            Job(
                Operation.WRITE,
                "chip",
                interface="external",
                advanced=AdvancedOptions(check_pins=True),
            ),
            "interface is unavailable",
        ),
        (
            Job(
                Operation.ID,
                "chip",
                advanced=AdvancedOptions(tuning=(("speed", "3"),)),
            ),
            "operation or memory",
        ),
    ],
)
def test_invalid_advanced_combinations_are_rejected(job, message):
    with pytest.raises(ValueError, match=message):
        job.validate(info())


@pytest.mark.parametrize(
    "options, message",
    [
        (AdvancedOptions(tuning=(("unknown", "1"),)), "unsafe name"),
        (AdvancedOptions(tuning=(("pulse", "nan"),)), "finite integer"),
        (AdvancedOptions(tuning=(("pulse", "70000"),)), "above its maximum"),
        (AdvancedOptions(tuning=(("speed", "8"),)), "Unsupported value"),
        (AdvancedOptions(tuning=(("pulse", "1"), ("pulse", "2"))), "repeated"),
    ],
)
def test_tunings_require_metadata_allowlist_and_finite_values(options, message):
    with pytest.raises(ValueError, match=message):
        Job(Operation.WRITE, "chip", advanced=options).validate(info())


@pytest.mark.parametrize("read_format, prefix", [("ihex", ":"), ("srec", "S")])
async def test_demo_encoded_reads_are_published_as_encoded_files(
    tmp_path, read_format, prefix
):
    destination = tmp_path / "backup.bin"
    flow = Workflow(DemoBackend())
    prepared = await flow.prepare(
        "T48",
        Job(
            Operation.READ,
            "AT28C256@DIP28",
            destination,
            advanced=AdvancedOptions(read_format=read_format),
        ),
    )
    await flow.execute(prepared, lambda _: None)
    lines = destination.read_text().splitlines()
    assert lines[0].startswith(prefix)
    assert len(lines) == 2049
    assert destination.read_bytes() != b"\xff" * 32768
    payload = bytearray()
    for line in lines[:-1]:
        if read_format == "ihex":
            record = bytes.fromhex(line[1:])
            assert sum(record) & 0xFF == 0
            payload.extend(record[4:-1])
        else:
            record = bytes.fromhex(line[2:])
            assert record[0] == len(record) - 1
            assert sum(record) & 0xFF == 0xFF
            payload.extend(record[3:-1])
    assert payload == b"\xff" * 32768


async def test_encoded_read_failure_still_cleans_staging(
    adapter, tmp_path, monkeypatch
):
    destination = tmp_path / "backup.hex"
    flow = Workflow(adapter)
    prepared = await flow.prepare(
        "T48",
        Job(
            Operation.READ,
            "AT28C256@DIP28",
            destination,
            advanced=AdvancedOptions(read_format="ihex"),
        ),
    )
    staging = prepared.execution_job.path.parent
    await adapter.version()
    monkeypatch.setenv("MINIPRO_TEST_MODE", "failure")

    async def connected():
        return ["T48"]

    monkeypatch.setattr(adapter, "connected", connected)
    result = await flow.execute(prepared, lambda _: None)
    assert result.returncode == 7
    assert not destination.exists() and not staging.exists()


async def test_retry_preserves_advanced_choices(adapter, tmp_path, monkeypatch):
    destination = tmp_path / "backup.hex"
    options = AdvancedOptions(read_format="ihex", skip_id=True)
    monkeypatch.setenv("MINIPRO_TEST_MODE", "id_mismatch")
    flow = Workflow(adapter)
    prepared = await flow.prepare(
        "T48",
        Job(Operation.READ, "AT28C256@DIP28", destination, advanced=options),
    )
    refused = await flow.execute(prepared, lambda _: None)
    retry = await flow.prepare_retry(prepared, refused)
    assert retry.job.advanced == options
    assert "-f" in retry.command and "ihex" in retry.command
    assert "-x" in retry.command and "-y" in retry.command
    await flow.execute(retry, lambda _: None)


@pytest.mark.parametrize("policy", ["warn", "silent"])
async def test_nonexact_size_policy_allows_binary_prepare(adapter, tmp_path, policy):
    image = tmp_path / "short.bin"
    image.write_bytes(b"short")
    prepared = await Workflow(adapter).prepare(
        "T48",
        Job(
            Operation.WRITE,
            "AT28C256@DIP28",
            image,
            advanced=AdvancedOptions(size_policy=policy),
        ),
    )
    prepared.close()


def test_tuning_option_is_frozen():
    option = TuningOption("pulse", minimum=0, maximum=1)
    with pytest.raises(AttributeError):
        option.name = "speed"


async def test_demo_capabilities_are_programmer_and_device_specific():
    backend = DemoBackend()
    at28 = await backend.device_info("T48", "AT28C256@DIP28")
    w25 = await backend.device_info("T48", "W25Q32JV@SOIC8")
    w25_tl866 = await backend.device_info("TL866II", "W25Q32JV@SOIC8")
    logic = await backend.device_info("T48", "7400")
    atmega = await backend.device_info("T48", "ATMEGA328P@DIP28")

    assert at28.supported_interfaces == ("zif",)
    assert [option.name for option in w25.tuning_options] == ["speed"]
    assert [option.name for option in w25_tl866.tuning_options] == []
    assert logic.supported_interfaces == ("zif",)
    assert [option.name for option in logic.tuning_options] == ["vcc"]
    assert atmega.supported_interfaces == ("zif", "icsp", "external")
