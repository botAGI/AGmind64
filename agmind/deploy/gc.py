"""Garbage Collection subsystem (Phase L.C).

Решает user pain "stale images/volumes/containers едят диск через месяц".
Auto-prune'ить безопасно — все production data в named volumes с label
`agmind.gc=keep`, временные volumes label'ятся `agmind.gc=auto` рендерером.

Operations:
    - containers: stopped/dead containers
    - images: dangling + старше cutoff (default 72h)
    - volumes: anonymous + labeled `agmind.gc=auto`
    - networks: unused (не привязанные ни к одному контейнеру)
    - models: GGUF файлы в /var/lib/agmind/models не упомянутые ни в одном
              ServiceDescriptor.env (TODO в Phase L.C.2)

CLI: `agmind gc [--auto] [--aggressive] [--dry-run]`.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from agmind.log import logger

log = logger(__name__)


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
    return subprocess.run(cmd, capture_output=True, text=True, check=False)


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


def gc_images(older_than_hours: int = 72, dry_run: bool = False) -> GcReport:
    """Remove dangling + старше cutoff images.

    Args:
        older_than_hours: skip удалённые позже N часов. Default 72h.
    """
    if not _docker_available():
        return GcReport(target="images", error="docker not installed")

    until = f"{older_than_hours}h"

    if dry_run:
        result = _run(
            [
                "docker",
                "image",
                "ls",
                "-a",
                "--filter",
                "dangling=true",
                "--format",
                "{{.Repository}}:{{.Tag}} ({{.Size}})",
            ]
        )
        items = [l for l in result.stdout.strip().splitlines() if l]
        return GcReport(target="images", items_removed=len(items), dry_run=True, items=items)

    result = _run(
        [
            "docker",
            "image",
            "prune",
            "-af",
            "--filter",
            f"until={until}",
        ]
    )
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
        used_filenames = _scan_used_models()

    candidates: list[Path] = []
    for path in models_dir.iterdir():
        if not path.is_file():
            continue
        if path.suffix.lower() not in (".gguf", ".safetensors", ".bin"):
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
                env = (data or {}).get("env", {}) if isinstance(data, dict) else {}
                for value in env.values():
                    if isinstance(value, str) and value.endswith((".gguf", ".safetensors", ".bin")):
                        used.add(Path(value).name)
            except Exception:
                continue

    # Legacy models.yaml catalog
    models_yaml = repo_root / "templates" / "models.yaml"
    if models_yaml.exists():
        try:
            import yaml

            data = yaml.safe_load(models_yaml.read_text(encoding="utf-8"))

            # Walk values recursively
            def _walk(obj: object) -> None:
                if isinstance(obj, dict):
                    for v in obj.values():
                        _walk(v)
                elif isinstance(obj, list):
                    for v in obj:
                        _walk(v)
                elif isinstance(obj, str) and obj.endswith((".gguf", ".safetensors", ".bin")):
                    used.add(Path(obj).name)

            _walk(data)
        except Exception:
            pass

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
        gc_images(older_than_hours=older_than_hours, dry_run=dry_run),
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
