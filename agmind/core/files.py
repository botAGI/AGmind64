"""Filesystem helpers shared by runtime state writers."""

from __future__ import annotations

import stat
from pathlib import Path


def write_text_atomic(
    path: Path,
    content: str,
    *,
    encoding: str = "utf-8",
    mode: int | None = None,
) -> None:
    """Write text through a sibling temp file and atomically replace target."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    file_mode = mode
    if file_mode is None and path.exists():
        file_mode = stat.S_IMODE(path.stat().st_mode)
    try:
        tmp.write_text(content, encoding=encoding)
        if file_mode is not None:
            tmp.chmod(file_mode)
        tmp.replace(path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
