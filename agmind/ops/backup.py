"""Phase L.E: backup / restore deployment config + state.

Что бэкапится по default:
    /opt/agmind/docker-compose.yml         — rendered compose
    /opt/agmind/.env                       — runtime env and generated service secrets
    /opt/agmind/templates/services/        — descriptor snapshot (если есть)
    ~/.local/share/agmind/setup-state.json — wizard state (без CF token)
    ~/.local/share/agmind/schema.json      — migrations applied (Phase L.D)
    /var/lib/agmind/snapshots/             — deploy snapshots (Phase L.B)

Что НЕ бэкапится:
    /var/lib/agmind/models/                — GGUF файлы (гигабайты)
    docker volumes (qdrant/grafana/...)    — требует stop containers, отдельный flow
    ~/.local/share/agmind/cf_dns_api_token — secret, копировать руками

Формат: gzipped tar (.tar.gz) с metadata.json + tree файлов под их absolute-like
именами (с заменой `/` → `__` для безопасности).
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath

from agmind.core.files import write_text_atomic
from agmind.core.logging import logger
from agmind.core.proc import sudo_argv, sudo_stdin_bytes, sudo_stdin_text
from agmind.ops.backup_data import (
    DataVolumeSource,
    DbDumpSource,
    dump_to_gzip,
    restore_db_command,
)

log = logger(__name__)

DEFAULT_INSTALL_DIR = Path("/opt/agmind")
DEFAULT_USER_DIR = Path.home() / ".local" / "share" / "agmind"
DEFAULT_SYSTEM_DIR = Path("/var/lib/agmind")

METADATA_FILENAME = "agmind-backup.json"
BACKUP_FORMAT_VERSION = 1

# D-06 (Phase 13): write-side of the stateful-apply data-backup guard. There is no canonical
# backup-archive directory in this codebase (``--output`` is always caller-chosen), so a
# successful ``agmind backup --include-data`` stamps this lightweight marker at the install
# dir root instead — the deploy guard reads it (`data_backup_is_fresh`) to prove a recent data
# backup happened before recreating a stateful service. Non-secret (0644), atomic write.
DATA_BACKUP_MARKER_NAME = ".agmind-last-data-backup.json"

# SPEC-17.4: optional at-rest encryption of the finished archive with `age`. age is a small Go
# binary the operator installs (https://github.com/FiloSottile/age) — intentionally NOT a Python
# dependency, exactly like rclone (off-host push) and k6 (load test). A missing binary raises a
# managed BackupEncryptError with an actionable message, never a traceback.
_AGE_MISSING_MSG = (
    "age not found — install age (https://github.com/FiloSottile/age) or drop --encrypt."
)


class BackupEncryptError(RuntimeError):
    """A managed at-rest-encryption failure (age missing / no recipient / age run failed).

    The CLI turns this into an actionable message + non-zero exit, never a traceback
    (mirrors ``agmind.ops.offhost.OffHostPushError``).
    """


def which_age() -> str | None:
    """Absolute path to the ``age`` binary, or ``None`` if it is not on PATH.

    Seam (monkeypatchable in tests) — age is intentionally NOT a Python dependency
    (it is a Go binary the operator installs), so the encrypt path fail-fasts when
    it is absent, mirroring ``agmind.ops.offhost.which_rclone``.
    """
    return shutil.which("age")


@dataclass(frozen=True)
class BackupSource:
    """One path to include в backup."""

    label: str  # short id записываемый в metadata (e.g. "compose")
    path: Path  # actual filesystem path
    optional: bool = True  # если False и path отсутствует — backup fails


@dataclass(frozen=True)
class BackupResult:
    """Outcome of `create_backup`."""

    output_path: Path
    bytes_written: int
    sources_included: tuple[str, ...]
    sources_missing: tuple[str, ...]


@dataclass(frozen=True)
class RestoreResult:
    """Outcome of `restore_backup`."""

    extracted: tuple[str, ...]
    metadata: dict[str, object]
    failed: tuple[str, ...] = ()  # members that could NOT be restored (DB rc!=0, corrupt, no dest)


@dataclass(frozen=True)
class PlanRow:
    """One row of a read-only restore plan (`restore_plan`)."""

    label: str
    kind: str  # "file" | "dir" | "data" | "missing"
    target: str  # resolved destination path ("" if unknown)
    detail: str  # size / child count / data kind


def default_sources(
    install_dir: Path = DEFAULT_INSTALL_DIR,
    user_dir: Path = DEFAULT_USER_DIR,
    system_dir: Path = DEFAULT_SYSTEM_DIR,
) -> list[BackupSource]:
    return [
        BackupSource("compose", install_dir / "docker-compose.yml"),
        BackupSource("env", install_dir / ".env"),
        BackupSource("versions", install_dir / "version.env"),
        BackupSource("descriptors", install_dir / "templates" / "services"),
        BackupSource("setup_state", user_dir / "setup-state.json"),
        BackupSource("schema_state", user_dir / "schema.json"),
        BackupSource("snapshots", system_dir / "snapshots"),
    ]


def volume_restore_target(label: str, system_dir: Path = DEFAULT_SYSTEM_DIR) -> Path | None:
    """Trusted destination for a ``volume/<relpath>`` data member.

    The label suffix is the data dir's path RELATIVE to ``/var/lib/agmind`` (set by
    ``backup_data.data_sources``); re-root it under the operator's ``system_dir``. This is the
    ONLY trusted source of the destination — the archive's self-declared ``host_path`` is
    attacker-controllable (audit H#4) and is never used. Any traversal / absolute / empty
    suffix returns None so a malicious label can never escape ``system_dir``.
    """
    if not label.startswith("volume/"):
        return None
    rel = label[len("volume/") :]
    relpath = PurePosixPath(rel)
    if not rel or relpath.is_absolute() or ".." in relpath.parts:
        return None
    return system_dir / rel


def write_data_backup_marker(install_dir: Path, archive: Path, sha256: str) -> None:
    """Stamp ``<install_dir>/.agmind-last-data-backup.json`` after a successful
    ``--include-data`` backup (D-06 write side). Records ``written_at`` (UTC ISO),
    the produced ``archive`` path, and its ``sha256`` — non-secret metadata, so the
    marker is written 0644 via the same atomic primitive as other runtime state.
    """
    marker = {
        "written_at": datetime.now(UTC).isoformat(),
        "archive": str(archive),
        "sha256": sha256,
    }
    write_text_atomic(
        Path(install_dir) / DATA_BACKUP_MARKER_NAME,
        json.dumps(marker, indent=2),
        mode=0o644,
    )


def read_data_backup_marker(install_dir: Path) -> dict[str, object] | None:
    """Best-effort load of the data-backup marker. NEVER raises — a missing or
    corrupt marker just means "no fresh data backup known" (fail-closed for the
    deploy guard), mirroring ``load_prior_setup_state``'s never-raise shape.
    """
    path = Path(install_dir) / DATA_BACKUP_MARKER_NAME
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    return payload


def data_backup_is_fresh(install_dir: Path, window_hours: int = 24) -> bool:
    """True when the last ``--include-data`` backup marker is younger than
    ``window_hours`` (D-06 default 24h). False for a missing marker, a corrupt
    marker, or an unparseable ``written_at`` — never raises (fail-closed: the
    deploy guard treats "unknown" the same as "not fresh").
    """
    marker = read_data_backup_marker(install_dir)
    if marker is None:
        return False
    written_at = marker.get("written_at")
    if not isinstance(written_at, str):
        return False
    try:
        written = datetime.fromisoformat(written_at)
    except ValueError:
        return False
    if written.tzinfo is None:
        written = written.replace(tzinfo=UTC)
    return datetime.now(UTC) - written <= timedelta(hours=window_hours)


def _safe_member_name(label: str) -> str:
    """Имя архивного file/dir. Lowercase, no slashes."""
    return label.replace("/", "_").replace("..", "_")


def _run_sudo_capture_bytes(args: list[str], sudo_password: str) -> bytes:
    result = subprocess.run(
        sudo_argv(args),
        capture_output=True,
        check=False,
        input=sudo_stdin_bytes(sudo_password),
    )
    if result.returncode != 0:
        stderr = result.stderr.decode(errors="replace").strip()
        raise OSError(f"sudo command failed ({args[0]}): {stderr or result.returncode}")
    return result.stdout


def _run_sudo_no_output(args: list[str], sudo_password: str) -> None:
    result = subprocess.run(
        sudo_argv(args),
        capture_output=True,
        text=True,
        check=False,
        input=sudo_stdin_text(sudo_password),
    )
    if result.returncode != 0:
        stderr = result.stderr.strip()
        raise OSError(f"sudo command failed ({args[0]}): {stderr or result.returncode}")


def _sensitive_member_mode(label: str, path: Path | None = None) -> int:
    if label == "env" or (path is not None and path.name == ".env"):
        return 0o600
    return 0o644


def _restored_member_mode(member_name: str) -> int:
    name = PurePosixPath(member_name).name
    if name == ".env" or name == "env.snapshot" or name.endswith(".env.snapshot"):
        return 0o600
    return 0o644


def _add_bytes_member(
    tar: tarfile.TarFile,
    arcname: str,
    payload: bytes,
    mode: int,
) -> None:
    info = tarfile.TarInfo(name=arcname)
    info.size = len(payload)
    info.mode = mode
    info.mtime = int(datetime.now(UTC).timestamp())
    tar.addfile(info, io.BytesIO(payload))


def _safe_tar_relpath(name: str, context: str = "tar stream") -> str:
    rel = name.removeprefix("./").strip("/")
    path = PurePosixPath(rel)
    if not rel or path.is_absolute() or ".." in path.parts:
        raise ValueError(f"unsafe member in {context}: {name}")
    return rel


def _raise_unsupported_backup_member(name: str) -> None:
    raise ValueError(f"unsupported backup source member: {name}")


def _metadata_string_list(
    payload: dict[object, object], key: str, *, required: bool = True
) -> list[str]:
    if key not in payload:
        if required:
            raise ValueError(f"{METADATA_FILENAME} metadata {key} must be a list of strings")
        return []
    value = payload[key]
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{METADATA_FILENAME} metadata {key} must be a list of strings")
    return list(value)


def _add_local_directory(tar: tarfile.TarFile, src_path: Path, arcname: str) -> list[str]:
    """Add a directory tree to the archive; return the member names of any SKIPPED symlinks.

    Symlinks are skipped (not stored), not aborted on: the produced archive stays symlink-free —
    identical restore safety to the old hard reject (a restore can never be escaped via a stored
    symlink) — but a single link (e.g. the dify-plugin-daemon uv-cache symlinks) no longer aborts
    the whole ``--include-data`` backup, which made it unusable on a real live stack. Genuine
    non-file/non-dir members (device/fifo) still raise — they cannot be backed up meaningfully and
    should never appear under a data dir.
    """
    if src_path.is_symlink() or not src_path.is_dir():
        _raise_unsupported_backup_member(str(src_path))
    tar.add(src_path, arcname=arcname, recursive=False)
    skipped: list[str] = []
    for item in sorted(src_path.rglob("*")):
        rel = item.relative_to(src_path).as_posix()
        member_name = f"{arcname}/{rel}"
        if item.is_symlink():
            skipped.append(member_name)
            continue
        if item.is_dir():
            tar.add(item, arcname=member_name, recursive=False)
        elif item.is_file():
            tar.add(item, arcname=member_name, recursive=False)
        else:
            _raise_unsupported_backup_member(member_name)
    return skipped


def _add_sudo_directory(
    tar: tarfile.TarFile,
    src_path: Path,
    arcname: str,
    sudo_password: str,
) -> list[str]:
    """Add a root-owned directory (via ``sudo tar``) to the archive; return skipped symlinks.

    Spools the ``sudo tar -cf -`` output to a temp FILE on disk instead of buffering the entire
    dir tar in memory: a multi-GB minio/elasticsearch/milvus volume held whole in RAM
    (capture_output + BytesIO) risks OOM on a unified-memory host. Symlinks are skipped (same
    contract as :func:`_add_local_directory`); genuine device/fifo members still raise.
    """
    top = tarfile.TarInfo(name=arcname)
    top.type = tarfile.DIRTYPE
    top.mode = 0o755
    top.mtime = int(datetime.now(UTC).timestamp())
    tar.addfile(top)

    skipped: list[str] = []
    # sudo tar runs as root but writes to the user-owned spool fd (fd 1), so the spool file stays
    # user-owned; the password goes in on stdin via communicate (no pipe deadlock — stdout is the
    # file, not a pipe we must drain concurrently).
    with tempfile.NamedTemporaryFile(prefix=".agmind-backup-spool-", suffix=".tar") as spool:
        proc = subprocess.Popen(
            sudo_argv(["tar", "-C", str(src_path), "-cf", "-", "."]),
            stdin=subprocess.PIPE,
            stdout=spool,
            stderr=subprocess.PIPE,
        )
        _, stderr = proc.communicate(input=sudo_stdin_bytes(sudo_password))
        if proc.returncode != 0:
            detail = (stderr or b"").decode(errors="replace").strip()
            raise OSError(f"sudo command failed (tar {src_path}): {detail or proc.returncode}")
        spool.flush()
        spool.seek(0)
        with tarfile.open(fileobj=spool, mode="r:") as src_tar:
            for member in src_tar.getmembers():
                if member.name in {".", "./"}:
                    continue
                rel = _safe_tar_relpath(member.name)
                if member.issym() or member.islnk():
                    skipped.append(f"{arcname}/{rel}")
                    continue
                if not (member.isdir() or member.isfile()):
                    _raise_unsupported_backup_member(f"{arcname}/{rel}")
                member.name = f"{arcname}/{rel}"
                extracted = src_tar.extractfile(member) if member.isfile() else None
                tar.addfile(member, extracted)
    return skipped


def _encrypt_with_age(archive: Path, recipient: str) -> Path:
    """Wrap a finalized backup archive with ``age -r <recipient>`` (SPEC-17.4).

    Produces ``<archive>.age`` (0600) and removes the plaintext archive so an encrypt run never
    leaves the secrets-bearing .tar.gz on disk. age writes to a temp sibling which is then
    atomically replaced into place — same atomic-artifact contract as the plaintext path. A
    missing ``age`` binary (TOCTOU vs. the caller's fail-fast check) or a non-zero age exit
    raises :class:`BackupEncryptError`, never a traceback. age is a Go binary the operator
    installs — never a Python dependency.
    """
    age_bin = which_age()
    if age_bin is None:
        raise BackupEncryptError(_AGE_MISSING_MSG)
    encrypted = archive.with_name(archive.name + ".age")
    tmp = archive.with_name(f".{archive.name}.age.tmp")
    _cleanup_path(tmp)
    argv = [age_bin, "-r", recipient, "-o", str(tmp), str(archive)]
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, check=False)
    except FileNotFoundError as exc:  # age vanished between which_age() and run()
        _cleanup_path(tmp)
        raise BackupEncryptError(f"failed to execute age: {exc}") from exc
    if proc.returncode != 0:
        _cleanup_path(tmp)
        detail = (proc.stderr or proc.stdout or "").strip()[-400:]
        raise BackupEncryptError(
            f"age exited non-zero (rc={proc.returncode}): {detail or 'no output'}"
        )
    tmp.chmod(0o600)
    tmp.replace(encrypted)
    # Only drop the plaintext once the encrypted artifact is safely in place.
    archive.unlink(missing_ok=True)
    return encrypted


def create_backup(
    output_path: Path,
    sources: list[BackupSource] | None = None,
    sudo_password: str | None = None,
    *,
    data_sources: list[DataVolumeSource | DbDumpSource] | None = None,
    data_run: Callable[..., subprocess.CompletedProcess[bytes]] | None = None,
    encrypt: bool = False,
    age_recipient: str | None = None,
) -> BackupResult:
    """Create tar.gz backup at output_path. Returns BackupResult.

    ``data_sources`` (from ``agmind.ops.backup_data.data_sources``) add a *data tier*: DB logical
    dumps (stored gzipped as ``<label>.sql.gz`` members) and ``/var/lib/agmind/*`` volume dirs.
    Each is recorded in metadata ``data`` with its kind (+ sha256 for dumps) for verify/restore.

    ``encrypt`` (SPEC-17.4): after the plaintext archive is finalized, wrap it with
    ``age -r <age_recipient>`` so the artifact becomes ``<output_path>.age`` (0600) and the
    plaintext is removed. Requires ``age_recipient`` and the ``age`` binary — both are checked
    UP FRONT so a missing recipient/binary fails before any (possibly multi-GB) archive is
    written, raising :class:`BackupEncryptError` rather than a traceback.
    """
    if sources is None:
        sources = default_sources()

    recipient = (age_recipient or "").strip()
    if encrypt:
        # Fail fast BEFORE building the archive: never write a plaintext .tar.gz we then cannot
        # encrypt (the whole point of --encrypt is to not leave secrets in the clear on disk).
        if not recipient:
            raise BackupEncryptError(
                "--encrypt requires an age recipient (--age-recipient age1...)."
            )
        if which_age() is None:
            raise BackupEncryptError(_AGE_MISSING_MSG)

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    included: list[str] = []
    missing: list[str] = []
    skipped_symlinks: list[str] = []
    tmp_path = output_path.with_name(f".{output_path.name}.tmp")
    _cleanup_path(tmp_path)

    try:
        with tarfile.open(tmp_path, "w:gz") as tar:
            for src in sources:
                if src.path.is_symlink():
                    _raise_unsupported_backup_member(str(src.path))
                if not src.path.exists():
                    missing.append(src.label)
                    if not src.optional:
                        raise FileNotFoundError(
                            f"required backup source missing: {src.label} ({src.path})"
                        )
                    continue
                arcname = _safe_member_name(src.label)
                log.info("backup: adding %s (%s) as %s", src.label, src.path, arcname)
                if sudo_password is not None and src.path.is_file():
                    payload = _run_sudo_capture_bytes(["cat", str(src.path)], sudo_password)
                    _add_bytes_member(
                        tar,
                        arcname,
                        payload,
                        mode=_sensitive_member_mode(src.label, src.path),
                    )
                elif sudo_password is not None and src.path.is_dir():
                    skipped_symlinks.extend(
                        _add_sudo_directory(tar, src.path, arcname, sudo_password)
                    )
                elif src.path.is_dir():
                    skipped_symlinks.extend(_add_local_directory(tar, src.path, arcname))
                elif src.path.is_file():
                    tar.add(src.path, arcname=arcname, recursive=False)
                else:
                    _raise_unsupported_backup_member(str(src.path))
                included.append(src.label)

            data_members: list[dict[str, str]] = []
            for ds in data_sources or []:
                arcname = _safe_member_name(ds.label)
                if isinstance(ds, DbDumpSource):
                    payload = dump_to_gzip(ds, run=data_run or subprocess.run)
                    arcname = f"{arcname}.sql.gz"
                    _add_bytes_member(tar, arcname, payload, mode=0o600)
                    data_members.append(
                        {
                            "label": ds.label,
                            "arcname": arcname,
                            "kind": "dbdump",
                            "engine": ds.engine,
                            "container": ds.container,
                            "user": ds.user,
                            "database": ds.database,
                            "globals_only": "1" if ds.globals_only else "",
                            "sha256": hashlib.sha256(payload).hexdigest(),
                        }
                    )
                else:  # DataVolumeSource — tar the host data dir
                    if ds.host_path.is_symlink() or not ds.host_path.is_dir():
                        _raise_unsupported_backup_member(str(ds.host_path))
                    if sudo_password is not None:
                        skipped_symlinks.extend(
                            _add_sudo_directory(tar, ds.host_path, arcname, sudo_password)
                        )
                    else:
                        skipped_symlinks.extend(_add_local_directory(tar, ds.host_path, arcname))
                    data_members.append(
                        {
                            "label": ds.label,
                            "arcname": arcname,
                            "kind": "volume",
                            "host_path": str(ds.host_path),
                        }
                    )
                included.append(ds.label)

            metadata = {
                "format_version": BACKUP_FORMAT_VERSION,
                "created_at": datetime.now(UTC).isoformat(),
                "included": included,
                "missing": missing,
                "data": data_members,
                "sources": [
                    {"label": s.label, "path": str(s.path), "optional": s.optional} for s in sources
                ],
            }
            meta_bytes = json.dumps(metadata, indent=2, ensure_ascii=False).encode("utf-8")
            info = tarfile.TarInfo(name=METADATA_FILENAME)
            info.size = len(meta_bytes)
            info.mtime = int(datetime.now(UTC).timestamp())
            tar.addfile(info, io.BytesIO(meta_bytes))
        if skipped_symlinks:
            # Not silent (Правила / "No silent caps"): the archive intentionally omits these
            # symlinks; the operator must know coverage was bounded.
            log.warning(
                "backup: skipped %d symlink(s) (not stored — archive stays symlink-safe): %s%s",
                len(skipped_symlinks),
                ", ".join(skipped_symlinks[:8]),
                " ..." if len(skipped_symlinks) > 8 else "",
            )
        tmp_path.chmod(0o600)
        tmp_path.replace(output_path)
        output_path.chmod(0o600)
    except Exception:
        _cleanup_path(tmp_path)
        raise

    if encrypt:
        # Plaintext archive is finalized (0600) — wrap it at rest with age, yielding
        # <output_path>.age (0600) and dropping the plaintext. output_path now points at the
        # encrypted artifact so BackupResult / off-host push / marker all reference the .age file.
        # Fail-closed: if age fails (vanished binary / bad recipient), remove the plaintext too —
        # an encrypt run must NEVER leave the secrets-bearing .tar.gz behind for the caller to
        # mistake for an encrypted artifact.
        try:
            output_path = _encrypt_with_age(output_path, recipient)
        except BackupEncryptError:
            _cleanup_path(output_path)
            raise

    size = output_path.stat().st_size
    return BackupResult(
        output_path=output_path,
        bytes_written=size,
        sources_included=tuple(included),
        sources_missing=tuple(missing),
    )


def read_metadata(backup_path: Path) -> dict[str, object]:
    """Open backup file, read agmind-backup.json metadata. Raises on missing."""
    try:
        with tarfile.open(backup_path, "r:gz") as tar:
            try:
                member = tar.getmember(METADATA_FILENAME)
            except KeyError as exc:
                raise ValueError(
                    f"{backup_path} is not an agmind backup (no {METADATA_FILENAME})"
                ) from exc
            if not member.isfile():
                raise ValueError(
                    f"unsupported metadata member type in backup archive: {member.name}"
                )
            f = tar.extractfile(member)
            if f is None:
                raise ValueError(f"cannot extract {METADATA_FILENAME} from {backup_path}")
            payload = json.loads(f.read().decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError(f"{METADATA_FILENAME} metadata payload must be an object")
            format_version = payload.get("format_version")
            if format_version != BACKUP_FORMAT_VERSION:
                raise ValueError(
                    "unsupported backup format version: "
                    f"{format_version!r} (expected {BACKUP_FORMAT_VERSION})"
                )
            _metadata_string_list(payload, "included")
            _metadata_string_list(payload, "missing", required=False)
            return dict(payload)
    except tarfile.TarError as exc:
        raise ValueError(f"invalid backup archive: {backup_path} ({exc})") from exc


def list_backups(directory: Path) -> list[dict[str, object]]:
    """List agmind ``*.tar.gz`` archives in ``directory``, newest first.

    Each entry carries cheap, non-secret metadata read from the archive's
    ``agmind-backup.json`` (no member contents are extracted, so no secret VALUES
    are read): name, path, byte size, mtime, and — when the archive is a valid
    agmind backup — its ``created_at`` / ``format_version`` / ``included`` labels
    and ``data`` member count. A non-agmind / corrupt ``.tar.gz`` is reported with
    ``ok=False`` and a short ``error`` reason rather than crashing the listing.

    Sort key is the archive's recorded ``created_at`` when available, falling back
    to the file mtime (so corrupt archives still sort sensibly), newest first.
    """
    directory = Path(directory)
    if not directory.is_dir():
        return []

    entries: list[dict[str, object]] = []
    for path in sorted(directory.glob("*.tar.gz")):
        if not path.is_file():
            continue
        try:
            size = path.stat().st_size
            mtime = path.stat().st_mtime
        except OSError as exc:
            entries.append(
                {
                    "name": path.name,
                    "path": str(path),
                    "size_bytes": 0,
                    "mtime": 0.0,
                    "created_at": None,
                    "ok": False,
                    "error": str(exc),
                    "included": [],
                    "data_members": 0,
                    "_sort": 0.0,
                }
            )
            continue

        entry: dict[str, object] = {
            "name": path.name,
            "path": str(path),
            "size_bytes": size,
            "mtime": mtime,
        }
        try:
            metadata = read_metadata(path)
        except (ValueError, OSError, EOFError, tarfile.TarError) as exc:
            # A truncated/corrupt gzip surfaces as EOFError / tarfile.ReadError (neither is an
            # OSError), so without these one bad archive would crash the whole listing and hide
            # every good backup instead of marking just the bad one [CORRUPT] (mirror verify_backup).
            entry.update(
                {
                    "created_at": None,
                    "ok": False,
                    "error": str(exc),
                    "included": [],
                    "data_members": 0,
                    "_sort": mtime,
                }
            )
            entries.append(entry)
            continue

        created_at = metadata.get("created_at")
        created_at_str = str(created_at) if isinstance(created_at, str) else None
        included_raw = metadata.get("included", [])
        included = [str(x) for x in included_raw] if isinstance(included_raw, list) else []
        raw_data = metadata.get("data", [])
        data_members = len(raw_data) if isinstance(raw_data, list) else 0
        format_version = metadata.get("format_version")
        sort_key = _created_at_sort_key(created_at_str, mtime)
        entry.update(
            {
                "created_at": created_at_str,
                "format_version": format_version,
                "included": included,
                "data_members": data_members,
                "ok": True,
                "error": None,
                "_sort": sort_key,
            }
        )
        entries.append(entry)

    entries.sort(key=lambda e: e["_sort"], reverse=True)  # type: ignore[arg-type,return-value]
    for entry in entries:
        entry.pop("_sort", None)
    return entries


def _created_at_sort_key(created_at: str | None, mtime: float) -> float:
    """Epoch seconds for sorting: parsed ``created_at`` ISO timestamp, else file mtime."""
    if created_at:
        try:
            return datetime.fromisoformat(created_at).timestamp()
        except ValueError:
            pass
    return mtime


def restore_plan(
    backup_path: Path,
    install_dir: Path = DEFAULT_INSTALL_DIR,
    user_dir: Path = DEFAULT_USER_DIR,
    system_dir: Path = DEFAULT_SYSTEM_DIR,
    labels: list[str] | None = None,
) -> list[PlanRow]:
    """Read-only plan of what ``restore_backup`` would do — never mutates anything.

    One row per included label (filtered to ``labels`` when given), classifying the
    archive member (file/dir/data) and resolving the destination from
    ``default_sources``. Used by ``agmind restore --dry-run``.
    """
    backup_path = Path(backup_path)
    metadata = read_metadata(backup_path)
    included_raw = metadata.get("included", [])
    included = [str(x) for x in included_raw] if isinstance(included_raw, list) else []
    by_label = {s.label: s for s in default_sources(install_dir, user_dir, system_dir)}
    raw_data = metadata.get("data", [])
    data_meta = (
        {str(m.get("label")): m for m in raw_data if isinstance(m, dict)}
        if isinstance(raw_data, list)
        else {}
    )
    wanted = set(labels) if labels else None

    rows: list[PlanRow] = []
    with tarfile.open(backup_path, "r:gz") as tar:
        names = {m.name: m for m in tar.getmembers()}
        for label in included:
            if wanted is not None and label not in wanted:
                continue
            target = str(by_label[label].path) if label in by_label else ""
            if label in data_meta:
                kind = str(data_meta[label].get("kind", "data"))
                if kind == "volume" and not target:
                    vol_target = volume_restore_target(label, system_dir)
                    target = str(vol_target) if vol_target is not None else ""
                rows.append(PlanRow(label, "data", target, kind))
                continue
            arcname = _safe_member_name(label)
            member = names.get(arcname)
            if member is not None and member.isfile():
                rows.append(PlanRow(label, "file", target, f"{member.size} bytes"))
                continue
            children = [m for n, m in names.items() if n.startswith(f"{arcname}/") and m.isfile()]
            if children or (member is not None and member.isdir()):
                total = sum(c.size for c in children)
                rows.append(PlanRow(label, "dir", target, f"{len(children)} files, {total} bytes"))
            else:
                rows.append(PlanRow(label, "missing", target, "not in archive"))
    return rows


def verify_backup(backup_path: Path) -> list[str]:
    """Return integrity issues (empty list = OK).

    Checks: the file exists, the archive opens, metadata is valid, and every data member that
    recorded a sha256 (DB dumps) still hashes to that value. A corrupt gzip surfaces as a
    metadata/archive error. Prerequisite for DR — corruption was previously undetected until restore.
    """
    backup_path = Path(backup_path)
    if not backup_path.exists():
        return [f"backup file not found: {backup_path}"]
    try:
        metadata = read_metadata(backup_path)
    except (ValueError, OSError, EOFError, tarfile.TarError) as exc:
        # A truncated/corrupt gzip surfaces here as EOFError ("Compressed file ended before the
        # end-of-stream marker") — catch it so a corrupt backup is REPORTED, not raised (a DR
        # integrity checker must never crash on the very corruption it exists to detect).
        return [f"metadata: {exc}"]

    raw = metadata.get("data", [])
    data = [m for m in raw if isinstance(m, dict)] if isinstance(raw, list) else []
    issues: list[str] = []
    try:
        with tarfile.open(backup_path, "r:gz") as tar:
            for member_meta in data:
                expected = member_meta.get("sha256")
                if not expected:
                    continue
                arcname = str(member_meta.get("arcname", ""))
                label = member_meta.get("label", arcname)
                try:
                    member = tar.getmember(arcname)
                except KeyError:
                    issues.append(f"{label}: member {arcname} missing from archive")
                    continue
                handle = tar.extractfile(member)
                if handle is None:
                    issues.append(f"{label}: member {arcname} unreadable")
                    continue
                if hashlib.sha256(handle.read()).hexdigest() != expected:
                    issues.append(f"{label}: sha256 mismatch (corrupt)")
    except (tarfile.TarError, EOFError, OSError) as exc:
        issues.append(f"archive: {exc}")
    return issues


def restore_backup(
    backup_path: Path,
    destinations: dict[str, Path] | None = None,
    sources: list[BackupSource] | None = None,
    sudo_password: str | None = None,
    *,
    db_passwords: dict[str, str] | None = None,
    data_run: Callable[..., subprocess.CompletedProcess[bytes]] | None = None,
    labels: list[str] | None = None,
) -> RestoreResult:
    """Extract backup into filesystem.

    Args:
        backup_path: путь к .tar.gz
        destinations: override {label: target_path}. Если label не указан —
            используется path из default_sources/sources.
        sources: для тестов — list of BackupSource (default = default_sources()).
        labels: when given, restore ONLY these labels — scopes BOTH the config members and
            the data members (a selective ``--label env`` must not replay DB dumps / volumes).

    Returns RestoreResult с metadata + extracted + failed labels.
    """
    backup_path = Path(backup_path)
    if not backup_path.exists():
        raise FileNotFoundError(f"backup file not found: {backup_path}")

    if sources is None:
        sources = default_sources()
    by_label = {s.label: s for s in sources}
    if destinations is None:
        destinations = {}
    wanted = set(labels) if labels else None

    metadata = read_metadata(backup_path)
    extracted: list[str] = []
    failed: list[str] = []
    included_labels = metadata.get("included", [])
    if not isinstance(included_labels, list):
        included_labels = []
    raw_data_members = metadata.get("data", [])
    data_members = (
        [m for m in raw_data_members if isinstance(m, dict)]
        if isinstance(raw_data_members, list)
        else []
    )
    data_labels = {str(m.get("label")) for m in data_members}

    with tarfile.open(backup_path, "r:gz") as tar:
        for label in included_labels:
            if str(label) in data_labels:
                continue  # data members are restored by kind below, not as config files
            if wanted is not None and str(label) not in wanted:
                continue
            arcname = _safe_member_name(str(label))
            try:
                member = tar.getmember(arcname)
            except KeyError:
                log.warning("backup: %s in metadata but missing in archive", label)
                continue
            target = destinations.get(str(label)) or (
                by_label[str(label)].path if str(label) in by_label else None
            )
            if target is None:
                log.warning("no destination known for label %s — skipping", label)
                continue

            target = Path(target)
            if sudo_password is None:
                target.parent.mkdir(parents=True, exist_ok=True)

            if member.isdir():
                _extract_dir(tar, member, target, sudo_password=sudo_password)
            else:
                _extract_file(
                    tar,
                    member,
                    target,
                    mode=_sensitive_member_mode(str(label), target),
                    sudo_password=sudo_password,
                )
            extracted.append(str(label))

        for member_meta in data_members:
            kind = member_meta.get("kind")
            arcname = str(member_meta.get("arcname", ""))
            dlabel = str(member_meta.get("label", arcname))
            if wanted is not None and dlabel not in wanted:
                continue  # selective --label scopes the data loop too (review M restore-label)
            try:
                member = tar.getmember(arcname)
            except KeyError:
                log.warning("backup: data member %s missing in archive", dlabel)
                failed.append(dlabel)
                continue
            if kind == "volume":
                # SECURITY (audit H#4): resolve the extraction root ONLY from trusted sources —
                # the operator's `destinations` mapping or `by_label` (current default sources).
                # NEVER fall back to the archive's self-declared `host_path`, which is
                # attacker-controllable: a swapped .tar.gz could set host_path=/root/.ssh and
                # overwrite files anywhere the user can write, outside /var/lib/agmind.
                target_path = destinations.get(dlabel) or (
                    by_label[dlabel].path if dlabel in by_label else None
                )
                if target_path is None:
                    log.error(
                        "backup: volume %s has no trusted destination (not in --label "
                        "destinations nor the current sources) — NOT restored (untrusted archive "
                        "host_path ignored)",
                        dlabel,
                    )
                    failed.append(dlabel)
                    continue
                _extract_dir(tar, member, Path(target_path), sudo_password=sudo_password)
                extracted.append(dlabel)
            elif kind == "dbdump":
                payload = tar.extractfile(member)
                if payload is None:
                    log.error("cannot read dbdump member %s — NOT restored", dlabel)
                    failed.append(dlabel)
                    continue
                raw = payload.read()
                # Integrity gate (audit L#33): verify the dump against the recorded sha256
                # (of the gzipped member, same basis as verify_backup) BEFORE piping it into
                # the live DB — a bit-rotted/truncated dump must not be loaded.
                expected_sha = str(member_meta.get("sha256", ""))
                if expected_sha and hashlib.sha256(raw).hexdigest() != expected_sha:
                    log.error(
                        "backup: dbdump %s sha256 mismatch (corrupt/truncated) — refusing to load",
                        dlabel,
                    )
                    failed.append(dlabel)
                    continue
                sql = gzip.decompress(raw)
                src = DbDumpSource(
                    label=dlabel,
                    container=str(member_meta.get("container", "")),
                    engine=str(member_meta.get("engine", "")),
                    user=str(member_meta.get("user", "")),
                    database=str(member_meta.get("database", "")),
                    password=(db_passwords or {}).get(dlabel, ""),
                    globals_only=bool(member_meta.get("globals_only")),
                )
                runner = data_run or subprocess.run
                result = runner(restore_db_command(src), input=sql, check=False)
                rc = getattr(result, "returncode", 0)
                if rc not in (0, None):
                    log.error("backup: dbdump %s restore command failed (rc=%s)", dlabel, rc)
                    failed.append(dlabel)
                else:
                    extracted.append(dlabel)

    return RestoreResult(extracted=tuple(extracted), metadata=metadata, failed=tuple(failed))


def _extract_file(
    tar: tarfile.TarFile,
    member: tarfile.TarInfo,
    target: Path,
    mode: int | None = None,
    sudo_password: str | None = None,
) -> None:
    if not member.isfile():
        raise ValueError(f"unsupported file member type in backup archive: {member.name}")
    f = tar.extractfile(member)
    if f is None:
        log.warning("cannot extract file member %s", member.name)
        return
    payload = f.read()
    if sudo_password is not None:
        _sudo_install_bytes(payload, target, mode or 0o644, sudo_password)
        log.info("restore: wrote %s via sudo", target)
        return
    _write_bytes_atomic(target, payload, mode=mode)
    log.info("restore: wrote %s", target)


def _write_bytes_atomic(target: Path, payload: bytes, mode: int | None = None) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = target.with_name(f".{target.name}.tmp")
    _cleanup_path(tmp_path)
    try:
        tmp_path.write_bytes(payload)
        if mode is not None:
            tmp_path.chmod(mode)
        tmp_path.replace(target)
    except Exception:
        _cleanup_path(tmp_path)
        raise


def _sudo_install_bytes(
    payload: bytes,
    target: Path,
    mode: int,
    sudo_password: str,
) -> None:
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        tmp.write(payload)
        tmp_path = Path(tmp.name)
    try:
        os.chmod(tmp_path, 0o600)
        _run_sudo_no_output(
            ["install", "-D", "-m", f"{mode:04o}", str(tmp_path), str(target)],
            sudo_password,
        )
    finally:
        tmp_path.unlink(missing_ok=True)


def _cleanup_path(path: Path) -> None:
    if path.is_symlink():
        path.unlink(missing_ok=True)
        return
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
        return
    path.unlink(missing_ok=True)


def _replace_path_atomic(staged: Path, target: Path) -> None:
    backup = target.with_name(f".{target.name}.rollback")
    _cleanup_path(backup)
    try:
        if target.exists():
            target.replace(backup)
        staged.replace(target)
    except Exception:
        if backup.exists() and not target.exists():
            backup.replace(target)
        raise
    finally:
        _cleanup_path(backup)


def _replace_path_via_sudo(staged: Path, target: Path, sudo_password: str) -> None:
    backup = target.with_name(f".{target.name}.rollback")
    _run_sudo_no_output(
        [
            "sh",
            "-c",
            """
set -eu
target=$1
staged=$2
backup=$3
rm -rf --one-file-system "$backup"
if [ -e "$target" ]; then
    mv "$target" "$backup"
fi
if mv "$staged" "$target"; then
    rm -rf --one-file-system "$backup"
    exit 0
fi
if [ -e "$backup" ] && [ ! -e "$target" ]; then
    mv "$backup" "$target"
fi
exit 1
""",
            "agmind-restore-directory",
            str(target),
            str(staged),
            str(backup),
        ],
        sudo_password,
    )


def _extract_dir(
    tar: tarfile.TarFile,
    top: tarfile.TarInfo,
    target_root: Path,
    sudo_password: str | None = None,
) -> None:
    """Extract directory member и всех её children в target_root."""
    if sudo_password is None:
        staged_root = target_root.with_name(f".{target_root.name}.tmp")
        _cleanup_path(staged_root)
        try:
            staged_root.mkdir(parents=True, exist_ok=True)
            _extract_dir_members(tar, top, staged_root, sudo_password=None)
            _replace_path_atomic(staged_root, target_root)
        except Exception:
            _cleanup_path(staged_root)
            raise
        log.info("restore: extracted directory %s -> %s", top.name, target_root)
        return

    staged_root = target_root.with_name(f".{target_root.name}.tmp")
    rollback_root = target_root.with_name(f".{target_root.name}.rollback")
    _run_sudo_no_output(["rm", "-rf", "--one-file-system", str(staged_root)], sudo_password)
    _run_sudo_no_output(["rm", "-rf", "--one-file-system", str(rollback_root)], sudo_password)
    try:
        _run_sudo_no_output(
            ["install", "-d", "-m", "0755", str(staged_root)],
            sudo_password,
        )
        _extract_dir_members(tar, top, staged_root, sudo_password=sudo_password)
        _replace_path_via_sudo(staged_root, target_root, sudo_password)
    except Exception:
        try:
            _run_sudo_no_output(
                ["rm", "-rf", "--one-file-system", str(staged_root)],
                sudo_password,
            )
        except OSError:
            pass
        raise
    log.info("restore: extracted directory %s -> %s", top.name, target_root)


def _extract_dir_members(
    tar: tarfile.TarFile,
    top: tarfile.TarInfo,
    target_root: Path,
    sudo_password: str | None = None,
) -> None:
    prefix = top.name.rstrip("/") + "/"
    for m in tar.getmembers():
        if m.name == top.name:
            continue
        if not m.name.startswith(prefix):
            continue
        rel = _safe_tar_relpath(m.name[len(prefix) :], context="backup archive")
        out_path = target_root / rel
        if m.isdir():
            if sudo_password is None:
                out_path.mkdir(parents=True, exist_ok=True)
            else:
                _run_sudo_no_output(
                    ["install", "-d", "-m", "0755", str(out_path)],
                    sudo_password,
                )
        elif m.isfile():
            f = tar.extractfile(m)
            if f is None:
                continue
            mode = _restored_member_mode(m.name)
            if sudo_password is None:
                _write_bytes_atomic(out_path, f.read(), mode=mode)
            else:
                _sudo_install_bytes(f.read(), out_path, mode, sudo_password)
        else:
            raise ValueError(f"unsupported member type in backup archive: {m.name}")
