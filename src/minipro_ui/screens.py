"""Focused dialogs with a shared help contract and explicit operation review."""

from __future__ import annotations

import asyncio
import shlex
from pathlib import Path

from rich.table import Table
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.events import DescendantBlur, ScreenResume
from textual.screen import ModalScreen
from textual.timer import Timer
from textual.widgets import (
    Button,
    DirectoryTree,
    Input,
    Label,
    Link,
    LoadingIndicator,
    Log,
    Static,
)

from .advanced import advanced_summary
from .file_io import FileChoice, FileStamp, nearest_directory, save_text
from .help import SHORTCUTS, HelpTopic
from .models import DESCRIPTIONS, Operation
from .process import Result
from .widgets import BottomBar, FileTree
from .workflow import PreparedJob, Workflow
from .xgpro_images import ExtractionResult


class Dialog(ModalScreen[object]):
    """All dialogs can be dismissed without changing the underlying selection."""

    BINDINGS = [
        Binding("escape", "close", "Back", show=False),
    ]
    help_topic = "confirm"

    def action_close(self) -> None:
        self.dismiss(None)


class HelpScreen(Dialog):
    def __init__(self, context: HelpTopic) -> None:
        super().__init__()
        self.context = context

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog", classes="wide"):
            yield Label(f"Help · {self.context.title}", classes="eyebrow")
            with VerticalScroll():
                for title, explanation in self.context.sections:
                    with Vertical(classes="help-section"):
                        yield Label(title, classes="heading")
                        yield Static(explanation, markup=False)
                yield Label("Keyboard shortcuts", classes="heading help-keys")
                table = Table(box=None, padding=(0, 1), show_header=False, expand=True)
                table.add_column(style="#84d8c1", no_wrap=True)
                table.add_column(ratio=1)
                for key, action in SHORTCUTS:
                    table.add_row(key, action)
                yield Static(table)
            yield Button("Close help  ·  Esc", id="close")
        yield BottomBar()

    @on(Button.Pressed, "#close")
    def close_help(self) -> None:
        self.action_close()


class FileScreen(Dialog):
    help_topic = "files"

    def __init__(
        self, value: str = "", *, title: str = "CHOOSE A FILE", save: bool = False
    ) -> None:
        super().__init__()
        self.value = value
        self.save_mode = save
        self.title_text = title
        self.path_timer: Timer | None = None

    def compose(self) -> ComposeResult:
        root = nearest_directory(self.value)
        with Vertical(id="dialog"):
            yield Label(self.title_text, classes="eyebrow")
            yield Input(self.value, placeholder="Path, including filename", id="path")
            with Horizontal(classes="buttons"):
                yield Button("Parent folder", id="parent-folder")
                yield Button("Home", id="home-folder")
            yield FileTree(root, id="file-tree")
            yield Static("", id="file-error", markup=False)
            yield Static(
                (
                    "Choose a filename. Replacing an existing file "
                    "requires confirmation."
                    if self.save_mode
                    else "Choose an existing input file."
                ),
                classes="muted",
            )
            with Horizontal(classes="buttons"):
                yield Button("Back", id="back")
                yield Button("Use path", id="use", variant="primary")
        yield BottomBar()

    @on(Button.Pressed, "#parent-folder")
    @on(Button.Pressed, "#home-folder")
    def change_directory(self, event: Button.Pressed) -> None:
        tree = self.query_one(DirectoryTree)
        tree.path = (
            Path.home() if event.button.id == "home-folder" else Path(tree.path).parent
        )
        self.set_path(Path(tree.path))

    def set_path(self, path: Path) -> None:
        """Reflect tree navigation without resetting the tree's current root."""
        if self.path_timer:
            self.path_timer.stop()
        field = self.query_one("#path", Input)
        with field.prevent(Input.Changed):
            field.value = str(path)

    @on(Input.Changed, "#path")
    def path_changed(self) -> None:
        if self.path_timer:
            self.path_timer.stop()
        self.path_timer = self.set_timer(0.15, self.sync_tree)

    def sync_tree(self) -> None:
        tree = self.query_one(DirectoryTree)
        try:
            nearest = nearest_directory(self.query_one("#path", Input).value)
            if Path(tree.path) != nearest:
                tree.path = nearest
            self.query_one("#file-error", Static).update("")
        except (OSError, RuntimeError, ValueError) as error:
            self.query_one("#file-error", Static).update(str(error))

    def on_descendant_blur(self, event: DescendantBlur) -> None:
        if event.widget.id == "path":
            self.sync_tree()

    @on(DirectoryTree.DirectorySelected)
    @on(DirectoryTree.FileSelected)
    def file_selected(
        self, event: DirectoryTree.FileSelected | DirectoryTree.DirectorySelected
    ) -> None:
        self.set_path(event.path)

    @on(Button.Pressed, "#use")
    @on(Input.Submitted, "#path")
    def use_path(self) -> None:
        value = self.query_one(Input).value.strip()
        if value:
            path = Path(value).expanduser().absolute()
            if self.save_mode and (path.exists() or path.is_symlink()):
                try:
                    choice = FileChoice(path, FileStamp.capture(path))
                except (OSError, ValueError) as error:
                    self.query_one("#file-error", Static).update(str(error))
                    return

                def confirmed(value: object) -> None:
                    if value is True:
                        self.dismiss(choice)

                self.app.push_screen(ReplaceFileScreen(path), confirmed)
            elif self.save_mode and not path.parent.is_dir():
                self.query_one("#file-error", Static).update(
                    "The destination directory does not exist."
                )
            elif not self.save_mode and not path.is_file():
                self.query_one("#file-error", Static).update(
                    "Choose an existing regular file."
                )
            else:
                self.dismiss(FileChoice(path))
        else:
            self.query_one("#file-error", Static).update("Enter a filename first.")

    @on(Button.Pressed, "#back")
    def back(self) -> None:
        self.action_close()


