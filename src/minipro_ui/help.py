"""Task-focused help, with short explanations separate from keyboard reference."""

from dataclasses import dataclass


@dataclass(frozen=True)
class HelpTopic:
    """A screen title and a sequence of labeled, independently readable tips."""

    title: str
    sections: tuple[tuple[str, str], ...]


SHORTCUTS = (
    ("Ctrl+? / Ctrl+/", "Open or close help"),
    ("Ctrl+p", "Find a command by name or description"),
    ("Alt / Alt+space", "Numbered focus hints (outside Help)"),
    ("Ctrl+q", "Quit; stop a running operation first"),
    ("Tab / Shift+Tab", "Move to the next / previous control"),
    ("Arrow keys", "Navigate the focused control"),
    ("Ctrl+k / j / h / l", "Arrow aliases: up / down / left / right"),
    ("Enter / Space", "Activate a button or choose an option"),
    ("Escape", "Close this dialog"),
)

TOPICS = {
    "programmers": HelpTopic(
        "Programmer",
        (
            (
                "Start here",
                "Connect one programmer, select it, then choose Save to open "
                "Device setup. Its type is detected over USB.",
            ),
            (
                "Connection problems",
                "Refresh checks again. Set the minipro executable in Settings. If "
                "several programmers are connected, disconnect extras; minipro "
                "cannot choose between them.",
            ),
            (
                "Status bar",
                "A ? after the chip name means physical chip presence is "
                "unverified. Shortcut labels use Ctrl for the Control key.",
            ),
        ),
    ),
    "devices": HelpTopic(
        "Device setup",
        (
            (
                "Find a chip",
                "Type a part number or package. Up/Down previews matching devices "
                "while you keep typing. Tab leaves the search field.",
            ),
            (
                "Choose and continue",
                "Click or press Enter to choose a draft device. Enter focuses Save. "
                "Save applies the device and connection, then opens Operation. "
                "Leaving without saving keeps the previous active device.",
            ),
            (
                "Connection",
                "ZIF uses the programmer socket and any required adapter. Powered "
                "ICSP supplies VCC; external-power ICSP leaves VCC off.",
            ),
            (
                "Before running",
                "Check the chip documentation for orientation. Choosing a device "
                "does not verify that the physical chip is present.",
            ),
            (
                "Device diagrams",
                "When minipro metadata names a diagram, the preview appears below the "
                "device details. The package includes independently drawn references "
                "checked against the source material, including programmer-specific "
                "variants. Leave the image "
                "folder blank to use them; entering a folder makes it authoritative "
                "and lets you provide replacements. Unsupported or unmapped "
                "programmer/device combinations stay metadata only. The preview "
                "uses terminal bitmap support when available; Enlarge and Open image "
                "provide larger or native viewers.",
            ),
        ),
    ),
    "operation": HelpTopic(
        "Operation",
        (
            (
                "Start a job",
                "Choose an operation and fill in the fields shown. Review checks "
                "the request. Only Run job starts the operation.",
            ),
            (
                "Read / Program / Verify",
                "Read saves chip contents to a file. Program writes a file to the "
                "chip and verifies it. Verify compares the chip with a file.",
            ),
            (
                "Other operations",
                "Blank check tests for erased memory. Erase clears the chip. Read "
                "chip ID uses the selected chip algorithm; it cannot identify "
                "every unknown chip. Logic/RAM test availability depends on the "
                "device.",
            ),
            (
                "Memory region",
                "Code is main memory; Data is separate EEPROM; Config holds "
                "configuration. User is device-specific. Calibration is read-only. "
                "Available regions depend on the chip and minipro.",
            ),
            (
                "Advanced options",
                "Expand Advanced for supported native minipro controls such as read "
                "format, erase and verification behavior, protection, size policy, "
                "configuration sections, and device tuning such as voltage, SPI "
                "speed, or programming pulse. Controls are filtered "
                "by operation, memory, programmer, and device; there are no custom "
                "arbitrary command fields. For config memory, leave all section "
                "boxes unchecked to include all supported sections. Reset restores "
                "minipro defaults.",
            ),
        ),
    ),
    "files": HelpTopic(
        "Choose a file",
        (
            (
                "Type a path",
                "The tree follows the nearest existing folder as you type. Choose "
                "an existing file to open, or a filename in an existing folder to "
                "save.",
            ),
            (
                "Browse folders",
                "Left collapses a branch; Right expands it. Ctrl+h/l do the same. "
                "Tree selections, Home, and Parent folder update the path field.",
            ),
            (
                "Save safely",
                "Use path accepts the filename. Existing files require Replace "
                "confirmation. A failed or cancelled chip read preserves the "
                "existing file.",
            ),
        ),
    ),
    "settings": HelpTopic(
        "Settings",
        (
            (
                "minipro executable",
                "Install minipro separately. Leave the field blank to use system "
                "PATH, or enter its executable path. The active executable is "
                "shown below.",
            ),
            (
                "Automatic log saving",
                "Enable Automatically save log and choose a path. Date/time codes "
                "use the session start time: %Y-%m-%d is the date; %H-%M-%S is the "
                "time. Existing filenames receive a numbered suffix.",
            ),
            (
                "Device image folder",
                "Leave this blank to use the package's independently drawn references "
                "and check local minipro data folders. Enter a folder to "
                "make it authoritative and override bundled images with your own "
                "matching assets. Extract from Xgpro reads a local installer as an "
                "archive without running it and automatically uses the chosen output "
                "folder. Existing files are not overwritten automatically. Unsupported "
                "or missing references remain metadata only and do not show a folder "
                "picker.",
            ),
            (
                "Apply changes",
                "Save settings becomes available when a value changes. Demo "
                "settings are not persisted, but saving logs still works.",
            ),
        ),
    ),
    "review": HelpTopic(
        "Review the job",
        (
            (
                "Check the details",
                "Confirm the chip, programmer, connection, file, and memory "
                "region. The checksum identifies the reviewed input; changing that "
                "file requires another review.",
            ),
            (
                "Run or return",
                "Run job starts the operation. Back returns to configuration "
                "without running it.",
            ),
        ),
    ),
    "running": HelpTopic(
        "Running a job",
        (
            (
                "Progress",
                "The transcript shows minipro progress and diagnostics. Programmer "
                "and device configuration stay locked until the job finishes.",
            ),
            (
                "Cancel",
                "Use Cancel in the operation dialog to stop the job. An "
                "interrupted write or erase may leave incomplete chip data.",
            ),
            (
                "After completion",
                "Done returns to the workbench. Export log saves the transcript, "
                "with confirmation before replacing an existing file.",
            ),
            (
                "Chip ID mismatch",
                "When minipro explicitly offers -y to continue anyway, the dialog "
                "shows Continue at your own risk. Selecting it retries this job "
                "with -y and keeps both attempts in the transcript.",
            ),
        ),
    ),
    "history": HelpTopic(
        "Log (minipro)",
        (
            (
                "What is recorded",
                "Raw minipro commands, stdout, stderr, and exit codes, including "
                "discovery checks. Routine connection presence polls are omitted "
                "to keep the history useful. Demo entries are clearly marked as "
                "simulated.",
            ),
            (
                "Save a copy",
                "Save to file exports the transcript. Enable Automatically save "
                "log in Settings to write it throughout the session.",
            ),
        ),
    ),
    "memory": HelpTopic(
        "Memory browser",
        (
            (
                "Read the columns",
                "Address is the byte offset in the buffer. Hex bytes shows each "
                "byte in hexadecimal. Text shows printable characters; a dot "
                "represents a non-printable byte.",
            ),
            (
                "Navigate",
                "Scroll vertically through the entire buffer with the mouse, "
                "Up/Down, or Page Up/Page Down. Ctrl+k/j also scroll.",
            ),
            (
                "Buffer source",
                "The buffer comes from a completed code/data read, or a labeled "
                "demo sample. It is read-only. Scrolling never reads the chip.",
            ),
        ),
    ),
    "commands": HelpTopic(
        "Commands",
        (
            (
                "Find an action",
                "Type any part of a command name or description. Fuzzy matching "
                "also accepts abbreviated names.",
            ),
            (
                "Choose a result",
                "Up/Down chooses a command; Enter runs it. Shortcut hints appear "
                "on the right. Unavailable commands explain what is needed first.",
            ),
            (
                "Return",
                "Escape restores your previous focus. Hardware operations still "
                "require review before running.",
            ),
        ),
    ),
    "confirm": HelpTopic(
        "Confirm",
        (
            (
                "Choose deliberately",
                "Confirm performs the stated action. Back or Escape returns "
                "without making changes.",
            ),
        ),
    ),
}
