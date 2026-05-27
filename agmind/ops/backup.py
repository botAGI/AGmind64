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

import io
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from agmind.core.logging import logger

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
) -> BackupResult:
    """Create tar.gz backup at output_path. Returns BackupResult."""
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

            metadata = {
                "format_version": BACKUP_FORMAT_VERSION,
                "created_at": datetime.now(UTC).isoformat(),
                "included": included,
                "missing": missing,
                "sources": [
                    {"label": s.label, "path": str(s.path), "optional": s.optional} for s in sources
                ],
            }
            meta_bytes = json.dumps(metadata, indent=2, ensure_ascii=False).encode("utf-8")
            info = tarfile.TarInfo(name=METADATA_FILENAME)
            info.size = len(meta_bytes)
            info.mtime = int(datetime.now(UTC).timestamp())
            tar.addfile(info, io.BytesIO(meta_bytes))
        tmp_path.replace(output_path)
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
    with tarfile.open(backup_path, "r:gz") as tar:
        try:
            member = tar.getmember(METADATA_FILENAME)
        except KeyError as exc:
            raise ValueError(
                f"{backup_path} is not an agmind backup (no {METADATA_FILENAME})"
            ) from exc
        if not member.isfile():
            raise ValueError(f"unsupported metadata member type in backup archive: {member.name}")
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


def restore_backup(
    backup_path: Path,
    destinations: dict[str, Path] | None = None,
    sources: list[BackupSource] | None = None,
    sudo_password: str | None = None,
) -> RestoreResult:
    """Extract backup into filesystem.

    Args:
        backup_path: путь к .tar.gz
        destinations: override {label: target_path}. Если label не указан —
            используется path из default_sources/sources.
        sources: для тестов — list of BackupSource (default = default_sources()).

    Returns RestoreResult с metadata + extracted labels.
    """
    backup_path = Path(backup_path)
    if not backup_path.exists():
        raise FileNotFoundError(f"backup file not found: {backup_path}")

    if sources is None:
        sources = default_sources()
    by_label = {s.label: s for s in sources}
    if destinations is None:
        destinations = {}

    metadata = read_metadata(backup_path)
    extracted: list[str] = []
    included_labels = metadata.get("included", [])
    if not isinstance(included_labels, list):
        included_labels = []

    with tarfile.open(backup_path, "r:gz") as tar:
        for label in included_labels:
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

    return RestoreResult(extracted=tuple(extracted), metadata=metadata)


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
            if sudo_password is None:
                _write_bytes_atomic(out_path, f.read(), mode=0o644)
            else:
                _sudo_install_bytes(f.read(), out_path, 0o644, sudo_password)
        else:
            raise ValueError(f"unsupported member type in backup archive: {m.name}")