class DirectoryScreen(Dialog):
    """Choose an existing folder or type a new destination folder."""

    help_topic = "files"

    def __init__(self, value: str = "", *, title: str = "CHOOSE A FOLDER") -> None:
        super().__init__()
        self.value = value
        self.title_text = title
        self.path_timer: Timer | None = None

    def compose(self) -> ComposeResult:
        root = nearest_directory(self.value)
        with Vertical(id="dialog"):
            yield Label(self.title_text, classes="eyebrow")
            yield Input(self.value, placeholder="Folder path", id="path")
            with Horizontal(classes="buttons"):
                yield Button("Parent folder", id="parent-folder")
                yield Button("Home", id="home-folder")
            yield FileTree(root, id="file-tree")
            yield Static("", id="file-error", markup=False)
            yield Static(
                "Choose an existing folder or type a new folder path.",
                classes="muted",
            )
            with Horizontal(classes="buttons"):
                yield Button("Back", id="back")
                yield Button("Use folder", id="use", variant="primary")
        yield BottomBar()

    @on(Button.Pressed, "#parent-folder")
    @on(Button.Pressed, "#home-folder")
    def change_directory(self, event: Button.Pressed) -> None:
        tree = self.query_one(DirectoryTree)
        tree.path = (
            Path.home() if event.button.id == "home-folder" else Path(tree.path).parent
        )
        self.set_path(Path(tree.path))

    def set_path(self, path: Path) -> None:
        if self.path_timer:
            self.path_timer.stop()
        field = self.query_one("#path", Input)
        with field.prevent(Input.Changed):
            field.value = str(path)

    @on(Input.Changed, "#path")
    def path_changed(self) -> None:
        if self.path_timer:
            self.path_timer.stop()
        self.path_timer = self.set_timer(0.15, self.sync_tree)

    def sync_tree(self) -> None:
        tree = self.query_one(DirectoryTree)
        try:
            nearest = nearest_directory(self.query_one("#path", Input).value)
            if Path(tree.path) != nearest:
                tree.path = nearest
            self.query_one("#file-error", Static).update("")
        except (OSError, RuntimeError, ValueError) as error:
            self.query_one("#file-error", Static).update(str(error))

    def on_descendant_blur(self, event: DescendantBlur) -> None:
        if event.widget.id == "path":
            self.sync_tree()

    @on(DirectoryTree.DirectorySelected)
    def directory_selected(self, event: DirectoryTree.DirectorySelected) -> None:
        self.set_path(event.path)

    @on(Button.Pressed, "#use")
    @on(Input.Submitted, "#path")
    def use_path(self) -> None:
        value = self.query_one("#path", Input).value.strip()
        if not value:
            self.query_one("#file-error", Static).update("Enter a folder path first.")
            return
        path = Path(value).expanduser().absolute()
        if path.exists() and not path.is_dir():
            self.query_one("#file-error", Static).update("Choose a folder, not a file.")
            return
        self.dismiss(path)

    @on(Button.Pressed, "#back")
    def back(self) -> None:
        self.action_close()


