"""Installed console entry point; importing the package never starts the UI."""

from __future__ import annotations

import argparse

from . import __version__


def main() -> None:
    parser = argparse.ArgumentParser(description="A terminal workbench for minipro.")
    parser.add_argument(
        "--version", action="version", version=f"minipro-ui {__version__}"
    )
    parser.add_argument(
        "--minipro", metavar="PATH", help="Installed minipro executable (default: PATH)"
    )
    parser.add_argument(
        "--demo", action="store_true", help="Explore without minipro or USB hardware"
    )
    parser.add_argument(
        "--allow-unverified",
        action="store_true",
        help="Allow hardware jobs with an unverified minipro build",
    )
    args = parser.parse_args()
    from .app import MiniproApp

    MiniproApp(
        demo=args.demo, executable=args.minipro, allow_unverified=args.allow_unverified
    ).run()


if __name__ == "__main__":
    main()
