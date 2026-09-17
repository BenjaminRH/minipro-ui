#!/usr/bin/env python3
"""Source-derived CLI simulator, NOT a recording or a hardware emulator.

The 0.7.4 command forms originate in upstream's tagged main.c/man page. Failure
modes deliberately include malformed output and misleading success-looking text.
"""

import json
import os
import sys
import time
from pathlib import Path

args = sys.argv[1:]
mode = os.environ.get("MINIPRO_TEST_MODE", "")


def encoded_read(format_name, size=32768):
    data = b"\xff" * size
    lines = []
    for address in range(0, size, 16):
        chunk = data[address : address + 16]
        if format_name == "ihex":
            payload = bytes((len(chunk), address >> 8, address & 0xFF, 0)) + chunk
            lines.append(f":{payload.hex().upper()}{(-sum(payload)) & 0xFF:02X}")
        else:
            payload = bytes((len(chunk) + 3, address >> 8, address & 0xFF)) + chunk
            lines.append(f"S1{payload.hex().upper()}{(~sum(payload)) & 0xFF:02X}")
    lines.append(":00000001FF" if format_name == "ihex" else "S9030000FC")
    return "\n".join(lines) + "\n"


if capture := os.environ.get("MINIPRO_TEST_ARGV"):
    with open(capture, "a") as destination:
        destination.write(json.dumps(args) + "\n")
if mode == "timeout":
    time.sleep(60)
if mode == "failure":
    print("Verification OK (earlier stage)", file=sys.stderr)
    print("Unexpected device failure", file=sys.stderr)
    sys.exit(7)
if "-V" in args:
    print(
        "minipro version " + os.environ.get("MINIPRO_TEST_VERSION", "0.7.4"),
        file=sys.stderr,
    )
    print("Git commit:\t3808aecb6a1dac9906a9691b93820ee1bd2b7a18", file=sys.stderr)
elif "-Q" in args:
    print("tl866a: TL866CS/A\ntl866ii: TL866II+\nt48: T48\nt56: T56", file=sys.stderr)
elif "-k" in args:
    if mode == "disconnected":
        print("[No programmer found]", file=sys.stderr)
    elif mode == "multiple":
        print("t48: T48\nt48: T48", file=sys.stderr)
    else:
        print("t48: T48", file=sys.stderr)
elif "-l" in args:
    print("warning: fixture diagnostic", file=sys.stderr)
    print("AT28C256@DIP28\nW25Q32JV@SOIC8\n7400\n82HS641B(custom)")
elif "-d" in args:
    name = args[args.index("-d") + 1]
    if name == "NOT_A_REAL_CHIP" or mode == "bad_metadata":
        print("Device not found!", file=sys.stderr)
        sys.exit(1 if mode != "bad_metadata" else 0)
    print(f"Name: {name}\nPackage: DIP28", file=sys.stderr)
    print(
        "Vector count: 4" if name == "7400" else "Memory: 32768 Bytes", file=sys.stderr
    )
else:
    if mode in {"id_mismatch", "id_mismatch_refusal"} and "-y" not in args:
        print(
            "Invalid Chip ID: expected 0xEF3011, got 0x0000 (unknown)",
            file=sys.stderr,
        )
        print("(use '-y' to continue anyway at your own risk)", file=sys.stderr)
        sys.exit(1)
    if "-r" in args and mode != "missing_output":
        destination = Path(args[args.index("-r") + 1])
        if "-f" in args and args[args.index("-f") + 1] == "ihex":
            destination.write_text(encoded_read("ihex"))
        elif "-f" in args and args[args.index("-f") + 1] == "srec":
            destination.write_text(encoded_read("srec"))
        else:
            destination.write_bytes(b"\xff" * 32768)
    print("Reading... 0%\rReading... 50%\rReading... 100%", file=sys.stderr)
    print("Operation completed", file=sys.stderr)