class ExtractImagesScreen(Dialog):
    """Extract Xgpro's image archive without blocking the Textual event loop."""

    help_topic = "settings"

    def __init__(self, installer: str = "", output: str = "") -> None:
        super().__init__()
        self.installer = installer
        self.output = output
        self.extracting = False
        self.pending_result: ExtractionResult | None = None

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog", classes="wide"):
            with VerticalScroll(id="extract-fields"):
                yield Label("EXTRACT XGPRO IMAGES", classes="eyebrow")
                yield Static(
                    "Choose a local Xgpro installer file. The installer is read as an "
                    "archive; it is never executed.",
                    classes="muted",
                )
                yield Link(
                    "Official Xgecu download page",
                    url="http://www.xgecu.com/EN/Download.html",
                    id="xgpro-download",
                )
                yield Label("Installer", classes="heading")
                with Horizontal(classes="file-row"):
                    yield Input(
                        self.installer,
                        placeholder="Path to Xgpro .exe, .rar, or .zip",
                        id="extract-installer",
                    )
                    yield Button("Browse…", id="extract-browse-installer")
                yield Label("Output folder", classes="heading")
                with Horizontal(classes="file-row"):
                    yield Input(
                        self.output,
                        placeholder="Folder for extracted images",
                        id="extract-output",
                    )
                    yield Button("Browse…", id="extract-browse-output")
                yield Static(
                    "Existing files are never overwritten automatically. "
                    "Choose another "
                    "folder or resolve any collision before trying again.",
                    classes="muted",
                )
                yield Static("", id="extract-status", markup=False)
            with Horizontal(classes="buttons"):
                yield Button("Cancel", id="extract-cancel")
                yield Button(
                    "Extract and use folder",
                    id="extract-run",
                    variant="primary",
                )
        yield BottomBar()

    @on(Button.Pressed, "#extract-browse-installer")
    def browse_installer(self) -> None:
        def chosen(value: object) -> None:
            if isinstance(value, FileChoice):
                self.query_one("#extract-installer", Input).value = str(value.path)

        self.app.push_screen(
            FileScreen(
                self.query_one("#extract-installer", Input).value,
                title="CHOOSE XGPRO INSTALLER",
            ),
            chosen,
        )

    @on(Button.Pressed, "#extract-browse-output")
    def browse_output(self) -> None:
        def chosen(value: object) -> None:
            if isinstance(value, Path):
                self.query_one("#extract-output", Input).value = str(value)

        self.app.push_screen(
            DirectoryScreen(
                self.query_one("#extract-output", Input).value,
                title="CHOOSE IMAGE OUTPUT FOLDER",
            ),
            chosen,
        )

    @on(Button.Pressed, "#extract-run")
    def run_extraction(self) -> None:
        installer = self.query_one("#extract-installer", Input).value.strip()
        output = self.query_one("#extract-output", Input).value.strip()
        status = self.query_one("#extract-status", Static)
        if not installer or not output:
            status.update("Enter both an installer file and an output folder.")
            self._scroll_status_into_view()
            return
        self.extracting = True
        self._set_extraction_controls(disabled=True)
        status.update("Extracting images…")

        self.run_worker(
            self._extract(installer, output),
            name="xgpro-image-extraction",
            group="xgpro-image-extraction",
            exclusive=True,
        )

    def _set_extraction_controls(self, *, disabled: bool) -> None:
        for selector in ("#extract-installer", "#extract-output"):
            self.query_one(selector, Input).disabled = disabled
        for selector in (
            "#extract-run",
            "#extract-cancel",
            "#extract-browse-installer",
            "#extract-browse-output",
        ):
            self.query_one(selector, Button).disabled = disabled

    def _scroll_status_into_view(self) -> None:
        fields = self.query_one("#extract-fields", VerticalScroll)
        status = self.query_one("#extract-status", Static)
        fields.scroll_to(
            y=status.virtual_region.y,
            animate=False,
            force=True,
            immediate=True,
        )

    def _show_extraction_progress(self, message: str) -> None:
        if self.extracting:
            self.query_one("#extract-status", Static).update(message)
            self._scroll_status_into_view()

    def _dismiss_pending_result(self) -> None:
        if self.pending_result is not None and self.app.screen is self:
            result = self.pending_result
            self.pending_result = None
            self.dismiss(result)

    def on_screen_resume(self, event: ScreenResume) -> None:
        del event
        self._dismiss_pending_result()

    async def _extract(self, installer: str, output: str) -> None:
        from .xgpro_images import ExtractionError, extract_images

        def progress(message: str) -> None:
            self.app.call_from_thread(self._show_extraction_progress, message)

        try:
            result = await asyncio.to_thread(
                extract_images, installer, output, progress=progress
            )
        except ExtractionError as error:
            self.extracting = False
            self._set_extraction_controls(disabled=False)
            self.query_one("#extract-status", Static).update(str(error))
            self._scroll_status_into_view()
        except Exception as error:
            self.extracting = False
            self._set_extraction_controls(disabled=False)
            self.query_one("#extract-status", Static).update(
                f"Could not extract images: {error}"
            )
            self._scroll_status_into_view()
        else:
            self.extracting = False
            self.pending_result = result
            self.query_one("#extract-status", Static).update(
                f"Extraction complete: {result.written} written, "
                f"{result.skipped} unchanged."
            )
            self._scroll_status_into_view()
            self._dismiss_pending_result()

    @on(Button.Pressed, "#extract-cancel")
    def cancel_extraction(self) -> None:
        if self.extracting:
            return
        if self.pending_result is not None:
            self._dismiss_pending_result()
        else:
            self.dismiss(None)

    def action_close(self) -> None:
        if self.extracting:
            return
        if self.pending_result is not None:
            self._dismiss_pending_result()
        else:
            self.dismiss(None)


