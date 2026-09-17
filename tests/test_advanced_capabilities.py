"""Database-backed native minipro controls."""

from pathlib import Path

from minipro_ui.capabilities import advanced_capabilities


def _write_infoic(tmp_path: Path, record: str) -> Path:
    path = tmp_path / "infoic.xml"
    path.write_text(
        '<infoic><database type="INFOIC2PLUS"><ic ' + record + " /></database></infoic>"
    )
    return path


def test_memory_masks_expose_tunings_and_protection(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_infoic(
        tmp_path,
        'name="chip" type="1" read_buffer_size="32" '
        'chip_info="0x0006" protocol_id="0x03" flags="0xc000"',
    )

    result = advanced_capabilities(Path("/tmp/minipro"), "T48", "chip")

    assert [option.name for option in result.tuning_options] == [
        "vpp",
        "vdd",
        "vcc",
        "pulse",
        "speed",
    ]
    assert result.tuning_options[1].values == ("3.3", "4", "4.5", "5", "5.5", "6.5")
    assert result.tuning_options[3].minimum == 0
    assert result.tuning_options[3].maximum == 65535
    assert result.supports_unprotect
    assert result.supports_protect
    assert not result.supports_pin_check


def test_programmer_specific_vpp_and_spi_speed(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_infoic(
        tmp_path,
        'name="chip" type="1" read_buffer_size="32" '
        'chip_info="0x0007" protocol_id="0x03" flags="0"',
    )

    t48 = advanced_capabilities(Path("/tmp/minipro"), "T48", "chip")
    tl866 = advanced_capabilities(Path("/tmp/minipro"), "TL866II", "chip")
    assert t48.tuning_options[0].name == "vpp"
    assert "25" in t48.tuning_options[0].values
    assert "25" not in tl866.tuning_options[0].values
    assert all(option.name != "speed" for option in tl866.tuning_options)


def test_family_only_record_is_not_reused_by_another_programmer(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_infoic(
        tmp_path,
        'name="chip" type="1" read_buffer_size="32" write_buffer_size="32" '
        'chip_info="0x0007" protocol_id="0x03" flags="0" '
        'pin_map="0x20000001"',
    )
    assert (
        advanced_capabilities(Path("/tmp/minipro"), "T48", "chip").tuning_options == ()
    )
    assert advanced_capabilities(Path("/tmp/minipro"), "TL866II", "chip").tuning_options


def test_logic_voltage_table_is_independent_from_memory_voltage_table():
    result = advanced_capabilities(
        Path("/tmp/minipro"), "TL866II+", "7400", "Name: 7400\nVector count: 8\n"
    )
    assert len(result.tuning_options) == 1
    assert result.tuning_options[0].name == "vcc"
    assert result.tuning_options[0].values == ("5", "3.3", "2.5", "1.8")
    assert not result.supports_pin_check


def test_unknown_programmer_cannot_enable_logic_voltage_override():
    result = advanced_capabilities(
        Path("/tmp/minipro"), "unknown", "7400", "Vector count: 8\n"
    )
    assert result.tuning_options == ()


def test_unknown_or_non_spi_metadata_fails_closed(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _write_infoic(
        tmp_path,
        'name="chip" type="1" read_buffer_size="32" '
        'chip_info="0x0006" protocol_id="0x01" flags="0"',
    )
    result = advanced_capabilities(Path("/tmp/minipro"), "T48", "chip")
    assert [option.name for option in result.tuning_options] == [
        "vpp",
        "vdd",
        "vcc",
        "pulse",
    ]

    missing = advanced_capabilities(Path("/tmp/minipro"), "T48", "missing")
    assert missing.tuning_options == ()
    assert not missing.supports_unprotect
    assert not missing.supports_protect


def test_config_profile_and_interface_masks_are_device_specific(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "infoic.xml").write_text(
        """<infoic>
          <database type="INFOIC2PLUS">
            <ic name="chip" type="1" read_buffer_size="32"
               write_buffer_size="32" chip_info="0" protocol_id="1"
               flags="0x00200000" config="profile" />
          </database>
          <configurations>
            <config name="profile" num_uids="2">
              <fuses count="1" /><locks count="1" />
            </config>
          </configurations>
        </infoic>"""
    )
    result = advanced_capabilities(Path("/tmp/minipro"), "TL866II", "chip")
    assert result.supported_interfaces == ("icsp",)
    assert result.supported_config_sections == ("fuses", "uid", "lock")


def test_empty_config_sections_are_not_advertised(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "infoic.xml").write_text(
        """<infoic>
          <database type="INFOIC2PLUS">
            <ic name="chip" type="1" read_buffer_size="32"
               write_buffer_size="32" chip_info="0" protocol_id="1"
               flags="0" config="empty" />
          </database>
          <configurations>
            <config name="empty" num_uids="0">
              <fuses count="0" /><locks count="bad" />
            </config>
          </configurations>
        </infoic>"""
    )
    result = advanced_capabilities(Path("/tmp/minipro"), "T48", "chip")
    assert result.supported_config_sections == ()
