"""Decode minipro 0.7.4 device flags without issuing hardware commands.

The masks and database groups follow upstream src/database.c. This module is
part of the CLI adapter: future database dialects can replace it independently.
"""

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from xml.etree import ElementTree

from .models import Operation, TuningOption

Capabilities = tuple[tuple[Operation, ...], tuple[str, ...]]


@dataclass(frozen=True)
class AdvancedCapabilities:
    """Native ``-o`` controls and protection features for one device.

    Values are the allowlists accepted by minipro 0.7.4.  An empty result is
    intentional when the database does not provide enough information to
    safely expose a control.
    """

    tuning_options: tuple[TuningOption, ...] = ()
    supports_pin_check: bool = False
    supports_unprotect: bool = False
    supports_protect: bool = False
    supported_interfaces: tuple[str, ...] = ()
    supported_config_sections: tuple[str, ...] = ()
    supports_read_format: bool = False
    supports_size_override: bool = False


# These tables mirror the voltage tables in minipro's 0.7.4 main.c.  Keep the
# spelling/order used by the CLI so values can be passed through unchanged.
_TL866A_VPP = ("10", "12.5", "13.5", "14", "16", "17", "18", "21")
_TL866A_VCC = ("3.3", "4", "4.5", "5", "5.5", "6.5")
_TL866II_VPP = (
    "9",
    "9.5",
    "10",
    "11",
    "11.5",
    "12",
    "12.5",
    "13",
    "13.5",
    "14",
    "14.5",
    "15.5",
    "16",
    "16.5",
    "17",
    "18",
)
_TL866II_VCC = _TL866A_VCC
_T48_VPP = _TL866II_VPP + ("21", "25")
_T48_VCC = _TL866II_VCC
_LOGIC_VCC = ("5", "3.3", "2.5", "1.8")
_T48_SPI_SPEED = ("3", "7.5", "15", "30")
_T48_CUSTOM_VPP = (
    "9",
    "9.5",
    "10",
    "11",
    "11.5",
    "12",
    "12.5",
    "13",
    "13.5",
    "14",
    "14.5",
    "15.5",
    "16",
    "16.5",
    "17",
    "18",
    "21",
    "25",
)
_T48_CUSTOM_VCC = (
    "1.75",
    "1.8",
    "1.9",
    "2",
    "2.1",
    "2.2",
    "2.3",
    "2.4",
    "2.5",
    "2.6",
    "2.7",
    "2.8",
    "2.9",
    "3",
    "3.3",
    "3.5",
    "3.7",
    "3.8",
    "4",
    "4.2",
    "4.3",
    "4.4",
    "4.5",
    "4.7",
    "4.8",
    "4.9",
    "5",
    "5.2",
    "5.3",
    "5.4",
    "5.5",
    "5.6",
    "5.7",
    "5.8",
    "5.9",
    "6",
    "6.1",
    "6.2",
    "6.3",
    "6.5",
    "6.6",
    "6.7",
    "6.8",
    "6.9",
)

# 0x03 is SPI25F, 0x04 is AT45D, and 0x0f is the second SPI25F family in
# minipro's protocol table.  T48's speed setting is meaningful for these
# protocols; other protocol IDs must not acquire an inferred speed control.
_SPI_PROTOCOLS = frozenset({0x03, 0x04, 0x0F})
_KNOWN_PROGRAMMERS = frozenset(
    {"tl866a", "tl866cs", "tl866ii", "tl866ii+", "t48", "t56"}
)


