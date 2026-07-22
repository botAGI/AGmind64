"""Advisory cross-process locks shared by every mutating day-2 operation.

Lives at the lowest shared layer (imports only the standard library) so both
the deploy engine (``agmind.deploy.runner``) and the ops CLI
(``agmind.cli.ops_cmd``) can take the same lock without a backwards import
(ops importing the deploy engine, or vice versa).

Lock-file hardening: the lock lives under ``XDG_RUNTIME_DIR`` (per-user,
mode 0700) when set, else a stable per-uid fallback dir under the system
temp — never the shared, world-writable ``/tmp`` root directly. The lock
file itself is opened ``0600`` with ``O_NOFOLLOW`` so a pre-planted symlink
at the predictable path is rejected (OSError) rather than followed, and any
local user other than the owner can no longer flock it to force a
denial-of-service on every deploy/rollback/restore/rotate-secrets.
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


def _lock_dir() -> Path:
    """Resolve the directory the deploy lock file lives in.

    Prefers ``XDG_RUNTIME_DIR`` (per-user, normally mode 0700, set by
    systemd-logind for an interactive session). When unset — the common case
    in CI/non-interactive contexts — falls back to a STABLE per-uid directory
    under the system temp dir, created once (``exist_ok=True``) rather than a
    fresh ``tempfile.mkdtemp()`` per call: two calls in the same process (or
    two concurrent processes for the same uid) must resolve to the identical
    path, or the single-flight lock never actually contends.
    """
    xdg = os.environ.get("XDG_RUNTIME_DIR")
    if xdg:
        return Path(xdg)
    fallback = Path(tempfile.gettempdir()) / f"agmind-runtime-{os.getuid()}"
    fallback.mkdir(mode=0o700, exist_ok=True)
    return fallback


@contextmanager
def deploy_lock(install_dir: Path) -> Iterator[bool]:
    """Advisory single-flight lock around a deploy apply, keyed on *install_dir*.

    Two concurrent `docker compose up` on the same project race to create the same
    container names (the `/agmind-watchtower` Conflict). The TUI guard stops in-app
    re-entry; this `flock` additionally serialises across PROCESSES (e.g. `agmind
    deploy` started while the installer is mid-deploy). Yields True if acquired, False
    if another deploy already holds it. The lock file lives under `_lock_dir()`
    (XDG_RUNTIME_DIR, or a stable per-uid fallback — always writable by the
    invoking user, unlike root-owned /opt/agmind, and never the shared /tmp root).
    """
    digest = hashlib.sha256(str(install_dir.resolve()).encode("utf-8")).hexdigest()[:16]
    lock_path = _lock_dir() / f"agmind-deploy-{digest}.lock"
    fd: int | None = None
    try:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        except PermissionError as exc:
            # EACCES: the lock file exists but we can't open it — almost always a stale lock left
            # root:root by a prior `sudo -E` op (rotate-secrets/restore run elevated), since our
            # own runs create it 0600 as the invoking uid. Proceeding (yield True) would silently
            # disable single-flight and let two concurrent deploys race the same container names
            # (#24). Fail CLOSED — treat it as held — and log how to clear it, rather than turn a
            # data-corrupting race into a "no lock" no-op.
            log.warning(
                "deploy lock at %s is not accessible (%s) — likely a stale lock from a prior "
                "`sudo` op; refusing rather than deploy without single-flight. Clear it and "
                "retry: sudo rm -f %s",
                lock_path,
                exc.strerror,
                lock_path,
            )
            yield False
            return
        except OSError as exc:
            # Other errors (missing runtime dir, ENOSPC, …) are lock-infra failures, not
            # contention — do not block the deploy on lock infra.
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
