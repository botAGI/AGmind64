#!/usr/bin/env python3
"""Run Kubernetes server-side dry-run proof for AGmind deployment targets."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from agmind.services.kubernetes_dry_run import main  # noqa: E402,I001

__all__ = ["main"]


if __name__ == "__main__":
    raise SystemExit(main())
