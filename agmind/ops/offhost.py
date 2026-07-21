"""Off-host backup push (SPEC-16.5).

Ship a produced backup archive to remote object storage via the operator's
``rclone`` config. rclone is intentionally NOT a Python dependency: it is a Go
binary the operator installs and configures once (``rclone config`` — S3 / B2 /
SFTP / …). The wrapper fail-fasts with an actionable :class:`OffHostPushError`
when it is absent, exactly like the k6 load-test wrapper
(``agmind.loadtest.k6`` raises ``LoadTestError`` on a missing ``k6``) — never a
traceback.

The remote target is either passed explicitly (``--remote``) or resolved from
the deployment ``.env`` key ``AGMIND_BACKUP_RCLONE_REMOTE`` so a whole
deployment can carry one canonical off-host destination.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Mapping
from pathlib import Path

RCLONE_REMOTE_ENV = "AGMIND_BACKUP_RCLONE_REMOTE"
DEFAULT_INSTALL_DIR = Path("/opt/agmind")


class OffHostPushError(RuntimeError):
    """A managed off-host push failure (rclone missing / no remote / rclone run failed).

    The CLI turns this into an actionable message + non-zero exit, never a traceback
    (mirrors ``agmind.loadtest.k6.LoadTestError``).
    """


def which_rclone() -> str | None:
    """Absolute path to the ``rclone`` binary, or ``None`` if it is not on PATH.

    Seam (monkeypatchable in tests) — rclone is intentionally NOT a Python
    dependency (it is a Go binary the operator installs + configures), so the
    wrapper fail-fasts when it is absent.
    """
    return shutil.which("rclone")


def resolve_remote(
    remote: str | None = None,
    *,
    install_dir: Path = DEFAULT_INSTALL_DIR,
    env: Mapping[str, str] | None = None,
) -> str | None:
    """Resolve the rclone remote target.

    An explicit ``remote`` wins; otherwise the ``AGMIND_BACKUP_RCLONE_REMOTE``
    value from the deployment ``.env`` (parsed from ``install_dir/.env`` when an
    ``env`` mapping is not supplied). Returns ``None`` when neither is set (the
    caller decides whether a missing remote is an error) — never an empty string.
    """
    if remote and remote.strip():
        return remote.strip()
    if env is None:
        from agmind.core.env import parse_env_file_or_empty

        env = parse_env_file_or_empty(Path(install_dir) / ".env")
    value = env.get(RCLONE_REMOTE_ENV, "").strip()
    return value or None


def push_backup(archive_path: Path, remote: str) -> str:
    """Copy ``archive_path`` to ``<remote>/<archive-basename>`` via ``rclone copyto``.

    ``rclone copyto`` copies a single file to a destination *file* path (dest
    includes the basename), so a swapped-in archive never clobbers a sibling on
    the remote. Returns the resolved remote target string ``<remote>/<basename>``.

    Raises :class:`OffHostPushError` (managed, not a traceback) when no remote is
    configured, rclone is missing, the archive does not exist, or ``rclone`` exits
    non-zero. rclone is a Go binary the operator installs + configures via
    ``rclone config`` — never a Python dependency.
    """
    archive_path = Path(archive_path)
    if not remote or not remote.strip():
        raise OffHostPushError(
            "no off-host remote configured. Pass --remote <rclone-remote:path> or set "
            f"{RCLONE_REMOTE_ENV} in the deployment .env (configure it first with "
            "`rclone config`)."
        )
    remote = remote.strip()
    rclone_bin = which_rclone()
    if rclone_bin is None:
        raise OffHostPushError(
            "rclone is not installed (not on PATH). Install + configure it to push backups "
            "off-host: https://rclone.org/install/ then `rclone config` (e.g. an S3/B2/SFTP "
            "remote)."
        )
    if not archive_path.is_file():
        raise OffHostPushError(f"backup archive not found: {archive_path}")

    target = f"{remote.rstrip('/')}/{archive_path.name}"
    argv = [rclone_bin, "copyto", str(archive_path), target]
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, check=False)
    except FileNotFoundError as exc:  # rclone vanished between which() and run()
        raise OffHostPushError(f"failed to execute rclone: {exc}") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()[-400:]
        raise OffHostPushError(
            f"rclone copyto exited non-zero (rc={proc.returncode}): {detail or 'no output'}"
        )
    return target
