#!/usr/bin/env python3
"""Command-line entry point for the factual diagram generator.

The implementation lives in :mod:`minipro_ui.generate_diagrams` so the
validation and rendering routines can also be exercised by the test suite.
"""

from minipro_ui.generate_diagrams import main

if __name__ == "__main__":
    raise SystemExit(main())
