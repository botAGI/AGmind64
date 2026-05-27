#!/usr/bin/env python3
"""Validate AGmind Proxmox exporter config and optionally probe the exporter."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from agmind.deploy.proxmox_exporter import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
