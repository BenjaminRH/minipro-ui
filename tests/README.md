# Shared compatibility tests

There is one suite, not a copied suite for each minipro release. A new binary is a
new test target. Run the contracts first, inspect failures, and change only the
adapter when a behavior actually differs. Retain new regression cases afterward.

## Default software checks

```sh
uv sync --locked
uv run pytest --compat-report compatibility.json
uv run ruff check .
uv run ruff format --check .
uv run mypy src
```

The suite covers:

- Command contracts for every exposed operation, all three connection modes, and
  the five memory-region selections. These tests exercise argv construction; they
  do not claim that every operation/region combination is meaningful on hardware.
- Release/revision recognition, candidate-version opt-in, programmer lists, catalog
  decoration, device metadata, unknown formats, missing executables, and failures.
- Preservation of failed exit codes even when earlier diagnostics contain “OK”.
- File snapshots, post-review changes, exact binary sizes, atomic backup publication,
  missing output, overwrite races, invalid requests, and wrong programmers.
- Actual subprocess streaming, separate stdout/stderr, carriage-return progress,
  large output, closed stdin, timeouts, cancellation and child-process reaping.
- Textual interaction at multiple terminal sizes, help with text-input focus,
  help on every dialog, selection, review/cancel, and a complete demo backup.
- Preference discovery, raw subprocess history, automatic log saving, and keyboard journeys.

`fixtures/minipro_fake.py` is a **source-derived simulator**, not captured hardware
output. Its command forms are based on upstream tag `0.7.4`, commit
`3808aecb6a1dac9906a9691b93820ee1bd2b7a18`. The suite runs it through real subprocesses.
It is not proof of device support. Regression fixtures may be added without copying
any test suite; label actual captures with executable/revision, programmer/firmware,
chip, adapter, operation, and capture origin.

## Check a real executable, including a future version

```sh
uv run pytest tests/contracts/test_executable.py \
  --minipro-bin /path/to/minipro \
  --programmer T48 \
  --compat-report compatibility.json
```

Repeat `--minipro-bin` for multiple targets. These tests invoke the candidate's
version, programmer list, device catalog, device metadata and invalid-device error
path. They do not read, write, erase or test a chip. minipro may enumerate attached
USB devices as part of its metadata commands.

For a custom source build, point `MINIPRO_HOME` at the directory containing the
**matching** `infoic.xml` and `logicic.xml`. That variable also applies to the UI.
Test another programmer's catalog by changing `--programmer` to TL866A, TL866II,
T56, or T76. A version that does not advertise that model reports UNSUPPORTED.

Unknown candidate versions use the current adapter dialect intentionally: they
are not skipped merely because they are new. Any parser or semantic mismatch fails
normally. A green metadata run does not establish hardware-operation compatibility.

## Hardware contracts

Use a disposable or backed-up test chip. Connect exactly one programmer. The profile
describes the physical setup and independent expected results, not a minipro version.
For example, create `lab.json` beside a known, exact-size `reference.bin`:

```json
{
  "programmer": "T48",
  "cases": [
    {
      "operation": "READ",
      "device": "AT28C256@DIP28",
      "memory": "code",
      "interface": "zif",
      "expected_sha256": "REPLACE_WITH_THE_INDEPENDENTLY_KNOWN_CHIP_CONTENT_HASH"
    },
    {
      "operation": "VERIFY",
      "device": "AT28C256@DIP28",
      "input": "reference.bin"
    },
    {
      "operation": "WRITE",
      "device": "AT28C256@DIP28",
      "input": "reference.bin"
    }
  ]
}
```

Use the **exact device spelling from that backend's catalog**, including a package
suffix if the catalog uses one. The example is illustrative, not a guarantee of a
particular entry name. Input paths are relative to the profile. Available operation
keys are READ, WRITE, VERIFY, BLANK, ERASE, ID and TEST. `expected_exit` defaults to
zero. Set it explicitly for a known failing comparison/blank check.

```sh
uv run pytest tests/contracts/test_hardware.py \
  --minipro-bin /absolute/path/to/minipro \
  --hardware-profile lab.json \
  --compat-report hardware-report.json
```

WRITE, ERASE and TEST additionally require `--allow-destructive` (RAM tests can
change contents). The dedicated flag is required even when selecting those tests:

```sh
uv run pytest tests/contracts/test_hardware.py -k WRITE \
  --minipro-bin /absolute/path/to/minipro \
  --hardware-profile lab.json --allow-destructive
```

Profiles require exactly one executable target. Prepare the chip's expected state
before each run; do not rely on test order. Use `-k READ`, `-k ERASE`, etc. for scenarios
that require different chips or initial contents. In particular, an erase changes
later read/verify expectations. READ compares the saved data with an independently
known SHA-256, and a successful WRITE is followed by explicit verification. Add
multiple cases per operation for other regions or expected failures. Operations
omitted from the physical setup remain UNTESTED, not passed or unsupported.

## Interpreting the report

Pytest reports PASSED / FAILED / UNTESTED / UNSUPPORTED in its summary and optional
JSON file. The JSON identifies each test and supplied binary (path and SHA-256).
JUnit output (`--junitxml=results.xml`) also includes release/revision properties
from the executable version test. Unexpected skips count as UNTESTED. Unknown
versions and malformed output do not become “unsupported” automatically.

Software tests, real executable tests and hardware tests have separate module paths
so reports retain their evidence level. Hardware that was not present is never
counted as validated. Any test failure makes pytest exit nonzero.

## Adding a minipro version

1. Obtain the executable and its matching device/algorithm data.
2. Run the existing executable contracts unchanged for relevant programmer families.
3. Run applicable lab profiles against available hardware.
4. Investigate failures. Put text/flag differences in `backend.py`, never screens.
5. Add focused regression fixtures for discovered differences.
6. Extend version recognition only after reviewing the results; document hardware
   coverage separately. A new dialect can implement the same `Backend` protocol.

CI runs software tests on Linux/macOS with Python 3.12/3.14 and an unmodified 0.7.4
metadata check on Linux. It does not claim physical hardware coverage.
