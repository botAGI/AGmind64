"""Advisory cross-process locks shared by every mutating day-2 operation.

Lives at the lowest shared layer (imports only the standard library) so both
the deploy engine (``agmind.deploy.runner``) and the ops CLI
(``agmind.cli.ops_cmd``) can take the same lock without a backwards import
(ops importing the deploy engine, or vice versa).

Lock-file hardening (moving off the shared ``/tmp`` namespace to
``XDG_RUNTIME_DIR``, ``0600`` permissions, ``O_NOFOLLOW``) is out of scope
here — tracked separately; this module reuses the existing flock verbatim.
"""

from __future__ import annotations

import fcntl
import hashlib
import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from agmind.core.logging import logger

log = logger(__name__)


@contextmanager
def deploy_lock(install_dir: Path) -> Iterator[bool]:
    """Advisory single-flight lock around a deploy apply, keyed on *install_dir*.

    Two concurrent `docker compose up` on the same project race to create the same
    container names (the `/agmind-watchtower` Conflict). The TUI guard stops in-app
    re-entry; this `flock` additionally serialises across PROCESSES (e.g. `agmind
    deploy` started while the installer is mid-deploy). Yields True if acquired, False
    if another deploy already holds it. The lock file lives under the system temp dir
    (always writable by the invoking user, unlike root-owned /opt/agmind).
    """
    digest = hashlib.sha256(str(install_dir.resolve()).encode("utf-8")).hexdigest()[:16]
    lock_path = Path(tempfile.gettempdir()) / f"agmind-deploy-{digest}.lock"
    fd: int | None = None
    try:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o666)
        except OSError as exc:
            # Cannot create the lock file — do not block the deploy on lock infra.
            log.debug("deploy lock unavailable (%s); proceeding without it", exc)
            yield True
            return
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            yield False
            return
        yield True
    finally:
        if fd is not None:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
            os.close(fd)
