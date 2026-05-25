#!/usr/bin/env python3
"""Run aggregate AGmind governance checks."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from agmind.governance import main  # noqa: E402,I001


if __name__ == "__main__":
    raise SystemExit(main())
