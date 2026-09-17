"""Catalog completion searches every entry and ranks exact matches first."""

from minipro_ui.catalog import match_devices


def test_subsequence_completion_and_ranking():
    names = ["AT28C256@DIP28", "AT28C64@DIP28", "W25Q32JV@SOIC8"]
    assert match_devices(names, "a28c256dp") == [names[0]]
    assert match_devices(names, "W25 SOIC") == [names[2]]
    assert match_devices(names, "absent") == []
    assert match_devices(["XAT28", "AT28C256", "AT28"], "at28")[0] == "AT28"


def test_search_includes_entries_beyond_display_limit():
    names = [f"CHIP{i:05d}" for i in range(10000)]
    assert len(match_devices(names, "")) == 300
    assert match_devices(names, "CHIP09999")[0] == "CHIP09999"
