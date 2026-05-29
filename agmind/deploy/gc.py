"""Garbage Collection subsystem (Phase L.C).

Решает user pain "stale images/volumes/containers едят диск через месяц".
Auto-prune'ить безопасно — все production data в named volumes с label
`agmind.gc=keep`, временные volumes label'ятся `agmind.gc=auto` рендерером.

Operations:
    - containers: stopped/dead containers
    - images: dangling + старше cutoff (default 72h)
    - volumes: anonymous + labeled `agmind.gc=auto`
    - networks: unused (не привязанные ни к одному контейнеру)
    - models: GGUF файлы в /var/lib/agmind/models не упомянутые в service
              descriptors, rendered model env defaults, or model catalog

CLI: `agmind gc [--auto] [--aggressive] [--dry-run]`.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from agmind.core.logging import logger

log = logger(__name__)

_MODEL_SUFFIXES = (".gguf", ".safetensors", ".bin")
_MODEL_FILENAME_RE = re.compile(r"([A-Za-z0-9_.][^/\\\s:{}$\"']*\.(?:gguf|safetensors|bin))")


@dataclass
class GcReport:
    """Outcome of one GC operation."""

    target: str
    """`containers` | `images` | `volumes` | `networks` | `models` | `snapshots`"""

    items_removed: int = 0
    bytes_freed: int = 0
    error: str | None = None
    dry_run: bool = False
    items: list[str] = field(default_factory=list)
    """Specific items affected (для verbose output)."""


def _docker_available() -> bool:
    return shutil.which("docker") is not None


def _run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, check=False)
    except OSError as exc:
        return subprocess.CompletedProcess(
            cmd,
            1,
            stdout="",
            stderr=f"docker command failed: {exc}",
        )


def _parse_size(text: str) -> int:
    """Parse `docker system prune` output для извлечения bytes freed.

    Output examples:
        "Total reclaimed space: 1.234GB"
        "Total reclaimed space: 567.8MB"
        "Total reclaimed space: 0B"
    """
    for line in text.splitlines():
        if "reclaimed" not in line.lower():
            continue
        parts = line.strip().split()
        if not parts:
            continue
        last = parts[-1].upper()
        for suffix, mul in (("GB", 1024**3), ("MB", 1024**2), ("KB", 1024), ("B", 1)):
            if last.endswith(suffix):
                try:
                    val = float(last[: -len(suffix)])
                    return int(val * mul)
                except ValueError:
                    return 0
    return 0


def gc_containers(dry_run: bool = False) -> GcReport:
    """Remove stopped/dead containers."""
    if not _docker_available():
        return GcReport(target="containers", error="docker not installed")

    if dry_run:
        # docker container ls -a --filter status=exited --filter status=dead
        result = _run(
            [
                "docker",
                "container",
                "ls",
                "-a",
                "--filter",
                "status=exited",
                "--filter",
                "status=dead",
                "--format",
                "{{.Names}}",
            ]
        )
        if result.returncode != 0:
            return GcReport(target="containers", error=result.stderr.strip(), dry_run=True)
        names = [n for n in result.stdout.strip().splitlines() if n]
        return GcReport(
            target="containers",
            items_removed=len(names),
            dry_run=True,
            items=names,
        )

    result = _run(["docker", "container", "prune", "-f"])
    if result.returncode != 0:
        return GcReport(target="containers", error=result.stderr.strip())
    return GcReport(
        target="containers",
        bytes_freed=_parse_size(result.stdout),
    )


def gc_images(
    older_than_hours: int = 72, aggressive: bool = False, dry_run: bool = False
) -> GcReport:
    """Remove cutoff images. Mode-faithful: dry-run previews exactly what runs.

    По умолчанию (safe mode): удаляет только dangling images старше cutoff
    (`docker image prune -f --filter until=<N>h`) — это совпадает с тем, что
    показывает dry-run preview.
    aggressive=True: удаляет ВСЕ unused images старше cutoff
    (`docker image prune -af --filter until=<N>h`), включая запинённые но
    остановленные. Dry-run в этом режиме перечисляет тот же all-unused набор.

    Args:
        older_than_hours: skip удалённые позже N часов. Default 72h.
        aggressive: prune ВСЕ unused (а не только dangling).
    """
    if not _docker_available():
        return GcReport(target="images", error="docker not installed")

    until = f"{older_than_hours}h"

    if dry_run:
        if aggressive:
            # Mirror real `prune -af --filter until`: enumerate ALL images
            # filtered by the same cutoff (drop dangling=true).
            cmd = [
                "docker",
                "image",
                "ls",
                "-a",
                "--filter",
                f"until={until}",
                "--format",
                "{{.Repository}}:{{.Tag}} ({{.Size}})",
            ]
        else:
            # Mirror real `prune -f`: dangling-only.
            cmd = [
                "docker",
                "image",
                "ls",
                "-a",
                "--filter",
                "dangling=true",
                "--filter",
                f"until={until}",
                "--format",
                "{{.Repository}}:{{.Tag}} ({{.Size}})",
            ]
        result = _run(cmd)
        if result.returncode != 0:
            return GcReport(target="images", error=result.stderr.strip(), dry_run=True)
        items = [l for l in result.stdout.strip().splitlines() if l]
        return GcReport(target="images", items_removed=len(items), dry_run=True, items=items)

    if aggressive:
        result = _run(["docker", "image", "prune", "-af", "--filter", f"until={until}"])
    else:
        result = _run(["docker", "image", "prune", "-f", "--filter", f"until={until}"])
    if result.returncode != 0:
        return GcReport(target="images", error=result.stderr.strip())
    return GcReport(
        target="images",
        bytes_freed=_parse_size(result.stdout),
    )


def gc_volumes(aggressive: bool = False, dry_run: bool = False) -> GcReport:
    """Remove unused volumes.

    По умолчанию (safe mode): удаляет только volumes с label `agmind.gc=auto`.
    aggressive=True: удаляет ВСЕ unused volumes (docker volume prune -af).
    """
    if not _docker_available():
        return GcReport(target="volumes", error="docker not installed")

    if dry_run:
        if aggressive:
            cmd = ["docker", "volume", "ls", "-q", "--filter", "dangling=true"]
        else:
            cmd = [
                "docker",
                "volume",
                "ls",
                "-q",
                "--filter",
                "dangling=true",
                "--filter",
                "label=agmind.gc=auto",
            ]
        result = _run(cmd)
        if result.returncode != 0:
            return GcReport(target="volumes", error=result.stderr.strip(), dry_run=True)
        items = [l for l in result.stdout.strip().splitlines() if l]
        return GcReport(target="volumes", items_removed=len(items), dry_run=True, items=items)

    if aggressive:
        result = _run(["docker", "volume", "prune", "-af"])
    else:
        result = _run(["docker", "volume", "prune", "-af", "--filter", "label=agmind.gc=auto"])
    if result.returncode != 0:
        return GcReport(target="volumes", error=result.stderr.strip())
    return GcReport(
        target="volumes",
        bytes_freed=_parse_size(result.stdout),
    )


def gc_networks(dry_run: bool = False) -> GcReport:
    """Remove unused docker networks."""
    if not _docker_available():
        return GcReport(target="networks", error="docker not installed")

    if dry_run:
        result = _run(["docker", "network", "ls", "--format", "{{.Name}}"])
        if result.returncode != 0:
            return GcReport(target="networks", error=result.stderr.strip(), dry_run=True)
        # docker network prune не показывает что бы удалил dry-run mode
        return GcReport(target="networks", dry_run=True)

    result = _run(["docker", "network", "prune", "-f"])
    if result.returncode != 0:
        return GcReport(target="networks", error=result.stderr.strip())
    return GcReport(target="networks", bytes_freed=0)  # networks don't free bytes


def gc_models(
    models_dir: Path = Path("/var/lib/agmind/models"),
    used_filenames: set[str] | None = None,
    dry_run: bool = False,
) -> GcReport:
    """Remove model files (GGUF/safetensors) не упомянутые ни в одном descriptor.

    Args:
        models_dir: где живут модели
        used_filenames: set имён файлов которые используются (если None — load из
                       templates/services/*.yaml через ServiceDescriptor scan)
        dry_run: только показать
    """
    if not models_dir.exists():
        return GcReport(target="models", error=f"{models_dir} does not exist")

    if used_filenames is None:
        try:
            used_filenames = _scan_used_models()
        except ValueError as exc:
            # Never delete on incomplete knowledge: a single unparseable
            # descriptor would drop its models from the used-set.
            return GcReport(target="models", error=f"model scan failed, not deleting: {exc}")

    candidates: list[Path] = []
    for path in models_dir.iterdir():
        if not path.is_file():
            continue
        if path.suffix.lower() not in _MODEL_SUFFIXES:
            continue
        if path.name in used_filenames:
            continue
        candidates.append(path)

    if dry_run:
        return GcReport(
            target="models",
            items_removed=len(candidates),
            bytes_freed=sum(p.stat().st_size for p in candidates),
            dry_run=True,
            items=[p.name for p in candidates],
        )

    total_bytes = 0
    removed: list[str] = []
    for path in candidates:
        size = path.stat().st_size
        try:
            path.unlink()
            total_bytes += size
            removed.append(path.name)
            log.info("removed orphan model %s (%d bytes)", path.name, size)
        except OSError as exc:
            log.error("failed to remove %s: %s", path, exc)

    return GcReport(
        target="models",
        items_removed=len(removed),
        bytes_freed=total_bytes,
        items=removed,
    )


def _scan_used_models() -> set[str]:
    """Scan templates/services/*.yaml для env vars типа `*_MODEL=*.gguf` и
    `*_FILENAME=*.gguf` чтобы определить какие модели реально используются.

    Также скан templates/models.yaml если он существует (legacy catalog).
    """
    used: set[str] = set()
    repo_root = Path(__file__).resolve().parent.parent.parent

    # Сервисные дескрипторы
    services_dir = repo_root / "templates" / "services"
    if services_dir.exists():
        for yaml_path in services_dir.glob("*.yaml"):
            try:
                import yaml

                data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
                used.update(_extract_model_filenames(data))
            except Exception as exc:
                raise ValueError(f"cannot parse model source {yaml_path}: {exc}") from exc

    # Legacy models.yaml catalog
    models_yaml = repo_root / "templates" / "models.yaml"
    if models_yaml.exists():
        try:
            import yaml

            data = yaml.safe_load(models_yaml.read_text(encoding="utf-8"))

            used.update(_extract_model_filenames(data))
        except Exception as exc:
            raise ValueError(f"cannot parse model source {models_yaml}: {exc}") from exc

    return used


def _extract_model_filenames(obj: object) -> set[str]:
    """Return model filenames from nested descriptor/catalog structures."""
    used: set[str] = set()
    if isinstance(obj, dict):
        for value in obj.values():
            used.update(_extract_model_filenames(value))
    elif isinstance(obj, list):
        for value in obj:
            used.update(_extract_model_filenames(value))
    elif isinstance(obj, str):
        for match in _MODEL_FILENAME_RE.finditer(obj):
            used.add(Path(match.group(1)).name)
    return used


def gc_all(
    aggressive: bool = False,
    older_than_hours: int = 72,
    dry_run: bool = False,
    include_models: bool = False,
) -> list[GcReport]:
    """Run all GC operations. Returns list of reports."""
    reports = [
        gc_containers(dry_run=dry_run),
        gc_images(older_than_hours=older_than_hours, aggressive=aggressive, dry_run=dry_run),
        gc_volumes(aggressive=aggressive, dry_run=dry_run),
        gc_networks(dry_run=dry_run),
    ]
    if include_models:
        reports.append(gc_models(dry_run=dry_run))
    return reports


def format_gc_report(reports: list[GcReport]) -> str:
    """Format reports для CLI output."""
    lines: list[str] = []
    total_bytes = 0
    for r in reports:
        prefix = "[dry-run] " if r.dry_run else ""
        if r.error:
            lines.append(f"  ✗ {r.target}: {r.error}")
        else:
            size_gb = r.bytes_freed / 1024**3
            size_str = f"{size_gb:.2f} GB" if r.bytes_freed >= 1024**2 else f"{r.bytes_freed} B"
            lines.append(f"  ✓ {prefix}{r.target}: {r.items_removed} items, {size_str} freed")
            total_bytes += r.bytes_freed

    total_gb = total_bytes / 1024**3
    if total_bytes > 0:
        lines.append(f"\nTotal: {total_gb:.2f} GB freed")
    return "\n".join(lines) + "\n"
