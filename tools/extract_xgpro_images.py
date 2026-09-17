#!/usr/bin/env python3
"""CLI wrapper for the Xgpro image extraction API."""

from __future__ import annotations

import sys

from minipro_ui.xgpro_images import main

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
