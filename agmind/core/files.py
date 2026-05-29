"""Filesystem helpers shared by runtime state writers."""

from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path


def write_text_atomic(
    path: Path,
    content: str,
    *,
    encoding: str = "utf-8",
    mode: int | None = None,
) -> None:
    """Write text through a unique temp file and durably, atomically replace target.

    Durability + atomicity contract:

    - The temp file is created with :func:`tempfile.mkstemp` in the *target*
      directory under a unique random name, so concurrent/sequential writers
      never collide on a fixed temp name.
    - The content is written through the temp fd, then ``os.fsync``'d before the
      replace, so the data is on disk before it becomes visible at ``path``.
    - ``os.replace`` performs the atomic rename, and the parent directory is
      then ``os.fsync``'d (best-effort) so the rename itself is durable across a
      crash — guarded for filesystems where directory fsync is unsupported.

    Secret-mode invariant: ``mkstemp`` creates the temp with ``0o600`` (safe for
    secrets under any umask). When ``mode`` is set (or inherited from an existing
    target), the temp is ``chmod``'d to that exact mode *before* the replace, so
    a secret file (e.g. ``mode=0o600``) is never momentarily group/world-readable
    and a non-secret file lands with the right mode — independent of umask.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    file_mode = mode
    if file_mode is None and path.exists():
        file_mode = stat.S_IMODE(path.stat().st_mode)

    # Unique temp in the TARGET dir so os.replace is a same-filesystem atomic
    # rename and concurrent writers never collide on a fixed name. mkstemp
    # creates with 0o600 (umask-independent), which is the secret-safe default.
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding=encoding) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())  # data durable before it becomes visible
        # Pin the final mode exactly (umask-independent) before the replace, so a
        # secret never widens and a non-secret inherits the right mode.
        if file_mode is not None:
            os.chmod(tmp, file_mode)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise

    # Best-effort: fsync the parent directory so the rename is durable across a
    # crash. Not portable to every filesystem, so failures are non-fatal.
    try:
        dir_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except OSError:
        pass