def decode_device(
    attributes: dict[str, str],
    programmer: str | None = None,
) -> tuple[tuple[Operation, ...], tuple[str, ...]]:
    """Translate a database record into supported operations and memory regions."""

    def number(key: str) -> int:
        return int(attributes.get(key, "0"), 0)

    if programmer and programmer.casefold() not in _KNOWN_PROGRAMMERS:
        return (), ()
    if programmer and not _matches_programmer(attributes, programmer):
        return (), ()
    kind = number("type")
    if kind == 5:
        return (Operation.TEST,), ()
    if kind == 6 or (kind != 3 and not number("read_buffer_size")):
        return (), ()
    flags = number("flags")
    operations = [Operation.READ, Operation.WRITE, Operation.VERIFY, Operation.BLANK]
    # A zero write buffer denotes a read-only memory record. PLD/JEDEC records
    # are the exception: their write path uses fuse rows instead of buffers.
    if kind != 3 and not number("write_buffer_size"):
        operations.remove(Operation.WRITE)
    # Upstream marks custom PROM protocols read-only after decoding flags.
    custom_prom = number("protocol_id") == 0x80000001
    if flags & 0x10:
        operations.append(Operation.ERASE)
    if flags & 0x20:
        operations.append(Operation.ID)
    if custom_prom:
        for operation in (Operation.WRITE, Operation.ERASE):
            if operation in operations:
                operations.remove(operation)
    if ((flags & 0x00300000) >> 20) == 3:
        for operation in (Operation.WRITE, Operation.ERASE):
            if operation in operations:
                operations.remove(operation)
    regions = ["code"]
    if number("data_memory_size"):
        regions.append("data")
    if attributes.get("config", "NULL").upper() != "NULL":
        regions.append("config")
    if number("data_memory2_size"):
        regions.append("user")
    if flags & 0x80000:
        regions.append("calibration")
    return tuple(operations), tuple(regions)


@lru_cache(maxsize=4)
def database_records(
    path: Path, modified: int, size: int
) -> dict[tuple[str, str], dict[str, str]]:
    """Cache raw records by file identity for all capability projections."""
    records = {}
    group = ""
    for event, element in ElementTree.iterparse(path, events=("start", "end")):
        if event == "start" and element.tag == "database":
            group = element.attrib.get("type", "")
        elif event == "end":
            if element.tag == "ic" and "flags" in element.attrib:
                attributes = dict(element.attrib)
                for name in element.attrib.get("name", "").split(","):
                    records[(group, name.casefold())] = attributes
            element.clear()
    return records


@lru_cache(maxsize=8)
def database_programmer_projection(
    path: Path, modified: int, size: int, group: str, programmer: str
) -> dict[str, bool]:
    """Cache the known name-to-programmer compatibility projection.

    Names absent from this projection are deliberately handled by the caller
    as unknown.  That keeps logic devices, custom entries, and databases that
    cannot be found from being hidden by a partial metadata install.
    """

    return {
        name: _matches_programmer(attributes, programmer)
        for (record_group, name), attributes in database_records(
            path, modified, size
        ).items()
        if record_group == group
    }


def filter_supported_devices(
    executable: Path, programmer: str, names: list[str]
) -> list[str]:
    """Remove only metadata-known devices incompatible with ``programmer``.

    The native catalog remains authoritative for unknown names.  In particular,
    this preserves custom and logic entries while filtering stale cross-family
    rows emitted by some INFOIC2PLUS ``-l`` catalogs.
    """

    if programmer.casefold() not in _KNOWN_PROGRAMMERS:
        return names
    resolved = _database_path(executable, programmer, "")
    if resolved is None:
        return names
    path, group = resolved
    try:
        stat = path.stat()
        projection = database_programmer_projection(
            path, stat.st_mtime_ns, stat.st_size, group, programmer
        )
    except (OSError, ValueError, ElementTree.ParseError):
        return names
    filtered: list[str] = []
    seen: set[str] = set()
    for name in names:
        key = name.casefold()
        compatible = projection.get(key)
        if compatible is False or key in seen:
            continue
        seen.add(key)
        filtered.append(name)
    return filtered


