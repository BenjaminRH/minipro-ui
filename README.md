# minipro-ui

A keyboard-friendly terminal workbench for **[minipro](https://gitlab.com/DavidGriffith/minipro)**,
the open-source XGecu programmer software. Pick a chip, choose a task, and review
before running it. Press **Ctrl+? on any screen or dialog** for explanations and shortcuts.

The UI uses your installed minipro executable. It does not install, fork, patch,
or replace minipro. The adapter can be replaced independently of the screens.

## Install and run

First install the prerequisites for your operating system below. Then download
and extract this project's source (or clone this repository), open its directory,
and install the application:

```sh
cd minipro-ui
uv tool install --python 3.12 .
uv tool update-shell
```

Open a new terminal after `update-shell`. You can now run this from **any directory**:

```sh
minipro-ui
```

No environment activation or project checkout is needed to run the installed copy.
The launcher normally lives in `~/.local/bin`; `uv tool dir --bin` prints the actual
location. `uv tool install` creates an isolated Python environment and installs the
UI's Python dependencies automatically. The checkout can be removed afterward.

Useful alternatives:

```sh
minipro-ui --demo                         # Explore without minipro or USB hardware
minipro-ui --minipro /path/to/minipro     # Explicit executable instead of PATH
minipro-ui --help
```

To install your updated checkout again:

```sh
uv tool install --reinstall --python 3.12 .
```

To uninstall: `uv tool uninstall minipro-ui`.

### Prerequisites

- **macOS or Linux**, with a terminal supporting keyboard navigation (mouse optional).
  An 80×24 terminal works with scrolling; 110×40 or larger is more comfortable.
- **uv**, for installation and isolated environments.
- **Python 3.12 or newer**. The commands below let uv install Python 3.12; an existing
  supported Python installation also works.
- **minipro**, installed separately, including its device databases and libusb runtime.
  Version **0.7.4** is the initial CLI compatibility baseline. See compatibility below.
- For hardware work: a programmer, a supported chip and the correct adapter/connection.
  Linux additionally needs permission to access the USB device.

You do **not** need a C compiler to install this UI. A compiler and development
headers are only needed when building minipro itself from source.

#### Fedora

Install the packaged minipro and the tools used in the following instructions:

```sh
sudo dnf install minipro git curl ca-certificates
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Open a new shell, then:

```sh
uv python install 3.12
minipro -V
```

Fedora's available minipro version depends on the Fedora release. Check `minipro -V`
before choosing a backend compatibility policy. If building minipro from source
instead, its build prerequisites are:

```sh
sudo dnf install gcc make pkgconf-pkg-config libusb1-devel zlib-ng-compat-devel git
```

Then follow the source build commands in the Debian section. On older Fedora releases, the zlib development package may be named `zlib-devel`.
Fedora package information: [minipro](https://packages.fedoraproject.org/pkgs/minipro/minipro/).

#### Debian / Ubuntu

A source build avoids assuming your release has a sufficiently recent minipro
package. Install uv and the prerequisites for building minipro:

```sh
sudo apt update
sudo apt install ca-certificates curl git build-essential pkg-config libusb-1.0-0-dev zlib1g-dev
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Open a new shell, then install Python and build the baseline minipro release:

```sh
uv python install 3.12
git clone --branch 0.7.4 --depth 1 https://gitlab.com/DavidGriffith/minipro.git minipro-source
cd minipro-source
make
sudo make install
minipro -V
```

These commands install **minipro**, not the UI. Return to the `minipro-ui` directory
and use `uv tool install --python 3.12 .` as described above.

If your distribution already provides a suitable minipro package, you can use it
instead of compiling; check its version and keep its matching data files.

#### macOS

Install [Homebrew](https://brew.sh/) if necessary, following its PATH setup prompts:

```sh
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```

Then install uv and minipro. Homebrew installs minipro's runtime dependencies:

```sh
brew install uv minipro
uv python install 3.12
minipro -V
```

There is no udev setup on macOS. For an optional minipro source build, install
Apple's command-line tools and build dependencies first:

```sh
xcode-select --install
brew install pkgconf libusb zlib git
```

Then use the upstream source build instructions. You do not need this source-build
step when using the Homebrew minipro package.

### Linux USB access

Try `minipro -k` as your normal user after connecting the programmer. A “No programmer found”
result means no detected programmer; an access error means permissions need attention.
Use your distribution's minipro USB rules when provided.

For a source installation on a desktop session, run these commands **inside the
matching minipro source checkout**. Both files are needed: one identifies the device,
the other grants the active desktop user access.

```sh
sudo install -m 0644 udev/60-minipro.rules udev/61-minipro-uaccess.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules
sudo udevadm trigger
```

Unplug and reconnect the programmer. For a headless/SSH workstation, use upstream's
`61-minipro-plugdev.rules` policy instead of `61-minipro-uaccess.rules`, and configure
membership in the `plugdev` group:

```sh
sudo groupadd -f plugdev
sudo usermod -aG plugdev "$USER"
sudo install -m 0644 udev/60-minipro.rules udev/61-minipro-plugdev.rules /etc/udev/rules.d/
sudo udevadm control --reload-rules
```

Log out and back in, then reconnect. Use one access policy appropriate to the machine.
Run the UI as your normal user, not with sudo. Newer programmers require rules from
an upstream version that recognizes their USB IDs.

### T56 / T76 algorithm files

Some programmers need vendor FPGA algorithm files in addition to the executable
and device databases. Use **the extraction instructions shipped with your installed
minipro version**; the UI does not download these files. The T48 keeps its algorithms
in the programmer and does not need this extra download.

The upstream extraction script uses bash, bsdtar, a SHA-256 tool, base64, and curl or
wget. Install the extra extraction tools if needed:

```sh
# Fedora
sudo dnf install bash bsdtar coreutils curl

# Debian / Ubuntu
sudo apt install bash libarchive-tools coreutils curl

# macOS (curl and base64 are already present)
brew install bash libarchive coreutils
```

On macOS, put Homebrew's libarchive tools on PATH for the extraction session:

```sh
export PATH="$(brew --prefix libarchive)/bin:$PATH"
```

Run `minipro -V` to find its **Share dir**, then run the installed
`dump-alg-minipro.bash /actual/share/directory` from a writable working directory.
If your package omits the script, use it from the corresponding upstream source.
For 0.7.4 this supports T56; T76 requires a newer upstream implementation and its
matching extraction script. Installation details can change with upstream versions;
consult the [upstream README](https://gitlab.com/DavidGriffith/minipro/-/blob/master/README.md)
and [manual](https://gitlab.com/DavidGriffith/minipro/-/blob/master/man/minipro.1).

## Using the workbench

1. **Programmer:** connect one unit. Its type is detected automatically; selecting
   it unlocks Device setup. Multiple connected units must be reduced to one because
   minipro cannot address a particular unit.
2. **Device setup:** choose ZIF/ICSP and enter a part number or package in the inline
   fuzzy finder. Up/Down previews matches while the search field keeps focus; Enter
   chooses a draft device and focuses **Save**. Save applies the device and
   connection, then opens Operation. Leaving without saving preserves the active
   device and status bar. Tab leaves the finder immediately. Details follow
   keyboard navigation; hovering does not change them.
3. **Operation:** choose the operation. Only applicable file and memory controls
   appear. Review the job and its exact command, then choose **Run job**.
   Use **Cancel** in the running-operation dialog to stop it. If minipro refuses
   an operation because its chip ID differs and explicitly offers `-y`, the
   completed dialog provides **Continue at your own risk**. Selecting it approves
   one retry with `-y`; both attempts remain in the transcript.
4. **Memory browser:** available after a successful code/data read; browse the saved
   bytes in one continuous scrollable view. Scrolling never reads the chip.
   This is a saved snapshot, not live chip memory. In demo mode,
   selecting a memory device also makes a labeled sample buffer available.
5. **Log (minipro):** live raw commands and stdout/stderr, with Save to file at the bottom.
6. **Settings:** configure the minipro executable (blank uses system PATH), an
   optional local folder for device diagrams, and automatic raw-log saving.

The single-line bottom bar shows programmer connection, target selection, and
operation state. The selected chip name appears with `?` because physical presence
is unverified. Shortcut keys use a distinct color from their action labels; narrow
terminals show just the keys to keep the footer within the screen.
Target selection is **not physical chip detection**; presence remains unverified.
Connection checks refresh while the workbench is idle, without interrupting a job.

When a device's minipro metadata names a diagram, Device setup shows a bitmap
preview below its details. The package includes independently drawn reference
images for the minipro names and programmer-specific variants that have been
checked against the corresponding reference material. Leave **Device image
folder** blank to use those bundled references and to search local minipro data
folders. Entering a valid folder makes that folder authoritative and lets you override
the bundled image set with your own matching assets. Invalid folder paths fall
back to the bundled images. minipro supplies the names
and device metadata; it does not supply the bundled artwork. The preview can be
enlarged or opened in the native image viewer.
Settings also offers **Extract from Xgpro**: choose a local installer and an
output folder, and the archive's image files are extracted without running the
installer. The extracted folder becomes the active image folder automatically;
existing files are never overwritten without an explicit extractor option.
The terminal preview depends on Kitty or Sixel bitmap support; the native viewer
remains available when inline graphics are unavailable. The connected programmer
selects the matching variant; unsupported or unmapped references stay metadata
only.
Missing files stay as metadata only and do not offer a folder picker or download.

| Shortcut | Action |
| --- | --- |
| Ctrl+? or Ctrl+/ | Contextual help with a navigation table |
| Ctrl+p | Fuzzy command palette, on every platform |
| Alt+space | Toggle numbered focus hints (disabled while Help is open) |
| Ctrl+q | Quit (cancel a running operation first) |
| Ctrl+k / j / h / l | Up / Down / Left / Right in any control, including text fields |

All other actions are available through commands or UI controls. Tab/Shift+Tab move
focus, arrows navigate, Enter activates, and Escape closes dialogs. A terminal may
intercept Ctrl+?; **Help** is always available through Ctrl+p. Printable `?` is ordinary
text in inputs. Cancelling a write/erase can leave incomplete chip data.

Focus hints dim the screen and label visible controls with numbers. Type a number
to focus a control; use Enter if the number could also begin a longer number.
Escape restores the previous focus. Bare Alt also toggles hints when the terminal
reports modifier keys. Terminals do not reliably report Alt press/release, so this
is a toggle, with Alt+space as the portable fallback, rather than a hold-to-show mode.

There are no file inspection/comparison tools or project recipes.

Operation choices come from the saved device's minipro database record. Unsupported
actions are omitted from both the selector and command palette; memory regions
are filtered as well. Calibration is offered only for reading. The 0.7.4 adapter
reads the installed `infoic.xml`, using the working directory, `MINIPRO_HOME`, or
standard `share/minipro` install locations. If it cannot find capability data,
operations remain unavailable; set `MINIPRO_HOME` to the directory containing the
database used by your minipro installation.

File dialogs update the tree to the nearest existing folder while you type a path.
Tree selections, Parent folder, and Home also update the path field. Left/Right
(or Ctrl+h/l) collapse/expand branches.

Reads save one explicitly selected region, publishing it only after successful
completion. Replacing an existing file requires confirmation in the file dialog;
failed or cancelled reads preserve its contents. Program and Verify operate on a private snapshot whose
SHA-256 matches the reviewed input. The collapsed **Advanced** panel exposes
supported native minipro controls for the selected programmer and device: encoded
read formats, erase and verification behavior, protection, pin and ID checks,
input-size policy, configuration sections, and allowlisted device tuning. Controls
stay within the operation and memory context; arbitrary ranges and custom command
fields are not accepted. Defaults retain minipro's normal behavior, including ID
checks, post-write verification, and exact input-size validation. Encoded reads
are not shown as raw bytes in Memory browser. For supported config sections, leave
all section boxes unchecked to include them all.

**Log (minipro)** contains the actual minipro command arguments and raw decoded
stdout/stderr, with stream labels and exit codes. It includes discovery checks and
hardware commands; routine USB presence polls are omitted so they do not drown
out useful history. UI notifications are not included. Demo mode records clearly
marked simulated commands and output; it never runs minipro.
**Save to file** at the bottom of the always-visible Log (minipro) tab exports the transcript
at any time.

Automatic saving is disabled by default. Enable it in Settings or `settings.json`:

```json
{
  "save_log": true,
  "log_path": "/tmp/minipro_ui_%Y-%m-%d_%H-%M-%S.log"
}
```

The path uses `strftime` placeholders, expanded once with the session start time.
It is a filename pattern, not a shell glob. Enabling saving writes the history so
far, then flushes new command/output chunks as they arrive. Existing log files are
preserved using a numbered suffix on collision. The parent directory must exist;
file errors are reported without interrupting a chip operation.

Preferences are stored using platform-standard config
paths (`~/.config/minipro-ui` on Linux, `~/Library/Application Support/minipro-ui`
on macOS). Demo mode does not persist preferences; it may create explicitly requested
demo backups and exported files, but never accesses a programmer.

## Compatibility and scope

The initial adapter implements minipro's **0.7.4 CLI dialect**. That release advertises
TL866A/CS, TL866II+, T48, and T56; it does not advertise T76. The UI also understands
T76 as a programmer family when the selected backend advertises it.

The app inspects unknown builds but requires `--allow-unverified` to run hardware
jobs with them. Development revisions are distinguished from the 0.7.4 release
commit when `-V` supplies that information. Use the unchanged compatibility suite
below to evaluate a candidate version before opting in. Recognition of a CLI build
is **not** certification of every chip, operation, firmware, or adapter.

Implemented here: the seven operations above, five memory-region selections,
ZIF/powered-ICSP/external-power-ICSP, live catalog metadata, saved-memory browsing,
and raw minipro command logging.
Actual region and operation availability still depends on minipro and the chip.
Unsupported requests remain errors rather than silently bypassing minipro checks.
Only one programmer may be connected for a job; concurrent production programming
is not implemented.

Firmware updating, unrestricted electrical parameter overrides, and automatic SPI
identification are **not exposed in this release**. The bundled diagrams are
informational references generated from transcribed facts. Adapter drawings retain
limits where the reference photos do not identify individual connections.
Device-gated voltage tuning and pin checks appear in Advanced only when the
selected programmer and device provide those native controls; dedicated contact
testing, advanced fuse editors, and NAND/eMMC-specific workflows remain
unavailable.
This UI does not promise the complete Xgpro feature set. In particular, T48 contact
checking/calibration and newer programmer support have upstream limitations.

## Developer setup and build

After installing the prerequisites and opening the checkout:

```sh
uv sync --locked
uv run minipro-ui --demo
uv run minipro-ui
uv build
```

`uv build` produces a wheel and source distribution in `dist/`. To install the wheel:

```sh
uv tool install --python 3.12 dist/minipro_ui-0.1.0-py3-none-any.whl
```

The package includes its terminal stylesheet and a `minipro-ui` console entry point.
Python dependencies are resolved in `uv.lock`; no editable install is needed by users.

The checked-in diagram manifest and renderer can be reproduced with:

```sh
uv run python tools/generate_diagrams.py \
  --manifest tools/diagram_data.json \
  --output src/minipro_ui/assets/diagrams
```

An optional `--reference-dir /path/to/reference-root` argument can check the
external source images during regeneration. The reference root is expected to
contain `legacy/` and `current/` folders matching the manifest groups. The
manifest holds the transcribed facts and source-set-specific
filenames. The optional reference-folder check only confirms that matching
source filenames exist; it does not perform electrical or semantic image
comparison. Some source photographs do not show complete wiring, and those
limits are recorded alongside the image.

To make a local image folder from an Xgpro installer, use the extraction helper.
It reads the installer as an archive and never runs the Windows executable. It
accepts either the installer itself or an outer `.rar`/`.zip` file and writes
only the JPG/JPEG files below its `IMG` directory, preserving their basenames:

```sh
minipro-ui-extract-images \
  /path/to/XgproV1316_Setup.exe \
  --output ~/minipro-images
```

From a source checkout, the equivalent command is `uv run python
tools/extract_xgpro_images.py ...`.

The helper prefers libarchive. macOS's built-in `/usr/bin/tar` works; install
Homebrew's `libarchive` only if that is unavailable. On Debian or Ubuntu,
install `libarchive-tools`; on Fedora, install `bsdtar`. `7z` and `7zz` are
supported fallbacks for archive formats they can decode. Point **Settings →
Device image folder** at the resulting directory. Existing identical files are
skipped. A different file with the same name is left alone unless `--overwrite`
is given.
The helper extracts only image files and does not copy manuals, icons, or other
installer contents.

## Developer tests

Run the default suite, formatter check, and type checker:

```sh
uv run pytest --compat-report compatibility.json
uv run ruff check .
uv run ruff format --check .
uv run mypy src
```

The default suite uses a source-derived CLI simulator and headless Textual interaction
tests. It performs **no hardware operations**. Real executable/hardware cases appear
as UNTESTED unless explicitly configured.

Run the **same executable contracts** against a particular minipro installation:

```sh
uv run pytest tests/contracts/test_executable.py \
  --minipro-bin /absolute/path/to/minipro \
  --programmer T48 \
  --compat-report compatibility.json
```

Repeat `--minipro-bin` to compare builds. All candidates use the same tests and
adapter; unrecognized versions are allowed in this evaluation suite so a new release
can reveal incompatibilities without first editing a supported-version list.
A custom database location can be supplied through minipro's `MINIPRO_HOME` environment
variable. Keep the executable and database version paired.

Hardware testing additionally requires an explicit physical-setup profile. Destructive
cases require `--allow-destructive`; they are never enabled by a normal `uv run pytest`.
See **[the complete testing guide](tests/README.md)** for profile format, version
onboarding, coverage interpretation, and CI details.
