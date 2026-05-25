#!/usr/bin/env python3
"""Validate selected-service topology for standard profile lanes."""

from __future__ import annotations

import sys
from collections.abc import Sequence
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))


def main(argv: Sequence[str] | None = None) -> int:
    from agmind.services.topology_checks import main as topology_main

    return topology_main(argv)


if __name__ == "__main__":
    raise SystemExit(main())
