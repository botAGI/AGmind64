#!/usr/bin/env python3
# ruff: noqa: E402, I001
"""Run a non-destructive backup/restore smoke against root-owned temp paths."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from agmind.ops.root_owned_backup_smoke import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
