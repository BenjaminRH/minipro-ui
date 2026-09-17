"""The supported native minipro options for the current operation."""

from __future__ import annotations

from textual import on
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Checkbox, Input, Label, Select

from .models import AdvancedOptions, DeviceInfo, Operation


class AdvancedOptionsPanel(Vertical):
    """Collapsed, typed controls for minipro's native operation switches."""

    def __init__(self) -> None:
        super().__init__(id="advanced-options")
        self.operation = Operation.READ
        self.memory = "code"
        self.info: DeviceInfo | None = None
        self.interface = "zif"
        self._supported_tuning: set[str] = set()

    def compose(self) -> ComposeResult:
        yield Button("Advanced ▸", id="advanced-toggle")
        with Vertical(id="advanced-fields"):
            with Horizontal(id="advanced-read-format-row", classes="advanced-row"):
                yield Label("Read format")
                yield Select(
                    [
                        ("Binary (.bin)", "bin"),
                        ("Intel HEX (.hex)", "ihex"),
                        ("S-record (.srec)", "srec"),
                    ],
                    value="bin",
                    allow_blank=False,
                    id="advanced-read-format",
                )
            with Horizontal(id="advanced-erase-mode-row", classes="advanced-row"):
                yield Label("Erase mode")
                yield Select(
                    [
                        ("Default", "default"),
                        ("Skip erase", "skip"),
                        ("Force erase", "force"),
                    ],
                    value="default",
                    allow_blank=False,
                    id="advanced-erase-mode",
                )
            yield Checkbox("Verify after write", value=True, id="advanced-verify")
            yield Checkbox("Unprotect before write", id="advanced-unprotect")
            yield Checkbox("Protect after write", id="advanced-protect")
            yield Checkbox("Check pins before operation", id="advanced-check-pins")
            yield Checkbox("Skip chip ID check", id="advanced-skip-id")
            with Horizontal(id="advanced-size-policy-row", classes="advanced-row"):
                yield Label("Input size")
                yield Select(
                    [
                        ("Exact", "exact"),
                        ("Allow mismatch with warning", "warn"),
                        ("Allow mismatch silently", "silent"),
                    ],
                    value="exact",
                    allow_blank=False,
                    id="advanced-size-policy",
                )
            with Vertical(id="advanced-config-row", classes="advanced-row"):
                yield Label("Configuration sections")
                with Horizontal(id="advanced-config-checks"):
                    yield Checkbox("Fuses", id="advanced-config-fuses")
                    yield Checkbox("UID", id="advanced-config-uid")
                    yield Checkbox("Lock", id="advanced-config-lock")
                yield Label(
                    "Leave all unchecked to include all supported sections.",
                    classes="muted",
                    id="advanced-config-help",
                )
            with Vertical(id="advanced-tuning-row", classes="advanced-row"):
                yield Label("Device tuning")
                with Vertical(id="advanced-tuning-fields"):
                    yield from self._tuning_row("vpp", "VPP (V)")
                    yield from self._tuning_row("vdd", "VDD (V)")
                    yield from self._tuning_row("vcc", "VCC (V)")
                    yield from self._tuning_row("speed", "SPI speed (MHz)")
                    with Horizontal(
                        classes="advanced-tuning-item", id="advanced-tuning-pulse-row"
                    ):
                        yield Label("Pulse (µs)")
                        yield Input(
                            id="advanced-tuning-pulse", placeholder="Device default"
                        )
            with Horizontal(classes="buttons", id="advanced-actions"):
                yield Button("Reset to defaults", id="advanced-reset")

    @staticmethod
    def _tuning_row(name: str, label: str) -> ComposeResult:
        with Horizontal(
            classes="advanced-tuning-item", id=f"advanced-tuning-{name}-row"
        ):
            yield Label(label)
            yield Select(
                [("Device default", "")],
                value="",
                allow_blank=False,
                id=f"advanced-tuning-{name}",
            )

    def on_mount(self) -> None:
        self._set_collapsed(True)
        self._refresh_visibility()

    def _set_collapsed(self, collapsed: bool) -> None:
        self.query_one("#advanced-fields").display = not collapsed
        self._refresh_title()

    def _refresh_title(self) -> None:
        count = len(self._nondefault_values())
        suffix = f" · {count} selected" if count else ""
        arrow = " ▾" if self.query_one("#advanced-fields").display else " ▸"
        self.query_one("#advanced-toggle", Button).label = f"Advanced{suffix}{arrow}"

    @on(Button.Pressed, "#advanced-toggle")
    def toggle(self) -> None:
        self._set_collapsed(self.query_one("#advanced-fields").display)

    @on(Button.Pressed, "#advanced-reset")
    def reset(self) -> None:
        self.reset_defaults()

    @on(Select.Changed)
    @on(Checkbox.Changed)
    @on(Input.Changed)
    def changed(self) -> None:
        if self.is_attached:
            self._refresh_title()

    def reset_defaults(self) -> None:
        select_values: tuple[tuple[str, str], ...] = (
            ("#advanced-read-format", "bin"),
            ("#advanced-erase-mode", "default"),
            ("#advanced-size-policy", "exact"),
        )
        for selector, value in select_values:
            self.query_one(selector, Select).value = value
        bool_values: tuple[tuple[str, bool], ...] = (
            ("#advanced-verify", True),
            ("#advanced-unprotect", False),
            ("#advanced-protect", False),
            ("#advanced-check-pins", False),
            ("#advanced-skip-id", False),
            ("#advanced-config-fuses", False),
            ("#advanced-config-uid", False),
            ("#advanced-config-lock", False),
        )
        for bool_selector, bool_value in bool_values:
            self.query_one(bool_selector, Checkbox).value = bool_value
        for name in ("vpp", "vdd", "vcc", "speed"):
            self.query_one(f"#advanced-tuning-{name}", Select).value = ""
        self.query_one("#advanced-tuning-pulse", Input).value = ""
        if self.is_attached:
            self._refresh_visibility()
            self._refresh_title()

    def configure_context(
        self,
        operation: Operation,
        info: DeviceInfo | None,
        memory: str,
        interface: str,
    ) -> None:
        self.operation, self.info, self.memory, self.interface = (
            operation,
            info,
            memory,
            interface,
        )
        if not self.is_attached:
            return
        self.reset_defaults()
        self._supported_tuning = {
            option.name for option in (info.tuning_options if info else ())
        }
        for name in ("vpp", "vdd", "vcc", "speed"):
            option = next(
                (
                    item
                    for item in (info.tuning_options if info else ())
                    if item.name == name
                ),
                None,
            )
            selector = self.query_one(f"#advanced-tuning-{name}", Select)
            selector.set_options(
                [("Device default", ""), *((value, value) for value in option.values)]
                if option
                else [("Device default", "")]
            )
            selector.value = ""
        self._refresh_visibility()
        self._refresh_title()

    def _tuning_applicable(self, name: str) -> bool:
        if name in {"vpp", "vdd", "pulse"}:
            return self.operation == Operation.WRITE
        if name == "vcc":
            return self.operation in {Operation.WRITE, Operation.TEST}
        if name == "speed":
            return (
                self.operation
                in {
                    Operation.READ,
                    Operation.WRITE,
                    Operation.VERIFY,
                    Operation.BLANK,
                }
                and self.memory != "config"
            )
        return False

    def _refresh_visibility(self) -> None:
        context_supported = bool(
            self.info and self.operation in self.info.available_operations
        )
        read_formats = bool(getattr(self.info, "supports_read_format", False))
        supports_size = bool(getattr(self.info, "supports_size_override", False))
        erase_supported = bool(
            self.info and Operation.ERASE in self.info.available_operations
        )
        id_supported = bool(
            self.info and Operation.ID in self.info.available_operations
        )
        self.query_one("#advanced-read-format-row").display = (
            read_formats
            and self.operation == Operation.READ
            and self.memory in {"code", "data", "user"}
        )
        self.query_one("#advanced-erase-mode-row").display = (
            self.operation == Operation.WRITE and erase_supported
        )
        self.query_one("#advanced-verify").display = self.operation == Operation.WRITE
        self.query_one("#advanced-unprotect").display = (
            self.operation == Operation.WRITE
            and bool(getattr(self.info, "supports_unprotect", False))
        )
        self.query_one("#advanced-protect").display = (
            self.operation == Operation.WRITE
            and bool(getattr(self.info, "supports_protect", False))
        )
        self.query_one("#advanced-check-pins").display = bool(
            getattr(self.info, "supports_pin_check", False) and self.interface == "zif"
        )
        self.query_one("#advanced-skip-id").display = (
            self.operation == Operation.READ and id_supported
        )
        self.query_one("#advanced-size-policy-row").display = (
            self.operation in {Operation.WRITE, Operation.VERIFY}
            and self.memory != "config"
            and supports_size
        )
        supported_sections = set(getattr(self.info, "supported_config_sections", ()))
        config_visible = (
            self.operation
            in {Operation.READ, Operation.WRITE, Operation.VERIFY, Operation.BLANK}
            and self.memory == "config"
        )
        for name in ("fuses", "uid", "lock"):
            self.query_one(f"#advanced-config-{name}").display = (
                name in supported_sections
            )
        self.query_one("#advanced-config-row").display = config_visible and bool(
            supported_sections
        )
        for name in ("vpp", "vdd", "vcc", "speed", "pulse"):
            self.query_one(f"#advanced-tuning-{name}-row").display = (
                name in self._supported_tuning and self._tuning_applicable(name)
            )
        self.query_one("#advanced-tuning-row").display = any(
            self.query_one(f"#advanced-tuning-{name}-row").display
            for name in ("vpp", "vdd", "vcc", "speed", "pulse")
        )
        self.display = context_supported and any(
            self.query_one(selector).display
            for selector in (
                "#advanced-read-format-row",
                "#advanced-erase-mode-row",
                "#advanced-verify",
                "#advanced-unprotect",
                "#advanced-protect",
                "#advanced-check-pins",
                "#advanced-skip-id",
                "#advanced-size-policy-row",
                "#advanced-config-row",
                "#advanced-tuning-row",
            )
        )

    def _nondefault_values(self) -> list[str]:
        values: list[str] = []
        if (
            self.query_one("#advanced-read-format", Select).value != "bin"
            and self.query_one("#advanced-read-format-row").display
        ):
            values.append("format")
        if (
            self.query_one("#advanced-erase-mode", Select).value != "default"
            and self.query_one("#advanced-erase-mode-row").display
        ):
            values.append("erase")
        if (
            not self.query_one("#advanced-verify", Checkbox).value
            and self.query_one("#advanced-verify").display
        ):
            values.append("verify")
        for selector, name in (
            ("#advanced-unprotect", "unprotect"),
            ("#advanced-protect", "protect"),
            ("#advanced-check-pins", "pins"),
            ("#advanced-skip-id", "ID"),
        ):
            if (
                self.query_one(selector, Checkbox).value
                and self.query_one(selector).display
            ):
                values.append(name)
        if (
            self.query_one("#advanced-size-policy", Select).value != "exact"
            and self.query_one("#advanced-size-policy-row").display
        ):
            values.append("size")
        for selector, name in (
            ("#advanced-config-fuses", "fuses"),
            ("#advanced-config-uid", "UID"),
            ("#advanced-config-lock", "lock"),
        ):
            if (
                self.query_one(selector, Checkbox).value
                and self.query_one(selector).display
            ):
                values.append(name)
        values.extend(
            name
            for name in ("vpp", "vdd", "vcc", "speed", "pulse")
            if self._tuning_value(name)
            and self.query_one(f"#advanced-tuning-{name}-row").display
        )
        return values

    def _tuning_value(self, name: str) -> str:
        if name == "pulse":
            return self.query_one("#advanced-tuning-pulse", Input).value.strip()
        value = self.query_one(f"#advanced-tuning-{name}", Select).value
        return value.strip() if isinstance(value, str) else ""

    def options(self) -> AdvancedOptions:
        readable = self.operation == Operation.READ and self.memory in {
            "code",
            "data",
            "user",
        }
        readable = readable and bool(getattr(self.info, "supports_read_format", False))
        writable = self.operation == Operation.WRITE
        sized = (
            self.operation in {Operation.WRITE, Operation.VERIFY}
            and self.memory != "config"
            and bool(getattr(self.info, "supports_size_override", False))
        )
        config = self.memory == "config" and self.operation in {
            Operation.READ,
            Operation.WRITE,
            Operation.VERIFY,
            Operation.BLANK,
        }
        tuning = tuple(
            (name, self._tuning_value(name))
            for name in ("vpp", "vdd", "vcc", "speed", "pulse")
            if name in self._supported_tuning
            and self._tuning_applicable(name)
            and self._tuning_value(name)
        )
        sections = tuple(
            name
            for name, selector in (
                ("fuses", "#advanced-config-fuses"),
                ("uid", "#advanced-config-uid"),
                ("lock", "#advanced-config-lock"),
            )
            if config
            and self.query_one(selector, Checkbox).value
            and self.query_one(selector).display
        )
        return AdvancedOptions(
            read_format=(
                str(self.query_one("#advanced-read-format", Select).value)
                if readable
                else "bin"
            ),
            erase_mode=(
                str(self.query_one("#advanced-erase-mode", Select).value)
                if writable and self.query_one("#advanced-erase-mode-row").display
                else "default"
            ),
            verify_after_write=(
                self.query_one("#advanced-verify", Checkbox).value if writable else True
            ),
            unprotect_before_write=(
                self.query_one("#advanced-unprotect", Checkbox).value
                if writable and self.query_one("#advanced-unprotect").display
                else False
            ),
            protect_after_write=(
                self.query_one("#advanced-protect", Checkbox).value
                if writable and self.query_one("#advanced-protect").display
                else False
            ),
            check_pins=(
                self.query_one("#advanced-check-pins", Checkbox).value
                if self.query_one("#advanced-check-pins").display
                else False
            ),
            skip_id=(
                self.query_one("#advanced-skip-id", Checkbox).value
                if self.operation == Operation.READ
                and self.query_one("#advanced-skip-id").display
                else False
            ),
            size_policy=(
                str(self.query_one("#advanced-size-policy", Select).value)
                if sized
                else "exact"
            ),
            config_sections=sections,
            tuning=tuning,
        )


def advanced_summary(options: AdvancedOptions) -> str:
    """Describe selected native options in product language for job review."""
    choices: list[str] = []
    if options.read_format != "bin":
        choices.append(f"Read format: {options.read_format.upper()}")
    if options.erase_mode != "default":
        choices.append(f"Erase: {options.erase_mode}")
    if not options.verify_after_write:
        choices.append("Verification after writing: skipped")
    if options.unprotect_before_write:
        choices.append("Unprotect before writing")
    if options.protect_after_write:
        choices.append("Protect after writing")
    if options.check_pins:
        choices.append("Check pins before operation")
    if options.skip_id:
        choices.append("Chip ID check: skipped")
    if options.size_policy != "exact":
        choices.append(f"Input size mismatch: {options.size_policy}")
    if options.config_sections:
        choices.append("Configuration sections: " + ", ".join(options.config_sections))
    choices.extend(f"{name}: {value}" for name, value in options.tuning)
    return "\n".join(choices)