class ReplaceFileScreen(Dialog):
    """Confirm replacement before returning an approved save destination."""

    def __init__(self, path: Path) -> None:
        super().__init__()
        self.path = path

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog", classes="replace-dialog"):
            yield Label("REPLACE FILE?", classes="eyebrow")
            yield Static(
                f"{self.path}\n\n"
                "This file already exists. Replace its contents when saving?",
                markup=False,
            )
            with Horizontal(classes="buttons"):
                yield Button("Back", id="back")
                yield Button("Replace", id="replace", variant="error")
        yield BottomBar()

    @on(Button.Pressed)
    def confirm(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "replace")


class ReviewScreen(Dialog):
    help_topic = "review"

    def __init__(self, prepared: PreparedJob) -> None:
        super().__init__()
        self.prepared = prepared

    def compose(self) -> ComposeResult:
        job = self.prepared.job
        description = DESCRIPTIONS[job.operation]
        if job.operation == Operation.WRITE and not job.advanced.verify_after_write:
            description = (
                "Program the selected memory region without minipro's post-write "
                "verification. Existing chip data may be lost."
            )
        with Vertical(id="dialog", classes="wide"):
            yield Label("REVIEW JOB", classes="eyebrow")
            yield Label(job.operation.value, classes="heading")
            with VerticalScroll():
                with Vertical(classes="command-preview"):
                    yield Label("minipro command", classes="command-title")
                    yield Static(
                        "$ " + shlex.join(self.prepared.command),
                        id="review-command",
                        markup=False,
                    )
                if self.prepared.staging is not None:
                    yield Static(
                        "The command uses a temporary file to protect your data. "
                        "The selected input or destination is shown below.",
                        classes="muted",
                    )
                if getattr(self.app, "demo", False):
                    yield Static(
                        "DEMO · This command is simulated; minipro will not run.",
                        classes="muted",
                    )
                yield Static(
                    f"Programmer   {self.prepared.programmer}\n"
                    f"Device       {job.device}\n"
                    f"Connection   {job.interface}\n"
                    + (
                        f"Memory       {job.memory}\n"
                        if job.operation
                        not in (Operation.ID, Operation.ERASE, Operation.TEST)
                        else ""
                    )
                    + (f"File         {job.path}\n" if job.operation.needs_file else "")
                    + "\n"
                    + description,
                    markup=False,
                )
                if summary := advanced_summary(job.advanced):
                    yield Static(
                        "Advanced options\n" + summary,
                        id="advanced-review",
                        markup=False,
                    )
                if job.replacement is not None:
                    yield Static(
                        "Existing destination will be replaced after "
                        "a successful read.",
                        markup=False,
                    )
                if self.prepared.digest:
                    yield Static(
                        f"\nInput SHA-256\n{self.prepared.digest}", markup=False
                    )
                yield Static(
                    (
                        "\nConfirm chip orientation and the required adapter before "
                        "starting. Only one programmer may be connected."
                    ),
                    classes="muted",
                )
            with Horizontal(classes="buttons"):
                yield Button("Back", id="back")
                yield Button(
                    "Run job",
                    id="run",
                    variant="error" if job.operation.destructive else "primary",
                )
        yield BottomBar()

    @on(Button.Pressed)
    def pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "run")


