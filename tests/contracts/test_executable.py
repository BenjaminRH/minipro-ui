"""Run unchanged against each candidate binary: no chip reads or writes."""

import pytest

from minipro_ui.backend import BackendError

pytestmark = pytest.mark.executable


async def test_version(candidate, record_property):
    version = await candidate.version()
    record_property("minipro_version", version.release)
    record_property("minipro_revision", version.revision)
    assert version.release
    assert "minipro" in version.details.lower()


async def test_catalog_and_metadata(candidate, pytestconfig):
    programmer = pytestconfig.getoption("--programmer")
    models = await candidate.programmers()
    assert models
    if programmer not in models:
        pytest.skip(f"UNSUPPORTED: executable does not advertise {programmer}")
    names = await candidate.devices(programmer)
    assert len(names) > 10
    assert len(names) == len(set(names))
    # Sample across the catalog instead of assuming one vendor's part is present.
    for name in (names[0], names[len(names) // 2], names[-1]):
        info = await candidate.device_info(programmer, name)
        assert info.name.casefold() == name.casefold()
        assert info.details.strip()
        assert info.logic or info.operations is not None, (
            f"Capability metadata is unavailable for {name}; check infoic.xml "
            "discovery and the database dialect for this minipro version."
        )


async def test_invalid_chip_reports_error(candidate, pytestconfig):
    with pytest.raises(BackendError):
        await candidate.device_info(
            pytestconfig.getoption("--programmer"), "MINIPRO_UI_NONEXISTENT_DEVICE_123"
        )


async def test_programmer_presence_output(candidate):
    models = await candidate.connected()
    advertised = await candidate.programmers()
    assert all(model in advertised for model in models)
