"""Capability decoding for the minipro 0.7.4 database dialect."""

import pytest

from minipro_ui.capabilities import (
    decode_device,
    device_capabilities,
    filter_supported_devices,
)
from minipro_ui.models import DeviceInfo, Operation


@pytest.mark.parametrize(
    "attributes,absent,regions",
    [
        ({"flags": "0"}, {Operation.ID, Operation.ERASE, Operation.TEST}, ("code",)),
        ({"flags": "0x30"}, {Operation.TEST}, ("code",)),
        ({"type": "5"}, set(Operation) - {Operation.TEST}, ()),
        ({"type": "6"}, set(Operation), ()),
        ({"read_buffer_size": "0"}, set(Operation), ()),
        (
            {"protocol_id": "0x80000001"},
            {Operation.WRITE, Operation.ID, Operation.ERASE, Operation.TEST},
            ("code",),
        ),
        (
            {
                "flags": "0x80030",
                "data_memory_size": "16",
                "data_memory2_size": "4",
                "config": "fuses",
            },
            {Operation.TEST},
            ("code", "data", "config", "user", "calibration"),
        ),
    ],
)
def test_database_flags(attributes, absent, regions):
    record = {
        "type": "1",
        "read_buffer_size": "32",
        "write_buffer_size": "32",
        "flags": "0",
        **attributes,
    }
    operations, available = decode_device(record)
    assert set(operations) == set(Operation) - absent
    assert available == regions


def test_database_aliases_and_programmer_groups(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "infoic.xml").write_text("""<infoic>
      <database type="INFOIC">
        <ic name="chip,alias" type="1" flags="0" read_buffer_size="32" />
      </database>
      <database type="INFOIC2PLUS">
        <ic name="chip,alias" type="1" flags="0x30" read_buffer_size="32" />
      </database>
    </infoic>""")
    executable = tmp_path / "minipro"
    assert Operation.ID not in device_capabilities(executable, "TL866A", "alias")[0]
    assert Operation.ID in device_capabilities(executable, "T48", "ALIAS")[0]
    assert device_capabilities(executable, "T48", "missing") is None


def test_calibration_only_offered_for_reading():
    info = DeviceInfo("chip", "", regions=("code", "calibration"))
    assert info.available_regions(Operation.READ) == ("code", "calibration")
    assert info.available_regions(Operation.WRITE) == ("code",)
    assert info.available_regions(Operation.ID) == ()


def test_unknown_regions_are_not_inferred():
    info = DeviceInfo("chip", "")
    assert info.available_regions(Operation.READ) == ()


def test_device_programmer_family_and_write_buffer_are_gated():
    base = {
        "type": "1",
        "read_buffer_size": "32",
        "write_buffer_size": "0",
        "flags": "0",
        "pin_map": "0x20000001",
    }
    operations, regions = decode_device(base, "TL866II")
    assert Operation.WRITE not in operations
    assert regions == ("code",)
    assert decode_device(base, "T48") == ((), ())
    assert decode_device({**base, "write_buffer_size": "32"}, "TL866II")[0] != ()


def test_read_only_records_cannot_advertise_write_or_erase():
    record = {
        "type": "1",
        "read_buffer_size": "32",
        "write_buffer_size": "32",
        "protocol_id": "0x01",
        "flags": "0x300010",
    }
    operations, _ = decode_device(record)
    assert Operation.WRITE not in operations
    assert Operation.ERASE not in operations


def test_custom_prom_with_zero_write_buffer_is_safe():
    operations, _ = decode_device(
        {
            "type": "1",
            "read_buffer_size": "32",
            "write_buffer_size": "0",
            "protocol_id": "0x80000001",
            "flags": "0x10",
        }
    )
    assert Operation.WRITE not in operations
    assert Operation.ERASE not in operations


def test_family_mismatch_is_checked_before_logic_shortcut():
    assert decode_device({"type": "5", "pin_map": "0x20000001"}, "T48") == ((), ())


def test_catalog_projection_filters_cross_family_rows_but_keeps_unknown_and_logic(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "infoic.xml").write_text(
        """<infoic><database type="INFOIC2PLUS">
          <ic name="SM39R12A2" type="1" flags="0" read_buffer_size="32"
              pin_map="0x20001120" />
          <ic name="AT28C256" type="1" flags="0" read_buffer_size="32"
              pin_map="0x40001120" />
          <ic name="TL866Only,CaseAlias" type="1" flags="0" read_buffer_size="32"
              pin_map="0x20001120" />
        </database></infoic>"""
    )
    executable = tmp_path / "minipro"
    catalog = ["SM39R12A2", "AT28C256", "7400", "custom-part", "CaseAlias"]
    assert filter_supported_devices(executable, "T48", catalog) == [
        "AT28C256",
        "7400",
        "custom-part",
    ]
    assert filter_supported_devices(executable, "T56", catalog) == [
        "7400",
        "custom-part",
    ]
    assert filter_supported_devices(executable, "TL866II", catalog) == [
        "SM39R12A2",
        "7400",
        "custom-part",
        "CaseAlias",
    ]


def test_catalog_projection_preserves_native_catalog_without_local_database(tmp_path):
    names = ["SM39R12A2", "custom-part"]
    assert filter_supported_devices(tmp_path / "minipro", "T48", names) == names
