"""Deploy orchestrator (Phase L.B): snapshot → render → diff → apply → healthcheck → rollback.

Это main entry point для CLI `agmind deploy`. Idempotent: безопасно запускать N раз —
если ничего не изменилось, no-op. Если что-то меняется — automatic snapshot перед apply,
automatic rollback если healthcheck не прошёл за timeout.
"""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from agmind.deploy.diff import ComposeDiff, compute_diff_from_files
from agmind.deploy.snapshot import Snapshot, SnapshotManager
from agmind.log import logger
from agmind.services.renderer import render_to_string

log = logger(__name__)

DEFAULT_INSTALL_DIR = Path("/opt/agmind")
DEFAULT_HEALTHCHECK_TIMEOUT = 300  # 5 min


@dataclass(frozen=True)
class DeployResult:
    """Outcome of deploy operation."""

    success: bool
    diff: ComposeDiff | None = None
    snapshot: Snapshot | None = None
    message: str = ""
    rollback_performed: bool = False


def _run_compose(args: list[str], cwd: Path) -> tuple[int, str, str]:
    """Run `docker compose` command. Returns (returncode, stdout, stderr)."""
    cmd = ["docker", "compose", *args]
    log.debug("running: %s (cwd=%s)", " ".join(cmd), cwd)
    result = subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode, result.stdout, result.stderr


def _wait_healthy(install_dir: Path, timeout: int) -> tuple[bool, list[str]]:
    """Wait until все сервисы помечены healthy (или running без healthcheck).

    Returns (success, unhealthy_names).
    """
    deadline = time.monotonic() + timeout
    last_unhealthy: list[str] = []

    while time.monotonic() < deadline:
        rc, stdout, _ = _run_compose(
            ["ps", "--format", "json"],
            cwd=install_dir,
        )
        if rc != 0:
            time.sleep(2)
            continue

        # docker compose ps --format json возвращает one-line-per-container JSONL
        import json

        unhealthy: list[str] = []
        for line in stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                container = json.loads(line)
            except json.JSONDecodeError:
                continue
            name = container.get("Service", container.get("Name", ""))
            health = container.get("Health", "")
            state = container.get("State", "")
            # healthy / starting / unhealthy / "" (no healthcheck declared)
            if health == "unhealthy":
                unhealthy.append(name)
            elif health == "starting":
                unhealthy.append(f"{name} (starting)")
            elif health == "":
                # Нет healthcheck — считаем healthy если running
                if state != "running":
                    unhealthy.append(f"{name} ({state})")

        if not unhealthy:
            return True, []
        last_unhealthy = unhealthy
        time.sleep(5)

    return False, last_unhealthy


