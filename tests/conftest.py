"""Shared test targets and an honest compatibility report for candidate binaries."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

from minipro_ui.backend import CliAdapter

REPORTS: list[dict[str, object]] = []


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("minipro compatibility")
    group.addoption(
        "--minipro-bin",
        action="append",
        default=[],
        help="Candidate executable; repeat to compare versions",
    )
    group.addoption(
        "--programmer", default="T48", help="Catalog model for executable tests"
    )
    group.addoption("--hardware-profile", help="Explicit JSON hardware scenarios")
    group.addoption(
        "--allow-destructive",
        action="store_true",
        help="Permit profile cases that write, erase or test RAM",
    )
    group.addoption(
        "--compat-report", help="Write machine-readable coverage outcomes to JSON"
    )


def pytest_configure(config: pytest.Config) -> None:
    REPORTS.clear()
    if (
        config.getoption("--hardware-profile")
        and len(config.getoption("--minipro-bin")) != 1
    ):
        raise pytest.UsageError("Hardware tests require exactly one --minipro-bin.")
    for marker in (
        "executable: runs an installed executable without chip operations",
        "hardware: requires a configured programmer and chip",
        "destructive: may modify chip contents",
    ):
        config.addinivalue_line("markers", marker)


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    if "candidate_path" in metafunc.fixturenames:
        targets = metafunc.config.getoption("--minipro-bin")
        metafunc.parametrize(
            "candidate_path", targets or [None], ids=targets or ["not-configured"]
        )


@pytest.fixture
def fake_executable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Use the actual process runner, not a mock of its return value."""
    destination = tmp_path / "minipro fake"
    script = Path(__file__).parent / "fixtures" / "minipro_fake.py"
    destination.write_text(
        f"#!{sys.executable}\n" + script.read_text().split("\n", 1)[1]
    )
    destination.chmod(0o755)
    (tmp_path / "infoic.xml").write_text(
        """<infoic>
          <database type="INFOIC2PLUS">
            <ic name="AT28C256@DIP28" type="1" protocol_id="0x07"
                read_buffer_size="0x400" code_memory_size="0x8000"
                write_buffer_size="0x80"
                chip_info="0x0006" flags="0xC030" pin_map="1" config="NULL" />
            <ic name="W25Q32JV@SOIC8" type="1" protocol_id="0x03"
                read_buffer_size="0x1000" code_memory_size="0x400000"
                write_buffer_size="0x100"
                chip_info="0x0006" flags="0xC030" pin_map="1" config="NULL" />
            <ic name="82HS641B" type="1" protocol_id="0x07"
                read_buffer_size="0x100" code_memory_size="0x8000"
                write_buffer_size="0x80"
                chip_info="0x0006" flags="0xC030" pin_map="1" config="NULL" />
            <ic name="7400" type="5" flags="0" read_buffer_size="0" />
          </database>
        </infoic>"""
    )
    monkeypatch.setenv("MINIPRO_HOME", str(tmp_path))
    return destination


@pytest.fixture
def adapter(fake_executable: Path) -> CliAdapter:
    return CliAdapter(fake_executable)


@pytest.fixture
def candidate(candidate_path: str | None) -> CliAdapter:
    if candidate_path is None:
        pytest.skip("UNTESTED: no --minipro-bin supplied")
    path = Path(candidate_path).expanduser().resolve()
    if not path.is_file():
        pytest.fail(f"Candidate executable not found: {path}")
    return CliAdapter(path, allow_unverified=True)


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    if report.when == "call" or (
        report.when in ("setup", "teardown") and report.outcome != "passed"
    ):
        detail = str(report.longrepr) if report.longrepr else ""
        status = report.outcome.upper()
        if report.skipped:
            status = "UNSUPPORTED" if "UNSUPPORTED:" in detail else "UNTESTED"
        REPORTS.append(
            {
                "test": report.nodeid,
                "status": status,
                "detail": detail,
                "properties": dict(report.user_properties),
                "evidence": "hardware"
                if "test_hardware.py" in report.nodeid
                else "executable"
                if "test_executable.py" in report.nodeid
                else "software",
            }
        )


def pytest_terminal_summary(
    terminalreporter: object, exitstatus: int, config: pytest.Config
) -> None:
    # Pytest's terminal reporter is intentionally kept out of runtime dependencies.
    from collections import Counter

    counts = Counter(str(item["status"]) for item in REPORTS)
    terminalreporter.write_sep("=", "compatibility coverage")
    terminalreporter.write_line(
        " · ".join(f"{key}: {value}" for key, value in sorted(counts.items()))
    )
    terminalreporter.write_line(
        "Simulated tests are software coverage, not hardware certification."
    )
    if destination := config.getoption("--compat-report"):
        binaries = []
        for value in config.getoption("--minipro-bin"):
            path = Path(value).expanduser().resolve()
            binaries.append(
                {
                    "path": str(path),
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest()
                    if path.is_file()
                    else None,
                }
            )
        Path(destination).write_text(
            json.dumps({"binaries": binaries, "tests": REPORTS}, indent=2) + "\n"
        )
