"""Tabbed programming workbench; hardware behavior stays in the backend layer."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.events import Key
from textual.widget import Widget
from textual.widgets import (
    Button,
    Checkbox,
    Input,
    Label,
    Log,
    OptionList,
    Select,
    Static,
    TabbedContent,
    TabPane,
)
from textual.widgets.option_list import Option

from .advanced import AdvancedOptionsPanel
from .backend import Backend, CliAdapter
from .catalog import match_devices
from .commands import Command, CommandScreen
from .demo import DemoBackend
from .diagram_widgets import DeviceDiagrams
from .file_io import FileChoice, save_text
from .focus_hints import FocusHints
from .help import TOPICS
from .memory import MemoryView
from .models import DESCRIPTIONS, DeviceInfo, Job, Operation
from .screens import (
    DirectoryScreen,
    ExtractImagesScreen,
    FileScreen,
    HelpScreen,
    ReviewScreen,
    RunScreen,
)
from .session_log import SessionLog
from .settings import Settings, config_file
from .widgets import BottomBar, CatalogList, CatalogSearch
from .workflow import Workflow
from .xgpro_images import ExtractionResult

MEMORIES = ("code", "data", "config", "user", "calibration")


class MiniproApp(App[None]):
    """Own UI state without inferring physical chip presence from a catalog choice."""

    TITLE = "minipro-ui"
    CSS_PATH = "app.tcss"
    ENABLE_COMMAND_PALETTE = False
    BINDINGS = [
        Binding(
            "ctrl+question_mark,ctrl+slash,ctrl+underscore,"
            "ctrl+shift+slash,ctrl+shift+question_mark,ctrl+shift+underscore",
            "help",
            "Help",
            priority=True,
        ),
        Binding("ctrl+p", "commands", "Commands", priority=True),
        Binding(
            "alt+space,left_alt,right_alt", "focus_hints", show=False, priority=True
        ),
        Binding("ctrl+q", "quit", "Quit", priority=True),
        Binding("ctrl+k", "arrow('up')", show=False, priority=True),
        Binding("ctrl+j", "arrow('down')", show=False, priority=True),
        Binding("ctrl+h", "arrow('left')", show=False, priority=True),
        Binding("ctrl+l", "arrow('right')", show=False, priority=True),
    ]

    def __init__(
        self,
        *,
        demo: bool = False,
        executable: str | None = None,
        allow_unverified: bool = False,
        settings: Settings | None = None,
        backend: Backend | None = None,
    ) -> None:
        super().__init__()
        self.settings = settings or Settings.load()
        if executable is not None:
            self.settings.executable = executable
        self.settings_baseline = (
            self.settings.executable,
            self.settings.save_log,
            self.settings.log_path,
            getattr(self.settings, "image_directory", ""),
        )
        self.demo = demo
        self.allow_unverified = allow_unverified
        self.backend: Backend | None = backend or (DemoBackend() if demo else None)
        self.busy = False
        self.configuration_locked = False
        self.configuration_states: list[tuple[Widget, bool]] = []
        self.device_name = ""
        self.device_info: DeviceInfo | None = None
        self.draft_device_name = ""
        self.highlighted_device_name = ""
        self.device_connection = "zif"
        self.file_choice: FileChoice | None = None
        self.session_log = SessionLog(
            lambda message: self.notify(message, severity="error")
        )
        if isinstance(self.backend, (CliAdapter, DemoBackend)):
            self.backend.log = self.session_log
        self.devices: list[str] = []
        self.matches: list[str] = []
        self.connected_models: list[str] = []
        self.selected_programmer = ""
        self.connection_snapshot: tuple[str, ...] | None = None
        self._initial_programmer_focus_pending = True
        self.connection_note = "Checking"
        self.operation_state = "IDLE"
        self.catalog_family = ""
        self.metadata_generation = 0
        self.metadata_cache: dict[tuple[str, str], DeviceInfo] = {}
        self.memory_data: bytes | None = None
        self.memory_source = ""
        self.memory_path: Path | None = None
        self.rendered_log_parts = -1

    def compose(self) -> ComposeResult:
        with TabbedContent(initial="programmers", id="tabs"):
            with TabPane("Programmer", id="programmers"), VerticalScroll():
                yield Label("Connected programmer", classes="heading")
                yield Static(
                    "The programmer type is detected over USB. "
                    "Connect one unit to begin.",
                    classes="muted",
                )
                yield Select(
                    [], prompt="No programmer selected", id="programmer", disabled=True
                )
                yield Static(
                    "Checking USB connection…", id="programmer-info", markup=False
                )
                with Horizontal(classes="buttons"):
                    yield Button("Refresh", id="refresh-programmers")
                    yield Button(
                        "Save",
                        id="to-device",
                        variant="primary",
                        disabled=True,
                    )
            with TabPane("Device setup", id="setup", disabled=True):
                with Horizontal(id="device-browser"):
                    with Vertical(id="device-search-pane"):
                        yield CatalogSearch(
                            placeholder="Part number / package…", id="search"
                        )
                        yield CatalogList(id="matches")
                        yield Static(
                            "↑↓ Preview · Enter Select · Tab Next", classes="muted"
                        )
                    with VerticalScroll(id="device-info-pane") as info_pane:
                        info_pane.can_focus = False
                        yield Static(
                            "Highlight a device to see its details.",
                            id="device-info",
                            markup=False,
                        )
                        yield DeviceDiagrams()
                yield Static(
                    "Choose a device, then Save to apply it",
                    id="device",
                    markup=False,
                )
                with Horizontal(id="device-options"):
                    yield Label("Connection", classes="connection-label")
                    yield Select(
                        [
                            ("ZIF socket", "zif"),
                            ("ICSP · powered", "icsp"),
                            ("ICSP · external power", "external"),
                        ],
                        value="zif",
                        allow_blank=False,
                        id="interface",
                    )
                    yield Button(
                        "Save",
                        id="to-operation",
                        variant="primary",
                        disabled=True,
                    )
            with TabPane("Operation", id="operate", disabled=True), VerticalScroll():
                yield Label("Operation", classes="form-label")
                yield Select(
                    [(op.value, op.value) for op in Operation],
                    value=Operation.READ.value,
                    allow_blank=False,
                    id="operation",
                )
                yield Static(
                    DESCRIPTIONS[Operation.READ], id="description", markup=False
                )
                with Vertical(id="file-fields"):
                    yield Label(
                        "Save backup to a new file",
                        id="file-label",
                        classes="form-label",
                    )
                    with Horizontal(classes="file-row"):
                        yield Input(placeholder="Path to file…", id="file")
                        yield Button("Browse…", id="choose-file")
                with Vertical(id="memory-fields"):
                    yield Label("Memory region", classes="form-label")
                    yield Select(
                        [(n.capitalize(), n) for n in MEMORIES],
                        value="code",
                        allow_blank=False,
                        id="memory",
                    )
                yield Static("", id="memory-help", classes="muted", markup=False)
                yield AdvancedOptionsPanel()
                yield Static("", id="job-error", markup=False)
                yield Button("Review job", id="review", variant="primary")
            with TabPane("Memory browser", id="memory-tab", disabled=True):
                yield Static(
                    "No successful memory read in this session.",
                    id="memory-source",
                    markup=False,
                )
                yield Static("", id="memory-columns")
                yield MemoryView(id="memory-log")
            with TabPane("Log (minipro)", id="log-tab"):
                yield Log(id="raw-log", highlight=False, auto_scroll=False)
                with Horizontal(classes="buttons"):
                    yield Button("Save to file", id="save-log-file", variant="primary")
            with TabPane("Settings", id="preferences"):
                with VerticalScroll(id="settings-fields"):
                    with Vertical(classes="setting-group"):
                        yield Label("minipro executable", classes="heading")
                        yield Static(
                            "Leave blank to use minipro from the system PATH.",
                            classes="muted",
                        )
                        yield Input(
                            self.settings.executable,
                            placeholder="System minipro",
                            id="executable",
                        )
                        yield Static(
                            "Active minipro: checking…", id="used-minipro", markup=False
                        )
                        yield Static(
                            "", id="backend-details", classes="muted", markup=False
                        )
                    with Vertical(classes="setting-group"):
                        yield Label("Device image folder", classes="heading")
                        yield Static(
                            "Leave blank to use bundled references, or choose a folder "
                            "with your own matching images.",
                            classes="muted",
                            markup=False,
                        )
                        with Horizontal(classes="file-row"):
                            yield Input(
                                getattr(self.settings, "image_directory", ""),
                                placeholder="Optional image folder",
                                id="image-directory",
                            )
                            yield Button("Browse…", id="browse-image-folder")
                        yield Static(
                            "",
                            id="image-directory-warning",
                            classes="image-directory-warning",
                            markup=False,
                        )
                        yield Button("Extract from Xgpro", id="extract-xgpro")
                    with Vertical(classes="setting-group"):
                        yield Checkbox(
                            "Automatically save log",
                            value=self.settings.save_log,
                            id="save-log",
                        )
                        yield Label("Log file path", id="log-path-label")
                        yield Input(
                            self.settings.log_path,
                            id="log-path",
                            disabled=not self.settings.save_log,
                        )
                        yield Static(
                            "Codes insert the session start date and time:\n"
                            "%Y = year (2026)   %m = month (09)   %d = day (11)\n"
                            "%H = hour (00–23)  %M = minute (00–59)  "
                            "%S = second (00–59)\n\n"
                            "Example: /tmp/minipro_ui_%Y-%m-%d_%H-%M-%S.log\n"
                            "becomes /tmp/minipro_ui_2026-09-11_14-30-05.log\n"
                            "Use %% for a literal %. Codes are optional.\n"
                            "If the file exists, a numbered suffix is added.",
                            classes="muted",
                            markup=False,
                        )
                    with Vertical(classes="setting-group"):
                        yield Label("Settings file", classes="heading")
                        yield Static(str(config_file()), classes="muted", markup=False)
                yield Button(
                    "Save settings",
                    id="save-settings",
                    variant="primary",
                    disabled=True,
                )
        yield BottomBar()

    def on_mount(self) -> None:
        self.theme = "textual-dark"
        self.session_log.configure(self.settings.save_log, self.settings.log_path)
        self.action_refresh()
        self.set_interval(5, self.poll_connection)
        self.set_interval(0.25, self.refresh_log_view)
        self.query_one("#refresh-programmers", Button).focus()
        self.update_image_directory_warning()

    @property
    def running_screen(self) -> RunScreen | None:
        return next(
            (s for s in self.screen_stack if isinstance(s, RunScreen) and s.running),
            None,
        )

    @property
    def workspace_active(self) -> bool:
        return len(self.screen_stack) == 1 and not self.busy

    def update_status(self) -> None:
        """Keep status visible on every modal without claiming chip detection."""
        state = self.operation_state
        if state == "IDLE":
            state = (
                "NO PROGRAMMER"
                if not self.selected_programmer
                else ("NO DEVICE" if not self.device_name else "IDLE")
            )
        programmer = self.selected_programmer or "USB —"
        prefix = f"{'DEMO ' if self.demo else ''}{programmer} │ "
        suffix = f" │ {state}"
        for screen in self.screen_stack:
            for bar in screen.query(BottomBar):
                label = bar.query_one(".connection", Static)
                width = max(1, label.size.width - len(prefix) - len(suffix) - 1)
                target = f"{self.device_name}?" if self.device_name else "No device"
                if len(target) > width:
                    target = target[: width - 1] + "…"
                label.update(prefix + target + suffix + " ")
                label.tooltip = (
                    f"Programmer: {programmer}\n"
                    f"Device: {self.device_name or 'Not selected'}\n"
                    "Selection does not verify physical chip presence.\n"
                    f"Operation: {state}"
                )

    def action_help(self) -> None:
        if isinstance(self.screen, HelpScreen):
            self.screen.action_close()
        else:
            topic = getattr(self.screen, "help_topic", None)
            if topic is None:
                topic = {
                    "programmers": "programmers",
                    "setup": "devices",
                    "operate": "operation",
                    "preferences": "settings",
                    "log-tab": "history",
                    "memory-tab": "memory",
                }[self.query_one(TabbedContent).active]
            self.push_screen(HelpScreen(TOPICS[topic]))

    def action_arrow(self, direction: str) -> None:
        """Route Vim aliases through exactly the same path as physical arrows."""
        self.post_message(Key(direction, None))

    def action_focus_hints(self) -> None:
        """Toggle numbered focus navigation, except while reading help."""
        if isinstance(self.screen, HelpScreen):
            return
        if isinstance(self.screen, FocusHints):
            self.screen.action_close()
        else:
            self.push_screen(FocusHints(self.screen))

    def action_commands(self) -> None:
        if not isinstance(self.screen, CommandScreen):
            self.push_screen(CommandScreen(self.commands()))

    def commands(self) -> list[Command]:
        """The palette is the single inventory of application commands."""
        blocked = (
            "Close the current dialog first"
            if len(self.screen_stack) > 1
            else ("Wait for the current request" if self.busy else "")
        )
        operation_blocked = blocked or (
            "Select a connected programmer first"
            if not self.selected_programmer
            else "Select a device first"
            if not self.device_name
            else ""
        )
        if self.device_info and not self.device_info.available_operations:
            operation_blocked = (
                operation_blocked or "No available operations for this device"
            )
        result = [
            Command(
                "Focus hints",
                "Show numbers on visible controls, then type a number to focus one.",
                "focus_hints",
                shortcut="Alt+space",
                reason="Close Help first"
                if isinstance(self.screen, HelpScreen)
                else "",
            ),
            Command(
                "Programmer",
                "Choose the connected programmer; its type is detected automatically.",
                "programmer_tab",
                reason=blocked,
            ),
            Command(
                "Device setup",
                "Search the catalog and configure the connection.",
                "device",
                reason=blocked
                or (
                    "Select a connected programmer first"
                    if not self.selected_programmer
                    else ""
                ),
            ),
            Command(
                "Operation",
                "Choose and prepare a chip operation.",
                "operation_tab",
                reason=operation_blocked,
            ),
            Command(
                "Memory browser",
                "Browse bytes from the last successful memory read.",
                "memory_tab",
                reason=blocked or ("Read memory first" if not self.has_memory else ""),
            ),
            Command(
                "Settings",
                "Configure minipro and saved preferences.",
                "settings",
                reason=blocked,
            ),
            Command(
                "Choose file",
                "Browse for the current operation's input or destination.",
                "file",
                reason=operation_blocked
                or (
                    "This operation does not use a file"
                    if not self.current_job().operation.needs_file
                    else ""
                ),
            ),
            Command(
                "Review job",
                "Validate this job before explicitly running it.",
                "review",
                reason=operation_blocked,
            ),
            Command(
                "Refresh connection",
                "Check USB connection and reload the device catalog.",
                "refresh",
                reason=blocked,
            ),
            Command(
                "Log (minipro)",
                "View raw minipro commands and output; save them to a file.",
                "log",
                reason=blocked,
            ),
            Command(
                "Help", "Explain this screen and keyboard navigation.", "help", "Ctrl+/"
            ),
            Command(
                "Quit",
                "Exit minipro-ui after stopping any running operation.",
                "quit",
                "Ctrl+q",
            ),
        ]
        for op in Operation:
            if self.device_info and op not in self.device_info.available_operations:
                continue
            result.append(
                Command(
                    op.value,
                    DESCRIPTIONS[op],
                    f"choose_operation('{op.name}')",
                    reason=operation_blocked,
                )
            )
        if len(self.screen_stack) == 1:
            result.append(
                Command(
                    "Save log to file",
                    "Export the full raw command transcript.",
                    "save_log_file",
                    reason=blocked,
                )
            )
            result.append(
                Command(
                    "Save settings",
                    "Save modified settings.",
                    "apply_preferences",
                    reason=blocked
                    or (
                        "No settings have changed"
                        if self.query_one("#save-settings", Button).disabled
                        else ""
                    ),
                )
            )
        # Dialog buttons are commands too, including Save history and Run job.
        if len(self.screen_stack) > 1:
            for button in self.screen.query(Button):
                visible = button.display and all(
                    ancestor.display for ancestor in button.ancestors
                )
                if button.id and visible:
                    result.append(
                        Command(
                            str(button.label),
                            button.tooltip
                            and str(button.tooltip)
                            or f"{button.label} in the current dialog.",
                            f"dialog_button('{button.id}')",
                            reason="Unavailable in the current state"
                            if button.disabled
                            else "",
                        )
                    )
        return result

    def action_dialog_button(self, button_id: str) -> None:
        self.screen.query_one(f"#{button_id}", Button).press()

    async def action_quit(self) -> None:
        extracting = any(
            isinstance(screen, ExtractImagesScreen)
            and (screen.extracting or screen.pending_result is not None)
            for screen in self.screen_stack
        )
        if self.running_screen or extracting:
            self.notify(
                "Wait for the current operation to finish before quitting.",
                severity="warning",
            )
        else:
            self.exit()

    def save_settings(self) -> None:
        if not self.demo:
            try:
                self.settings.save()
            except OSError as error:
                self.notify(f"Could not save preferences: {error}", severity="error")

    def poll_connection(self) -> None:
        if self.workspace_active and not self.running_screen:
            self.action_refresh()

    @work(group="connection")
    async def action_refresh(self) -> None:
        if not self.workspace_active:
            return
        self.busy = True
        try:
            if self.backend is None:
                executable = self.settings.discover()
                if executable is None:
                    raise RuntimeError(
                        "minipro not found. Configure its path in Settings."
                    )
                self.backend = CliAdapter(
                    executable,
                    allow_unverified=self.allow_unverified,
                    log=self.session_log,
                )
            version = await self.backend.version()
            models = await self.backend.connected()
            self.connected_models = models
            self.update_programmers(models)
            self.connection_note = "Not connected"
            used = (
                str(self.backend.executable)
                if isinstance(self.backend, CliAdapter)
                else "Demo backend (no executable is run)"
            )
            self.query_one("#used-minipro", Static).update(f"Active minipro: {used}")
            self.query_one("#backend-details", Static).update(
                f"minipro {version.release} · {version.revision}\n"
                + ("Demo: no USB access" if self.demo else "System backend")
            )
            family = self.selected_programmer
            if family and family != self.catalog_family:
                await self.load_catalog(family)
        except Exception as error:
            self.connected_models = []
            self.clear_device_diagram()
            self.highlighted_device_name = ""
            self.update_programmers([])
            self.connection_note = "Unavailable"
            self.query_one("#used-minipro", Static).update(
                "Active minipro: unavailable"
            )
            self.query_one("#backend-details", Static).update(str(error))
            self.query_one("#programmer-info", Static).update(str(error))
            self.query_one("#device-info", Static).update(
                f"{error}\nOpen Settings to configure minipro."
            )
        finally:
            self.busy = False
            self.update_status()

    async def load_catalog(self, family: str) -> None:
        if self.backend is None:
            return
        self.devices = await self.backend.devices(family)
        self.catalog_family = family
        self.filter_devices()

    def update_programmers(self, models: list[str]) -> None:
        """Auto-select a single USB unit; minipro cannot target one of several."""
        if self.configuration_locked:
            return
        previous_catalog = self.catalog_family
        snapshot = tuple(models)
        if snapshot == self.connection_snapshot:
            return
        self.connection_snapshot = snapshot
        selection = self.query_one("#programmer", Select)
        with selection.prevent(Select.Changed):
            selection.set_options(
                [
                    (f"{model} · USB programmer", str(i))
                    for i, model in enumerate(models)
                ]
            )
            selection.disabled = len(models) != 1
            if len(models) == 1:
                selection.value = "0"
            else:
                selection.clear()
        self.select_programmer(models[0] if len(models) == 1 else "")
        if self._initial_programmer_focus_pending:
            # Discovery is asynchronous. Only complete the initial default when
            # the user has left the initial Refresh focus in place.
            if self.focused is self.query_one("#refresh-programmers", Button):
                self._focus_programmer_default()
            self._initial_programmer_focus_pending = False
        if len(models) == 1 and models[0] == previous_catalog:
            self.filter_devices()
            self.restore_highlighted_preview()
        self.query_one("#programmer-info", Static).update(
            f"Detected type: {models[0]}\nReady to configure a device."
            if len(models) == 1
            else "Multiple programmers detected. Disconnect the extra units; "
            "minipro cannot target an individual unit."
            if models
            else "No programmer connected. Connect one unit, then refresh "
            "or wait for detection."
        )

    def select_programmer(self, model: str) -> None:
        if self.configuration_locked:
            return
        self.clear_device_diagram()
        self.highlighted_device_name = ""
        self.metadata_generation += 1
        self.selected_programmer = model
        self.query_one(AdvancedOptionsPanel).reset_defaults()
        tabs = self.query_one(TabbedContent)
        if model:
            tabs.enable_tab("setup")
            if self.catalog_family and model != self.catalog_family:
                self.device_name = ""
                self.draft_device_name = ""
                self.query_one("#device", Static).update("No device selected")
        else:
            self.device_name = ""
            self.draft_device_name = ""
            self.query_one("#device", Static).update("No device selected")
            if tabs.active == "setup":
                self.action_programmer_tab()
            tabs.disable_tab("setup")
        if model and self.device_name:
            tabs.enable_tab("operate")
        else:
            self.device_info = None
            if tabs.active == "operate":
                self.action_programmer_tab()
            tabs.disable_tab("operate")
        self.query_one("#to-operation", Button).disabled = not (
            model and self.draft_device_name
        )
        self.query_one("#to-device", Button).disabled = not model
        self.update_status()

    @on(Select.Changed, "#programmer")
    def programmer_changed(self, event: Select.Changed) -> None:
        model = (
            self.connected_models[0]
            if event.value == "0" and len(self.connected_models) == 1
            else ""
        )
        self.select_programmer(model)
        if model and model != self.catalog_family and not self.busy:
            self.action_refresh()

    @on(Input.Changed, "#search")
    def filter_devices(self) -> None:
        self.metadata_generation += 1
        self.clear_device_diagram()
        self.highlighted_device_name = ""
        query = self.query_one("#search", Input).value
        ordered = dict.fromkeys(
            [n for n in self.settings.recent_devices if n in self.devices]
            + self.devices
        )
        self.matches = match_devices(ordered, query)
        listing = self.query_one("#matches", OptionList)
        listing.clear_options()
        listing.add_options(Option(Text(name)) for name in self.matches)
        listing.highlighted = 0 if self.matches else None
        if not self.matches:
            self.query_one("#device-info", Static).update(
                "No matching devices. Try a shorter part number."
            )

    def clear_device_diagram(self) -> None:
        """Invalidate and hide any image while metadata selection is changing."""
        self.query_one(DeviceDiagrams).clear()

    def show_device_diagram(self, name: str, info: DeviceInfo) -> None:
        """Show a diagram for the currently highlighted metadata record."""
        self.highlighted_device_name = name
        executable = (
            self.backend.executable
            if isinstance(self.backend, CliAdapter)
            else self.settings.discover()
        )
        self.query_one(DeviceDiagrams).show_device(
            info.details,
            getattr(self.settings, "image_directory", ""),
            executable,
            self.selected_programmer,
        )

    def refresh_device_diagram(self) -> None:
        """Re-resolve the highlighted diagram after an image-folder change."""
        name = self.highlighted_device_name
        info = self.metadata_cache.get((self.selected_programmer, name))
        if name and info is not None:
            self.show_device_diagram(name, info)
        else:
            self.clear_device_diagram()

    def restore_highlighted_preview(self) -> None:
        """Restore cached metadata and its diagram after reconnecting or tab changes."""
        index = self.query_one("#matches", OptionList).highlighted
        if index is None or index >= len(self.matches):
            self.clear_device_diagram()
            return
        name = self.matches[index]
        info = self.metadata_cache.get((self.selected_programmer, name))
        if info is None:
            return
        self.highlighted_device_name = name
        self.query_one("#device-info", Static).update(info.details)
        self.show_device_diagram(name, info)

    @on(OptionList.OptionHighlighted, "#matches")
    @work(exclusive=True, group="metadata")
    async def highlighted_device(self, event: OptionList.OptionHighlighted) -> None:
        if self.backend is None or event.option_index >= len(self.matches):
            self.clear_device_diagram()
            return
        generation = self.metadata_generation
        backend = self.backend
        name = self.matches[event.option_index]
        family = self.selected_programmer
        key = (family, name)
        self.clear_device_diagram()
        self.highlighted_device_name = name
        pane = self.query_one("#device-info", Static)
        pane.update(f"{name}\nLoading details…")
        try:
            if key not in self.metadata_cache:
                info = await self.backend.device_info(family, name)
                self.metadata_cache[key] = info
            if generation == self.metadata_generation and backend is self.backend:
                pane.update(self.metadata_cache[key].details)
                self.show_device_diagram(name, self.metadata_cache[key])
                if name == self.draft_device_name:
                    self.configure_interface_options(self.metadata_cache[key])
        except Exception as error:
            if generation == self.metadata_generation and backend is self.backend:
                self.clear_device_diagram()
                self.highlighted_device_name = ""
                pane.update(f"{name}\nDetails unavailable: {error}")

    @on(Input.Submitted, "#search")
    def select_highlighted(self) -> None:
        index = self.query_one("#matches", OptionList).highlighted
        if index is not None and index < len(self.matches):
            self.device_selected(self.matches[index])

    @on(OptionList.OptionSelected, "#matches")
    def select_device(self, event: OptionList.OptionSelected) -> None:
        self.device_selected(self.matches[event.option_index])
        self.query_one("#search", Input).focus()

    def device_selected(self, value: object) -> None:
        """Stage a device choice; the active configuration changes only on Save."""
        if self.configuration_locked:
            return
        if isinstance(value, str):
            self.draft_device_name = value
            self.query_one("#device", Static).update(f"Draft: {value} · Save to apply")
            if (
                info := self.metadata_cache.get((self.selected_programmer, value))
            ) is not None:
                self.configure_interface_options(info)
                if getattr(info, "supported_interfaces", ("zif",)):
                    self.query_one("#to-operation", Button).disabled = False
            else:
                self.query_one("#to-operation", Button).disabled = False
            self.query_one("#to-operation", Button).focus()

    @work(exclusive=True, group="save-device")
    async def action_save_device(self) -> None:
        """Commit the staged chip and connection together before opening Operation."""
        if (
            self.configuration_locked
            or not self.selected_programmer
            or not self.draft_device_name
        ):
            return
        value = self.draft_device_name
        family = self.selected_programmer
        backend = self.backend
        if backend is None:
            return
        try:
            info = self.metadata_cache.get((family, value))
            if info is None:
                info = await backend.device_info(family, value)
                self.metadata_cache[(family, value)] = info
        except Exception as error:
            self.query_one("#device", Static).update(f"Could not load device: {error}")
            return
        if (
            family != self.selected_programmer
            or value != self.draft_device_name
            or backend is not self.backend
            or self.configuration_locked
        ):
            return
        if not tuple(getattr(info, "supported_interfaces", ("zif",))):
            self.query_one("#device", Static).update(
                f"{value} has no supported connection interface for this programmer."
            )
            return
        changed = value != self.device_name
        self.device_name = value
        self.device_info = info
        self.device_connection = str(self.query_one("#interface", Select).value)
        self.configure_interface_options(info)
        self.device_connection = str(self.query_one("#interface", Select).value)
        self.query_one(AdvancedOptionsPanel).reset_defaults()
        self.query_one("#device", Static).update(
            f"Saved: {value} · physical presence unverified"
        )
        if changed:
            self.settings.recent_devices = list(
                dict.fromkeys([value, *self.settings.recent_devices])
            )[:12]
            self.save_settings()
            if self.demo and value != "7400":
                self.memory_data = b"\xff" * 32768
                self.memory_path = None
                self.memory_source = f"DEMO sample: {value} · simulated erased memory"
                self.query_one(TabbedContent).enable_tab("memory-tab")
                self.render_memory()
        self.query_one(TabbedContent).enable_tab("operate")
        self.configure_operations()
        self.update_status()
        # Let tab-enabled messages settle before selecting the newly enabled pane.
        self.call_after_refresh(self.action_operation_tab)

    def configure_operations(self) -> None:
        """Keep operation controls synchronized with the saved device's capabilities."""
        if self.device_info is None:
            return
        operations = self.device_info.available_operations
        selector = self.query_one("#operation", Select)
        selected = selector.value
        selector.disabled = not operations
        selector.display = bool(operations)
        self.query_one("#review", Button).disabled = not operations
        if operations:
            selector.set_options([(op.value, op.value) for op in operations])
            selector.value = selected if selected in operations else operations[0].value
            self.display_operation(Operation(str(selector.value)))
        else:
            self.query_one("#description", Static).update(
                "This device has no supported operations in this minipro version."
                if self.device_info.operations is not None
                else "Device capabilities could not be loaded. Ensure infoic.xml "
                "is installed in share/minipro, or set MINIPRO_HOME to its directory."
            )
            self.query_one("#file-fields").display = False
            self.query_one("#memory-fields").display = False
            self.query_one("#memory-help", Static).update("")
            self.query_one(AdvancedOptionsPanel).display = False

    def configure_regions(self, operation: Operation) -> None:
        if self.device_info is None:
            return
        regions = self.device_info.available_regions(operation)
        selector = self.query_one("#memory", Select)
        selected = selector.value
        self.query_one("#memory-fields").display = bool(regions)
        if regions:
            selector.set_options([(region.capitalize(), region) for region in regions])
            selector.value = selected if selected in regions else regions[0]
        self.configure_advanced(operation)

    def configure_interface_options(self, info: DeviceInfo) -> None:
        labels = {
            "zif": "ZIF socket",
            "icsp": "ICSP · powered",
            "external": "ICSP · external power",
        }
        supported = tuple(getattr(info, "supported_interfaces", ("zif",)))
        selector = self.query_one("#interface", Select)
        current = str(selector.value)
        if not supported:
            selector.set_options([("Unavailable for this device", "")])
            selector.value = ""
            selector.disabled = True
            self.query_one("#to-operation", Button).disabled = True
            return
        selector.set_options(
            [(labels.get(interface, interface), interface) for interface in supported]
        )
        selector.value = current if current in supported else supported[0]
        selector.disabled = False

    @on(Select.Changed, "#operation")
    def operation_changed(self, event: Select.Changed) -> None:
        if isinstance(event.value, str):
            self.display_operation(Operation(event.value))

    @on(Select.Changed, "#memory")
    def memory_changed(self) -> None:
        self.update_memory_help()
        self.configure_advanced()

    @on(Checkbox.Changed, "#advanced-verify")
    def advanced_verify_changed(self, event: Checkbox.Changed) -> None:
        if not isinstance(self.device_info, DeviceInfo):
            return
        operation = Operation(str(self.query_one("#operation", Select).value))
        description = DESCRIPTIONS[operation]
        if operation == Operation.WRITE and not event.value:
            description = (
                "Program the selected memory region without minipro's post-write "
                "verification. Existing chip data may be lost."
            )
        self.query_one("#description", Static).update(description)

    def configure_advanced(self, operation: Operation | None = None) -> None:
        panel = self.query_one(AdvancedOptionsPanel)
        if operation is None:
            operation = Operation(str(self.query_one("#operation", Select).value))
        panel.configure_context(
            operation,
            self.device_info,
            str(self.query_one("#memory", Select).value),
            self.device_connection,
        )

    def update_memory_help(self) -> None:
        help_text = self.query_one("#memory-help", Static)
        explanations = {
            "code": (
                "Code is main addressable memory: firmware on an MCU, or ordinary "
                "stored bytes on EEPROM/flash."
            ),
            "data": "Data is separate data memory, often EEPROM.",
            "config": (
                "Config holds device settings such as fuses, security/lock bits, "
                "and IDs; its layout is device-specific."
            ),
            "user": "User is separate user/ID memory when provided by the chip.",
            "calibration": "Calibration stores factory values and is read-only here.",
        }
        operation = str(self.query_one("#operation", Select).value)
        if (
            operation
            in {
                Operation.ID.value,
                Operation.ERASE.value,
                Operation.TEST.value,
            }
            or not self.query_one("#memory-fields").display
        ):
            help_text.update("")
            help_text.display = False
            return
        memory = str(self.query_one("#memory", Select).value)
        text = explanations.get(memory, "")
        help_text.update(text)
        help_text.display = bool(text)

    def display_operation(self, operation: Operation) -> None:
        """Refresh fields even when a device change preserves the operation value."""
        if self.device_info and operation not in self.device_info.available_operations:
            return
        self.query_one("#description", Static).update(DESCRIPTIONS[operation])
        self.query_one("#file-fields").display = operation.needs_file
        self.query_one("#memory-fields").display = operation not in (
            Operation.ID,
            Operation.ERASE,
            Operation.TEST,
        )
        self.query_one("#file-label", Label).update(
            "Backup destination" if operation == Operation.READ else "Input file"
        )
        self.query_one("#job-error", Static).update("")
        self.configure_regions(operation)
        self.update_memory_help()

    def switch_tab(self, tab: str) -> None:
        if self.configuration_locked and tab in {
            "programmers",
            "setup",
            "operate",
            "preferences",
        }:
            return
        self.query_one(TabbedContent).active = tab

    def action_programmer_tab(self) -> None:
        self.switch_tab("programmers")
        self._focus_programmer_default()

    def _focus_programmer_default(self) -> None:
        """Focus the useful next action for the current programmer state."""
        if (
            len(self.screen_stack) != 1
            or self.query_one(TabbedContent).active != "programmers"
            or self.configuration_locked
        ):
            return
        target = (
            self.query_one("#to-device", Button)
            if not self.query_one("#to-device", Button).disabled
            else self.query_one("#refresh-programmers", Button)
        )
        target.focus()

    def action_device(self) -> None:
        if self.selected_programmer:
            self.switch_tab("setup")
            self.restore_highlighted_preview()
            self.query_one("#search", Input).focus()

    def action_operation_tab(self) -> None:
        if self.selected_programmer and self.device_name:
            self.switch_tab("operate")
            self.query_one("#operation", Select).focus()

    def action_choose_operation(self, name: str) -> None:
        if (
            self.device_info
            and Operation[name] not in self.device_info.available_operations
        ):
            return
        self.query_one("#operation", Select).value = Operation[name].value
        self.action_operation_tab()

    def action_settings(self) -> None:
        self.switch_tab("preferences")
        self.query_one("#executable", Input).focus()

    def action_file(self) -> None:
        job = self.current_job()
        if self.workspace_active and job.operation.needs_file:
            self.push_screen(
                FileScreen(
                    self.query_one("#file", Input).value,
                    title="SAVE BACKUP"
                    if job.operation == Operation.READ
                    else "OPEN INPUT FILE",
                    save=job.operation == Operation.READ,
                ),
                self.file_selected,
            )

    def file_selected(self, value: object) -> None:
        if isinstance(value, FileChoice):
            self.file_choice = value
            self.query_one("#file", Input).value = str(value.path)

    def action_log(self) -> None:
        self.switch_tab("log-tab")
        self.query_one("#raw-log", Log).focus()
        self.refresh_log_view()

    @on(TabbedContent.TabActivated)
    def tab_activated(self, event: TabbedContent.TabActivated) -> None:
        if event.pane.id == "programmers":
            self._focus_programmer_default()
        if event.pane.id != "operate":
            self.query_one("#job-error", Static).update("")
        if event.pane.id == "log-tab":
            self.refresh_log_view()

    def refresh_log_view(self) -> None:
        """Refresh raw text only when visible and changed; preserve reading position."""
        if self.query_one(TabbedContent).active != "log-tab":
            return
        count = len(self.session_log.parts)
        if count == self.rendered_log_parts:
            return
        log = self.query_one("#raw-log", Log)
        y = log.scroll_y
        follow = log.is_vertical_scroll_end
        log.clear()
        log.write(self.session_log.text)
        if follow:
            log.scroll_end(animate=False)
        else:
            log.scroll_to(y=y, animate=False)
        self.rendered_log_parts = count

    def action_save_log_file(self) -> None:
        def save(value: object) -> None:
            if isinstance(value, FileChoice):
                try:
                    save_text(value, self.session_log.text)
                except (OSError, ValueError) as error:
                    self.notify(str(error), severity="error")
                else:
                    self.notify("Log saved.")

        self.push_screen(FileScreen(title="SAVE LOG", save=True), save)

    def current_job(self) -> Job:
        operation = Operation(str(self.query_one("#operation", Select).value))
        value = self.query_one("#file", Input).value.strip()
        advanced = self.query_one(AdvancedOptionsPanel).options()
        return Job(
            operation,
            self.device_name,
            Path(value).expanduser().absolute()
            if value and operation.needs_file
            else None,
            str(self.query_one("#memory", Select).value),
            self.device_connection,
            self.file_choice.replacement
            if self.file_choice is not None
            and value
            and Path(value).expanduser().absolute() == self.file_choice.path
            and operation == Operation.READ
            else None,
            advanced=advanced,
        )

    @work
    async def action_review(self) -> None:
        if not self.workspace_active:
            return
        self.action_operation_tab()
        error_box = self.query_one("#job-error", Static)
        if self.backend is None or not self.device_name or not self.selected_programmer:
            error_box.update("Select a device in Device setup first.")
            return
        self.busy = True
        error_box.update("Checking job…")
        try:
            workflow = Workflow(self.backend)
            prepared = await workflow.prepare(
                self.selected_programmer, self.current_job()
            )

            def reviewed(value: object) -> None:
                if value is True:
                    self.operation_started(prepared.job)
                    self.push_screen(RunScreen(workflow, prepared), self.job_finished)
                else:
                    prepared.close()

            error_box.update("")
            self.push_screen(ReviewScreen(prepared), reviewed)
        except Exception as error:
            error_box.update(str(error))
        finally:
            self.busy = False

    def set_configuration_locked(self, locked: bool) -> None:
        """Freeze configuration until subprocess cleanup has finished."""
        if locked == self.configuration_locked:
            return
        self.configuration_locked = locked
        if locked:
            controls = list(
                self.query(
                    "#programmer, #interface, #search, #matches, #to-device, "
                    "#to-operation, #operation, #file, #choose-file, #memory, #review, "
                    "#executable, #save-log, #log-path, #save-settings, "
                    "#image-directory, #browse-image-folder, #extract-xgpro, "
                    "#advanced-options, #advanced-options *"
                )
            )
            tabs = self.query_one(TabbedContent)
            controls.extend(
                tabs.get_tab(tab)
                for tab in ("programmers", "setup", "operate", "preferences")
            )
            self.configuration_states = [
                (widget, widget.disabled) for widget in controls
            ]
            for widget, _ in self.configuration_states:
                widget.disabled = True
        else:
            for widget, disabled in self.configuration_states:
                widget.disabled = disabled
            self.configuration_states.clear()

    def operation_started(self, prepared: Job) -> None:
        """Mark a job active before its dialog or subprocess can be observed."""
        self.operation_state = {
            Operation.READ: "READING",
            Operation.WRITE: "WRITING",
            Operation.VERIFY: "VERIFYING",
            Operation.BLANK: "CHECKING",
            Operation.ERASE: "ERASING",
            Operation.ID: "READING ID",
            Operation.TEST: "TESTING",
        }[prepared.operation]
        self.set_configuration_locked(True)
        self.update_status()

    def operation_finished(self, prepared: Job, success: bool) -> None:
        """Called when execution ends, even if the result dialog stays open."""
        self.operation_state = "IDLE"
        self.set_configuration_locked(False)
        if (
            success
            and prepared.operation == Operation.READ
            and prepared.memory in ("code", "data")
            and getattr(prepared.advanced, "read_format", "bin") == "bin"
        ):
            self.memory_data = None
            self.memory_path = prepared.path
            self.memory_source = f"Last read: {prepared.device} · {prepared.memory}"
            self.query_one(TabbedContent).enable_tab("memory-tab")
            self.render_memory()
        self.update_status()

    def job_finished(self, value: object) -> None:
        self.action_refresh()

    @property
    def has_memory(self) -> bool:
        return self.memory_data is not None or self.memory_path is not None

    def action_memory_tab(self) -> None:
        if self.has_memory:
            self.switch_tab("memory-tab")
            self.query_one("#memory-log", MemoryView).focus()

    def render_memory(self) -> None:
        if not self.has_memory:
            return
        viewer = self.query_one("#memory-log", MemoryView)
        try:
            viewer.set_source(self.memory_data, self.memory_path)
            self.query_one("#memory-source", Static).update(
                f"{self.memory_source}\n{viewer.byte_count:,} bytes"
                " · read-only local buffer"
                " · scrolling never reads the chip"
            )
        except OSError as error:
            self.query_one("#memory-source", Static).update(
                f"Buffer unavailable: {error}"
            )

    @on(Button.Pressed)
    def button_pressed(self, event: Button.Pressed) -> None:
        if not self.workspace_active:
            return
        actions: dict[str, Callable[[], object]] = {
            "to-operation": self.action_save_device,
            "to-device": self.action_device,
            "refresh-programmers": self.action_refresh,
            "choose-file": self.action_file,
            "review": self.action_review,
            "save-settings": self.apply_settings,
            "save-log-file": self.action_save_log_file,
            "extract-xgpro": self.action_extract_xgpro,
            "browse-image-folder": self.action_browse_image_folder,
        }
        if action := actions.get(event.button.id or ""):
            action()

    @on(Checkbox.Changed, "#save-log")
    def log_saving_changed(self, event: Checkbox.Changed) -> None:
        self.query_one("#log-path", Input).disabled = not event.value
        self.query_one("#log-path-label", Label).set_class(not event.value, "muted")
        self.settings_edited()

    def action_apply_preferences(self) -> None:
        self.apply_settings()

    def action_browse_image_folder(self) -> None:
        if self.configuration_locked:
            return

        def chosen(value: object) -> None:
            if isinstance(value, Path):
                self.query_one("#image-directory", Input).value = str(value)

        self.push_screen(
            DirectoryScreen(
                self.query_one("#image-directory", Input).value,
                title="CHOOSE DEVICE IMAGE FOLDER",
            ),
            chosen,
        )

    def action_extract_xgpro(self) -> None:
        if self.configuration_locked:
            return

        def extracted(value: object) -> None:
            if isinstance(value, ExtractionResult):
                self.use_extracted_image_directory(value.output)

        self.push_screen(
            ExtractImagesScreen(output=getattr(self.settings, "image_directory", "")),
            extracted,
        )

    def use_extracted_image_directory(self, output: Path) -> None:
        """Persist only the extracted image folder and refresh its preview."""
        candidate = replace(self.settings, image_directory=str(output))
        if not self.demo:
            try:
                candidate.save()
            except OSError as error:
                self.notify(f"Could not save image folder: {error}", severity="error")
                return
        self.settings = candidate
        image_field = self.query_one("#image-directory", Input)
        image_field.value = str(output)
        self.update_image_directory_warning()
        self.settings_baseline = (
            candidate.executable,
            candidate.save_log,
            candidate.log_path,
            candidate.image_directory,
        )
        self.settings_edited()
        self.refresh_device_diagram()
        self.notify(f"Using extracted images from {output}")

    def settings_values(self) -> tuple[str, bool, str, str]:
        return (
            self.query_one("#executable", Input).value,
            self.query_one("#save-log", Checkbox).value,
            self.query_one("#log-path", Input).value,
            self.query_one("#image-directory", Input).value,
        )

    def update_image_directory_warning(self, value: str | None = None) -> None:
        """Show advisory validation for the optional device image directory."""
        warning = self.query_one("#image-directory-warning", Static)
        raw_value = (
            self.query_one("#image-directory", Input).value if value is None else value
        ).strip()
        message = ""
        if raw_value:
            try:
                path = Path(raw_value).expanduser()
                if path.exists():
                    if not path.is_dir():
                        message = "This image path is not a folder."
                else:
                    message = "This image folder does not exist."
            except (OSError, RuntimeError, ValueError):
                message = "This image folder path could not be checked."
        warning.update(message)
        warning.display = bool(message)

    @on(Input.Changed, "#executable")
    @on(Input.Changed, "#log-path")
    @on(Input.Changed, "#image-directory")
    def settings_edited(self) -> None:
        self.update_image_directory_warning()
        self.query_one("#save-settings", Button).disabled = (
            self.configuration_locked
            or self.settings_values() == self.settings_baseline
        )

    def apply_settings(self) -> None:
        if (
            self.configuration_locked
            or self.settings_values() == self.settings_baseline
        ):
            return
        executable, save_log, log_path, image_directory = self.settings_values()
        candidate = replace(
            self.settings,
            executable=executable.strip(),
            save_log=save_log,
            log_path=log_path.strip(),
            image_directory=image_directory.strip(),
        )
        if not self.demo:
            try:
                candidate.save()
            except OSError as error:
                self.notify(f"Could not save settings: {error}", severity="error")
                return
        backend_changed = candidate.executable != self.settings.executable
        log_changed = (candidate.save_log, candidate.log_path) != (
            self.settings.save_log,
            self.settings.log_path,
        )
        image_directory_changed = candidate.image_directory != getattr(
            self.settings, "image_directory", ""
        )
        self.settings = candidate
        self.settings_baseline = self.settings_values()
        self.settings_edited()
        if log_changed:
            self.session_log.configure(candidate.save_log, candidate.log_path)
        if image_directory_changed:
            self.refresh_device_diagram()
        if backend_changed:
            self.clear_device_diagram()
            self.highlighted_device_name = ""
            self.backend = DemoBackend(log=self.session_log) if self.demo else None
            self.metadata_cache.clear()
            self.catalog_family = ""
            self.device_name = ""
            self.draft_device_name = ""
            self.query_one("#device", Static).update("No device selected")
            self.connection_snapshot = None
            self.update_programmers([])
            self.action_refresh()

    def on_unmount(self) -> None:
        self.session_log.close()