@lru_cache(maxsize=4)
def configuration_sections(
    path: Path, modified: int, size: int
) -> dict[str, tuple[str, ...]]:
    """Read the optional fuse/UID/lock sections referenced by infoic records."""
    profiles: dict[str, list[str]] = {}
    current: str | None = None
    for event, element in ElementTree.iterparse(path, events=("start", "end")):
        if event == "start":
            if element.tag == "config":
                current = element.attrib.get("name")
                if current:
                    profiles[current] = []
                    try:
                        if int(element.attrib.get("num_uids", "0"), 0) > 0:
                            profiles[current].append("uid")
                    except ValueError:
                        pass
            elif current and element.tag == "fuses":
                try:
                    count = int(element.attrib.get("count", "0"), 0)
                except ValueError:
                    count = 0
                if count > 0 and "fuses" not in profiles[current]:
                    profiles[current].append("fuses")
            elif current and element.tag == "locks":
                try:
                    count = int(element.attrib.get("count", "0"), 0)
                except ValueError:
                    count = 0
                if count > 0 and "lock" not in profiles[current]:
                    profiles[current].append("lock")
        elif element.tag == "config":
            current = None
        element.clear()
    order = ("fuses", "uid", "lock")
    return {
        name: tuple(section for section in order if section in sections)
        for name, sections in profiles.items()
    }


@lru_cache(maxsize=4)
def database_index(
    path: Path, modified: int, size: int
) -> dict[tuple[str, str], Capabilities]:
    """Cache decoded operation/region records by file identity."""
    return {
        key: decode_device(attributes)
        for key, attributes in database_records(path, modified, size).items()
    }


def _database_path(
    executable: Path, programmer: str, name: str
) -> tuple[Path, str] | None:
    """Resolve the same database/group pair used by ``device_capabilities``."""
    if programmer.casefold() not in _KNOWN_PROGRAMMERS:
        return None
    roots = [Path.cwd()]
    if directory := os.environ.get("MINIPRO_HOME"):
        roots.append(Path(directory))
    else:
        roots.extend(
            [
                executable.parent.parent / "share" / "minipro",
                Path("/usr/local/share/minipro"),
                Path("/usr/share/minipro"),
                Path("/opt/homebrew/share/minipro"),
            ]
        )
    group = (
        "INFOIC" if programmer.casefold() in ("tl866a", "tl866cs") else "INFOIC2PLUS"
    )
    for root in roots:
        path = root / "infoic.xml"
        if not path.is_file():
            continue
        try:
            stat = path.stat()
            # Validate the file through the cached parser here.  This keeps
            # malformed/unreadable metadata fail-closed for both projections.
            database_records(path, stat.st_mtime_ns, stat.st_size)
            return path, group
        except (OSError, ValueError, ElementTree.ParseError):
            return None
    return None


