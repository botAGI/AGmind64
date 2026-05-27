"""Helpers for invoking Ansible tools from packaged AGmind entrypoints."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path


def resolve_ansible_command(name: str) -> str:
    """Resolve an Ansible console script from the current Python env first.

    Running `/path/to/.venv/bin/agmind` does not guarantee that `.venv/bin`
    is present in PATH. Prefer the executable next to `sys.executable`, then
    fall back to PATH, then return the raw name so subprocess reports a clear
    OS error.
    """

    local = Path(sys.executable).parent / name
    if local.exists() and os.access(local, os.X_OK):
        return str(local)
    return shutil.which(name) or name