def deploy(
    profiles: list[str],
    install_dir: Path = DEFAULT_INSTALL_DIR,
    domain: str | None = None,
    apply: bool = False,
    no_prompt: bool = False,
    healthcheck_timeout: int = DEFAULT_HEALTHCHECK_TIMEOUT,
    snapshot_reason: str = "",
) -> DeployResult:
    """Main deploy orchestrator.

    Args:
        profiles: active profile names (e.g. ['core', 'observability'])
        install_dir: где живёт docker-compose.yml и .env (default /opt/agmind)
        domain: override agmind.dev placeholder (для multi-tenant)
        apply: если False — только показать diff (dry run); True — реально применить
        no_prompt: пропустить interactive confirmation (для CI / Ansible)
        healthcheck_timeout: сколько ждать healthy state (sec)
        snapshot_reason: human-readable reason для snapshot meta

    Returns DeployResult.
    """
    install_dir.mkdir(parents=True, exist_ok=True)
    compose_file = install_dir / "docker-compose.yml"

    # 1. Render new compose
    log.info("rendering compose for profiles=%s, domain=%s", profiles, domain)
    try:
        new_compose = render_to_string(
            profiles=profiles,
            traefik_enabled=True,
            domain=domain,
        )
    except Exception as exc:
        return DeployResult(success=False, message=f"render failed: {exc}")

    # 2. Compute diff vs current
    diff = compute_diff_from_files(compose_file, new_compose)

    if not diff.has_changes:
        return DeployResult(
            success=True,
            diff=diff,
            message="no changes — current deployment matches rendered",
        )

    if not apply:
        # Dry run mode — return diff for caller to display
        return DeployResult(
            success=True,
            diff=diff,
            message=f"{diff.total_changes} pending change(s) — re-run with --apply to deploy",
        )

    # 3. Snapshot текущее состояние (только если что-то deployed)
    snapshot: Snapshot | None = None
    if compose_file.exists():
        snap_mgr = SnapshotManager()
        snapshot = snap_mgr.save(
            compose_text=compose_file.read_text(encoding="utf-8"),
            profile=",".join(profiles),
            reason=snapshot_reason or f"pre-deploy {','.join(profiles)}",
            descriptors_dir=install_dir / "templates" / "services"
                if (install_dir / "templates" / "services").exists() else None,
            env_file=install_dir / ".env" if (install_dir / ".env").exists() else None,
        )
        log.info("snapshot created: %s", snapshot.id)

    # 4. Write new compose
    compose_file.write_text(new_compose, encoding="utf-8")
    log.info("wrote new compose to %s", compose_file)

    # 5. docker compose up -d --remove-orphans
    log.info("running docker compose up -d --remove-orphans")
    rc, stdout, stderr = _run_compose(
        ["up", "-d", "--remove-orphans", "--quiet-pull"],
        cwd=install_dir,
    )
    if rc != 0:
        # Apply failed — rollback
        log.error("docker compose up failed (rc=%d): %s", rc, stderr)
        rolled_back = False
        if snapshot is not None:
            rolled_back = _rollback_to_snapshot(snapshot, install_dir)
        return DeployResult(
            success=False,
            diff=diff,
            snapshot=snapshot,
            message=f"docker compose up failed: {stderr[-500:]}",
            rollback_performed=rolled_back,
        )

    # 6. Wait for healthy
    log.info("waiting for healthy state (timeout=%ds)...", healthcheck_timeout)
    healthy, unhealthy = _wait_healthy(install_dir, healthcheck_timeout)

    if not healthy:
        # Healthcheck timeout — rollback
        log.error("healthcheck timeout — unhealthy: %s", unhealthy)
        rolled_back = False
        if snapshot is not None:
            rolled_back = _rollback_to_snapshot(snapshot, install_dir)
        return DeployResult(
            success=False,
            diff=diff,
            snapshot=snapshot,
            message=f"healthcheck timeout after {healthcheck_timeout}s; unhealthy: {unhealthy}",
            rollback_performed=rolled_back,
        )

    return DeployResult(
        success=True,
        diff=diff,
        snapshot=snapshot,
        message=f"deployed {diff.total_changes} change(s) — all healthy",
    )


def rollback(
    snapshot_id: str | None = None,
    install_dir: Path = DEFAULT_INSTALL_DIR,
) -> DeployResult:
    """Restore deployment to previous snapshot.

    Args:
        snapshot_id: ID конкретного snapshot (omit → latest)
        install_dir: где живёт docker-compose.yml
    """
    snap_mgr = SnapshotManager()
    if snapshot_id is None:
        snap = snap_mgr.latest()
    else:
        snap = snap_mgr.get(snapshot_id)

    if snap is None:
        return DeployResult(
            success=False,
            message=f"snapshot not found ({snapshot_id or '<latest>'})",
        )

    log.info("rolling back to snapshot %s", snap.id)
    success = _rollback_to_snapshot(snap, install_dir)

    return DeployResult(
        success=success,
        snapshot=snap,
        message=f"rolled back to {snap.id}" if success else "rollback failed",
        rollback_performed=success,
    )


def _rollback_to_snapshot(snapshot: Snapshot, install_dir: Path) -> bool:
    """Replace install_dir state from snapshot. Returns True on success."""
    try:
        compose_file = install_dir / "docker-compose.yml"
        if snapshot.compose_file.exists():
            compose_file.write_text(
                snapshot.compose_file.read_text(encoding="utf-8"),
                encoding="utf-8",
            )

        env_file = install_dir / ".env"
        if snapshot.env_file.exists():
            env_file.write_text(
                snapshot.env_file.read_text(encoding="utf-8"),
                encoding="utf-8",
            )

        # Restore descriptors (optional — sometimes not present)
        if snapshot.descriptors_dir.exists():
            import shutil
            target = install_dir / "templates" / "services"
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(snapshot.descriptors_dir, target)

        # Re-apply the rolled-back compose
        rc, _, stderr = _run_compose(
            ["up", "-d", "--remove-orphans"],
            cwd=install_dir,
        )
        if rc != 0:
            log.error("rollback compose up failed: %s", stderr)
            return False
        log.info("rollback complete to snapshot %s", snapshot.id)
        return True
    except Exception as exc:  # noqa: BLE001
        log.error("rollback failed: %s", exc)
        return False