def _programmer_tables(
    programmer: str, custom: bool
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    model = programmer.casefold()
    if model in ("tl866a", "tl866cs"):
        return _TL866A_VPP, _TL866A_VCC
    if model in ("tl866ii", "tl866ii+"):
        return _TL866II_VPP, _TL866II_VCC
    if model == "t48":
        return (_T48_CUSTOM_VPP, _T48_CUSTOM_VCC) if custom else (_T48_VPP, _T48_VCC)
    if model == "t56":
        return _T48_VPP, _T48_VCC
    return (), ()


def _matches_programmer(attributes: dict[str, str], programmer: str) -> bool:
    """Honor family-only bits stored in INFOIC2PLUS ``pin_map``."""
    try:
        pin_map = int(attributes.get("pin_map", "0"), 0)
    except ValueError:
        pin_map = 0
    family_bits = pin_map & 0x70000000
    if not family_bits:
        return True
    family_bit = {
        "tl866ii": 0x20000000,
        "tl866ii+": 0x20000000,
        "t48": 0x40000000,
        "t56": 0x10000000,
    }.get(programmer.casefold())
    return family_bit is not None and bool(family_bits & family_bit)


def advanced_capabilities(
    executable: Path, programmer: str, name: str, details: str = ""
) -> AdvancedCapabilities:
    """Return safe native controls for ``name`` without touching hardware.

    ``details`` is used to recognize logic devices because those records live
    in logicic.xml and ``-d`` identifies them with ``Vector count``.  Memory
    controls come only from the matching infoic.xml record and its masks.
    """
    if programmer.casefold() not in _KNOWN_PROGRAMMERS:
        return AdvancedCapabilities()
    if "vector count:" in details.casefold():
        return AdvancedCapabilities((TuningOption("vcc", values=_LOGIC_VCC),))

    resolved = _database_path(executable, programmer, name)
    if resolved is None:
        return AdvancedCapabilities()
    path, group = resolved
    try:
        stat = path.stat()
        attributes = database_records(path, stat.st_mtime_ns, stat.st_size).get(
            (group, name.casefold())
        )
    except (OSError, ValueError, ElementTree.ParseError):
        return AdvancedCapabilities()
    if not attributes:
        return AdvancedCapabilities()
    if not _matches_programmer(attributes, programmer):
        return AdvancedCapabilities()

    def number(key: str) -> int:
        try:
            return int(attributes.get(key, "0"), 0)
        except ValueError:
            return 0

    kind = number("type")
    if kind == 5:
        # This is useful for small fixture databases that include logic
        # records in infoic.xml, while normal 0.7.4 logic records use details.
        return AdvancedCapabilities((TuningOption("vcc", values=_LOGIC_VCC),))
    if kind == 6 or (kind != 3 and not number("read_buffer_size")):
        return AdvancedCapabilities()

    flags = number("flags")
    chip_info = number("chip_info")
    protocol = number("protocol_id")
    custom = bool(protocol & 0x80000000)
    vpp_values, vcc_values = _programmer_tables(programmer, custom)
    options: list[TuningOption] = []
    # minipro prints and accepts VPP for either adjustable-voltage class;
    # VDD/VCC/pulse are additionally available for MP_VOLTAGES1.
    if chip_info in (0x0006, 0x0007) and vpp_values:
        options.append(TuningOption("vpp", values=vpp_values))
    if chip_info == 0x0006 and vcc_values:
        options.extend(
            (
                TuningOption("vdd", values=vcc_values),
                TuningOption("vcc", values=vcc_values),
                TuningOption("pulse", minimum=0, maximum=0xFFFF),
            )
        )
    if programmer.casefold() == "t48" and protocol in _SPI_PROTOCOLS:
        options.append(TuningOption("speed", values=_T48_SPI_SPEED))
    support = (flags & 0x00300000) >> 20
    if support in (0, 3):
        interfaces: tuple[str, ...] = ("zif",)
    elif support == 1:
        interfaces = ("zif", "icsp", "external")
    elif support == 2:
        # The CLI forces ICSP with VCC for ICSP_ONLY devices, even if -I was
        # requested, so do not present the no-VCC external variant.
        interfaces = ("icsp",)
    else:
        interfaces = ()
    config_name = attributes.get("config", "").strip()
    sections: tuple[str, ...] = ()
    if config_name and config_name.casefold() != "null":
        try:
            sections = configuration_sections(path, stat.st_mtime_ns, stat.st_size).get(
                config_name, ()
            )
        except (OSError, ValueError, ElementTree.ParseError):
            sections = ()
    supports_pin_check = (
        programmer.casefold() in ("tl866ii", "tl866ii+")
        and (number("pin_map") & 0xFF) != 0
    )
    byte_memory = kind not in (3, 5, 6) and bool(number("read_buffer_size"))
    return AdvancedCapabilities(
        tuple(options),
        supports_pin_check=supports_pin_check,
        supports_unprotect=bool(flags & 0x4000),
        supports_protect=bool(flags & 0x8000),
        supported_interfaces=interfaces,
        supported_config_sections=sections,
        supports_read_format=byte_memory,
        supports_size_override=byte_memory,
    )


def device_capabilities(
    executable: Path, programmer: str, name: str
) -> Capabilities | None:
    """Find the installed database using minipro's cwd/environment precedence.

    Standard install prefixes cover packaged Linux and Homebrew installations.
    An absent or unreadable database leaves capabilities unknown; never infer
    erase or ID support from a chip's marketing name.
    """
    resolved = _database_path(executable, programmer, name)
    if resolved is None:
        return None
    path, group = resolved
    try:
        stat = path.stat()
        attributes = database_records(path, stat.st_mtime_ns, stat.st_size).get(
            (group, name.casefold())
        )
        return decode_device(attributes, programmer) if attributes else None
    except (OSError, ValueError, ElementTree.ParseError):
        return None
