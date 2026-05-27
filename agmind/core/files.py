"""Filesystem helpers shared by runtime state writers."""

from __future__ import annotations

import os
import stat
from pathlib import Path


def write_text_atomic(
    path: Path,
    content: str,
    *,
    encoding: str = "utf-8",
    mode: int | None = None,
) -> None:
    """Write text through a sibling temp file and atomically replace target.

    When ``mode`` is set (or inherited from an existing target), the temp file
    is *created* with that mode via ``O_CREAT|O_EXCL`` so a secret file
    (e.g. ``mode=0o600``) is never momentarily group/world-readable between
    creation and ``chmod`` — closing the umask race on secret writes.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    file_mode = mode
    if file_mode is None and path.exists():
        file_mode = stat.S_IMODE(path.stat().st_mode)
    tmp.unlink(missing_ok=True)  # clear a stale temp so O_EXCL create is reliable
    try:
        if file_mode is not None:
            # Pre-create with the final mode (O_EXCL) so the file is never
            # momentarily group/world-readable; the subsequent truncating write
            # keeps that mode, and the chmod pins it exactly regardless of umask.
            os.close(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, file_mode))
            tmp.write_text(content, encoding=encoding)
            tmp.chmod(file_mode)
        else:
            tmp.write_text(content, encoding=encoding)
        tmp.replace(path)
    except Exception:
        tmp.unlink(missing_ok=True)
        raise
