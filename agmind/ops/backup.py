"""Phase L.E: backup / restore deployment config + state.

Что бэкапится по default:
    /opt/agmind/docker-compose.yml         — rendered compose
    /opt/agmind/.env                       — env (без secrets если cf token в файле)
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

import json
import tarfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from agmind.log import logger

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
        BackupSource("descriptors", install_dir / "templates" / "services"),
        BackupSource("setup_state", user_dir / "setup-state.json"),
        BackupSource("schema_state", user_dir / "schema.json"),
        BackupSource("snapshots", system_dir / "snapshots"),
    ]


def _safe_member_name(label: str) -> str:
    """Имя архивного file/dir. Lowercase, no slashes."""
    return label.replace("/", "_").replace("..", "_")


def create_backup(
    output_path: Path,
    sources: list[BackupSource] | None = None,
) -> BackupResult:
    """Create tar.gz backup at output_path. Returns BackupResult."""
    if sources is None:
        sources = default_sources()

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    included: list[str] = []
    missing: list[str] = []

    with tarfile.open(output_path, "w:gz") as tar:
        for src in sources:
            if not src.path.exists():
                missing.append(src.label)
                if not src.optional:
                    raise FileNotFoundError(
                        f"required backup source missing: {src.label} ({src.path})"
                    )
                continue
            arcname = _safe_member_name(src.label)
            log.info("backup: adding %s (%s) as %s", src.label, src.path, arcname)
            tar.add(src.path, arcname=arcname, recursive=True)
            included.append(src.label)

        metadata = {
            "format_version": BACKUP_FORMAT_VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "included": included,
            "missing": missing,
            "sources": [
                {"label": s.label, "path": str(s.path), "optional": s.optional}
                for s in sources
            ],
        }
        meta_bytes = json.dumps(metadata, indent=2, ensure_ascii=False).encode("utf-8")
        info = tarfile.TarInfo(name=METADATA_FILENAME)
        info.size = len(meta_bytes)
        info.mtime = int(datetime.now(timezone.utc).timestamp())
        import io

        tar.addfile(info, io.BytesIO(meta_bytes))

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
        f = tar.extractfile(member)
        if f is None:
            raise ValueError(f"cannot extract {METADATA_FILENAME} from {backup_path}")
        return json.loads(f.read().decode("utf-8"))


def restore_backup(
    backup_path: Path,
    destinations: dict[str, Path] | None = None,
    sources: list[BackupSource] | None = None,
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

    with tarfile.open(backup_path, "r:gz") as tar:
        for label in metadata.get("included", []):
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
            target.parent.mkdir(parents=True, exist_ok=True)

            if member.isdir():
                _extract_dir(tar, member, target)
            else:
                _extract_file(tar, member, target)
            extracted.append(str(label))

    return RestoreResult(extracted=tuple(extracted), metadata=metadata)


def _extract_file(tar: tarfile.TarFile, member: tarfile.TarInfo, target: Path) -> None:
    f = tar.extractfile(member)
    if f is None:
        log.warning("cannot extract file member %s", member.name)
        return
    target.write_bytes(f.read())
    log.info("restore: wrote %s", target)


def _extract_dir(tar: tarfile.TarFile, top: tarfile.TarInfo, target_root: Path) -> None:
    """Extract directory member и всех её children в target_root."""
    target_root.mkdir(parents=True, exist_ok=True)
    prefix = top.name.rstrip("/") + "/"
    for m in tar.getmembers():
        if m.name == top.name:
            continue
        if not m.name.startswith(prefix):
            continue
        rel = m.name[len(prefix):]
        out_path = target_root / rel
        if m.isdir():
            out_path.mkdir(parents=True, exist_ok=True)
        else:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            f = tar.extractfile(m)
            if f is None:
                continue
            out_path.write_bytes(f.read())
    log.info("restore: extracted directory %s -> %s", top.name, target_root)
