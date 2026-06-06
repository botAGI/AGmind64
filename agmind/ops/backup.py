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
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from agmind.core.logging import logger
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


def _safe_member_name(label: str) -> str:
    """Имя архивного file/dir. Lowercase, no slashes."""
    return label.replace("/", "_").replace("..", "_")


def _sudo_bytes(sudo_password: str) -> bytes:
    return f"{sudo_password}\n".encode()


def _sudo_text(sudo_password: str) -> str:
    return f"{sudo_password}\n"


def _run_sudo_capture_bytes(args: list[str], sudo_password: str) -> bytes:
    cmd = ["sudo", "-S", "-p", "", "--", *args]
    result = subprocess.run(
        cmd,
        capture_output=True,
        check=False,
        input=_sudo_bytes(sudo_password),
    )
    if result.returncode != 0:
        stderr = result.stderr.decode(errors="replace").strip()
        raise OSError(f"sudo command failed ({args[0]}): {stderr or result.returncode}")
    return result.stdout


def _run_sudo_no_output(args: list[str], sudo_password: str) -> None:
    cmd = ["sudo", "-S", "-p", "", "--", *args]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=False,
        input=_sudo_text(sudo_password),
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


def _add_local_directory(tar: tarfile.TarFile, src_path: Path, arcname: str) -> None:
    if src_path.is_symlink() or not src_path.is_dir():
        _raise_unsupported_backup_member(str(src_path))
    tar.add(src_path, arcname=arcname, recursive=False)
    for item in sorted(src_path.rglob("*")):
        rel = item.relative_to(src_path).as_posix()
        member_name = f"{arcname}/{rel}"
        if item.is_symlink():
            _raise_unsupported_backup_member(member_name)
        if item.is_dir():
            tar.add(item, arcname=member_name, recursive=False)
        elif item.is_file():
            tar.add(item, arcname=member_name, recursive=False)
        else:
            _raise_unsupported_backup_member(member_name)


def _add_sudo_directory(
    tar: tarfile.TarFile,
    src_path: Path,
    arcname: str,
    sudo_password: str,
) -> None:
    payload = _run_sudo_capture_bytes(
        ["tar", "-C", str(src_path), "-cf", "-", "."],
        sudo_password,
    )
    top = tarfile.TarInfo(name=arcname)
    top.type = tarfile.DIRTYPE
    top.mode = 0o755
    top.mtime = int(datetime.now(UTC).timestamp())
    tar.addfile(top)

    with tarfile.open(fileobj=io.BytesIO(payload), mode="r:") as src_tar:
        for member in src_tar.getmembers():
            if member.name in {".", "./"}:
                continue
            rel = _safe_tar_relpath(member.name)
            if not (member.isdir() or member.isfile()):
                _raise_unsupported_backup_member(f"{arcname}/{rel}")
            member.name = f"{arcname}/{rel}"
            extracted = src_tar.extractfile(member) if member.isfile() else None
            tar.addfile(member, extracted)


def create_backup(
    output_path: Path,
    sources: list[BackupSource] | None = None,
    sudo_password: str | None = None,
    *,
    data_sources: list[DataVolumeSource | DbDumpSource] | None = None,
    data_run: Callable[..., subprocess.CompletedProcess[bytes]] | None = None,
) -> BackupResult:
    """Create tar.gz backup at output_path. Returns BackupResult.

    ``data_sources`` (from ``agmind.ops.backup_data.data_sources``) add a *data tier*: DB logical
    dumps (stored gzipped as ``<label>.sql.gz`` members) and ``/var/lib/agmind/*`` volume dirs.
    Each is recorded in metadata ``data`` with its kind (+ sha256 for dumps) for verify/restore.
    """
    if sources is None:
        sources = default_sources()

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    included: list[str] = []
    missing: list[str] = []
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
                    _add_sudo_directory(tar, src.path, arcname, sudo_password)
                elif src.path.is_dir():
                    _add_local_directory(tar, src.path, arcname)
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
                        _add_sudo_directory(tar, ds.host_path, arcname, sudo_password)
                    else:
                        _add_local_directory(tar, ds.host_path, arcname)
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
        tmp_path.chmod(0o600)
        tmp_path.replace(output_path)
        output_path.chmod(0o600)
    except Exception:
        _cleanup_path(tmp_path)
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
    except (ValueError, OSError) as exc:
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
    except tarfile.TarError as exc:
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
