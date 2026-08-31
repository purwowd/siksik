#!/usr/bin/env python3
"""CLI wrapper — delegates ke sd_detector.cli."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from sd_detector.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