class RunScreen(Dialog):
    help_topic = "running"

    def __init__(self, workflow: Workflow, prepared: PreparedJob) -> None:
        super().__init__()
        self.workflow = workflow
        self.prepared = prepared
        self.running = True
        self.transcript: list[str] = []
        self.operation_task: asyncio.Task[None] | None = None
        self.result: Result | None = None
        self.retry_available = False

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog", classes="wide"):
            yield Label(self.prepared.job.operation.value.upper(), classes="eyebrow")
            yield Static("Running · keep the chip connected", id="result", markup=False)
            yield LoadingIndicator(id="activity")
            yield Log(id="transcript", highlight=False, max_lines=10000)
            yield Static(
                "Retry with -y bypasses the chip ID mismatch for this attempt. "
                "Continue only if you accept the risk.",
                id="retry-explanation",
                classes="muted",
                markup=False,
            )
            with Horizontal(classes="buttons", id="retry-row"):
                yield Button(
                    "Continue at your own risk",
                    id="retry",
                    variant="error",
                    disabled=True,
                )
            with Horizontal(classes="buttons"):
                yield Button("Cancel", id="stop", variant="error")
                yield Button("Export log", id="export", disabled=True)
                yield Button("Done", id="done", disabled=True, variant="primary")
        yield BottomBar()

    def on_mount(self) -> None:
        started = getattr(self.app, "operation_started", None)
        if started:
            started(self.prepared.job)
        self.query_one("#retry-row").display = False
        self.query_one("#retry", Button).display = False
        self.query_one("#retry-explanation").display = False
        self.operation_task = asyncio.create_task(self.run_job())

    def append(self, line: str) -> None:
        self.transcript.append(line)
        self.transcript = self.transcript[-10000:]
        self.query_one(Log).write_line(line)

    async def run_job(self) -> None:
        success = False
        self.result = None
        self.append(f"{self.prepared.programmer} · {self.prepared.job.device}")
        self.append("$ " + shlex.join(self.prepared.command))
        try:
            result = await self.workflow.execute(self.prepared, self.append)
            self.result = result
            status = (
                "Completed successfully"
                if result.returncode == 0
                else f"Failed · minipro exit code {result.returncode}"
            )
            success = result.returncode == 0
        except asyncio.CancelledError:
            status = "Stopped · chip contents may be incomplete; verify before use"
        except Exception as error:
            status = f"Could not complete: {error}"
        self.finish_run(status, success)

    def finish_run(self, status: str, success: bool) -> None:
        """Apply the common completed state after an attempt or preparation error."""
        self.append(status)
        self.query_one("#result", Static).update(status)
        self.running = False
        finished = getattr(self.app, "operation_finished", None)
        if finished:
            finished(self.prepared.job, success)
        self.query_one("#activity").display = False
        self.query_one("#stop", Button).disabled = True
        self.query_one("#done", Button).disabled = False
        self.query_one("#export", Button).disabled = False
        self.retry_available = bool(
            not success
            and self.result is not None
            and self.result.returncode > 0
            and self.result.retry_with_id_override
            and not self.prepared.job.continue_on_id_mismatch
        )
        retry_row = self.query_one("#retry-row")
        retry_row.display = self.retry_available
        self.query_one("#retry-explanation").display = self.retry_available
        retry = self.query_one("#retry", Button)
        retry.display = self.retry_available
        retry.disabled = not self.retry_available
        self.query_one("#done", Button).focus()

    @on(Button.Pressed, "#retry")
    def retry_operation(self) -> None:
        """Explicitly approve a single -y retry in this same transcript."""
        if self.running or not self.retry_available or self.result is None:
            return
        self.retry_available = False
        self.query_one("#retry", Button).disabled = True
        self.query_one("#retry-row").display = False
        self.query_one("#retry", Button).display = False
        self.query_one("#retry-explanation").display = False
        self.running = True
        started = getattr(self.app, "operation_started", None)
        if started:
            started(self.prepared.job)
        self.query_one("#result", Static).update(
            "Retrying with -y · keep the chip connected"
        )
        self.query_one("#activity").display = True
        self.query_one("#stop", Button).disabled = False
        self.query_one("#done", Button).disabled = True
        self.query_one("#export", Button).disabled = True
        self.query_one("#stop", Button).focus()
        self.operation_task = asyncio.create_task(self.run_retry())

    async def run_retry(self) -> None:
        """Prepare and execute the approved override, handling cancellation safely."""
        initial = self.prepared
        result = self.result
        assert result is not None
        self.result = None
        success = False
        try:
            prepared = await self.workflow.prepare_retry(initial, result)
            self.prepared = prepared
            self.append("\nRetry approved · continuing with -y")
            self.append("$ " + shlex.join(prepared.command))
            result = await self.workflow.execute(prepared, self.append)
            self.result = result
            success = result.returncode == 0
            status = (
                "Completed successfully"
                if success
                else f"Failed · minipro exit code {result.returncode}"
            )
        except asyncio.CancelledError:
            status = "Stopped · chip contents may be incomplete; verify before use"
        except Exception as error:
            status = f"Could not complete retry: {error}"
        self.finish_run(status, success)

    async def on_unmount(self) -> None:
        if self.operation_task and not self.operation_task.done():
            self.operation_task.cancel()
            await self.operation_task

    def action_close(self) -> None:
        if not self.running:
            self.dismiss("\n".join(self.transcript))

    @on(Button.Pressed, "#done")
    def done(self) -> None:
        self.action_close()

    @on(Button.Pressed, "#stop")
    def cancel_operation(self) -> None:
        """Cancel the active subprocess and let Workflow clean up partial output."""
        if self.operation_task and self.running:
            self.operation_task.cancel()

    @on(Button.Pressed, "#export")
    def export(self) -> None:
        def save(value: object) -> None:
            if isinstance(value, FileChoice):
                try:
                    save_text(value, "\n".join(self.transcript) + "\n")
                except (OSError, ValueError) as error:
                    self.notify(str(error), severity="error")
                else:
                    self.notify("Log saved.")

        self.app.push_screen(
            FileScreen(title="EXPORT LOG TO A NEW FILE", save=True), save
        )
