"""Deploy orchestrator (Phase L.B): snapshot → render → diff → apply → healthcheck → rollback.

Это main entry point для CLI `agmind deploy`. Idempotent: безопасно запускать N раз —
если ничего не изменилось, no-op. Если что-то меняется — automatic snapshot перед apply,
automatic rollback если healthcheck не прошёл за timeout.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import yaml

from agmind.components.checks import check_deploy_conflicts
from agmind.core.logging import logger
from agmind.deploy.diff import ComposeDiff, compute_diff_from_files
from agmind.deploy.snapshot import Snapshot, SnapshotManager
from agmind.services.renderer import (
    load_descriptors,
    render_to_string,
    select_services,
    unknown_profiles,
)

# Progress callback: (step_id, message) — used by TUI DeployProgressScreen
# step_id one of: 'render', 'diff', 'snapshot', 'compose_up', 'healthcheck',
#                'wait_healthy', 'success', 'rollback', 'error'
ProgressCallback = Callable[[str, str], None]

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


def _run_compose(
    args: list[str],
    cwd: Path,
    sudo_password: str | None = None,
) -> tuple[int, str, str]:
    """Run `docker compose` command. Returns (returncode, stdout, stderr)."""
    env_file = cwd / ".env"
    env_args = ["--env-file", str(env_file)] if env_file.exists() else []
    compose_cmd = ["docker", "compose", *env_args, *args]
    cmd = compose_cmd
    log.debug("running: %s (cwd=%s)", " ".join(cmd), cwd)
    try:
        if sudo_password is not None:
            cmd = ["sudo", "-S", "-p", "", "--", *compose_cmd]
            log.debug("running: %s (cwd=%s)", " ".join(cmd), cwd)
            result = subprocess.run(
                cmd,
                cwd=cwd,
                capture_output=True,
                text=True,
                check=False,
                input=f"{sudo_password}\n",
            )
        else:
            result = subprocess.run(
                cmd,
                cwd=cwd,
                capture_output=True,
                text=True,
                check=False,
            )
    except OSError as exc:
        return 127, "", str(exc)
    return result.returncode, result.stdout, result.stderr


def _run_compose_maybe_sudo(
    args: list[str],
    cwd: Path,
    sudo_password: str | None,
) -> tuple[int, str, str]:
    if sudo_password is None:
        return _run_compose(args, cwd=cwd)
    return _run_compose(args, cwd=cwd, sudo_password=sudo_password)


def _read_text_maybe_sudo(
    path: Path,
    sudo_password: str | None = None,
) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except PermissionError:
        if sudo_password is None:
            raise

    result = subprocess.run(
        ["sudo", "-S", "-p", "", "--", "cat", str(path)],
        capture_output=True,
        text=True,
        check=False,
        input=f"{sudo_password}\n",
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "sudo cat failed").strip()
        raise OSError(f"cannot read {path} via sudo: {detail}")
    return result.stdout


def _write_text_maybe_sudo(
    path: Path,
    text: str,
    sudo_password: str | None = None,
    mode: str = "0644",
) -> None:
    local_tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            prefix=f".{path.name}.",
            dir=path.parent,
            delete=False,
        ) as handle:
            local_tmp_path = Path(handle.name)
        local_tmp_path.write_text(text, encoding="utf-8")
        local_tmp_path.chmod(int(mode, 8))
        local_tmp_path.replace(path)
        return
    except PermissionError:
        if local_tmp_path is not None:
            try:
                local_tmp_path.unlink()
            except FileNotFoundError:
                pass
        if sudo_password is None:
            raise
    except Exception:
        if local_tmp_path is not None:
            try:
                local_tmp_path.unlink()
            except FileNotFoundError:
                pass
        raise

    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        prefix=".agmind-write-",
        delete=False,
    ) as handle:
        tmp_path = Path(handle.name)
        handle.write(text)

    try:
        result = subprocess.run(
            [
                "sudo",
                "-S",
                "-p",
                "",
                "--",
                "install",
                "-D",
                "-m",
                mode,
                str(tmp_path),
                str(path),
            ],
            capture_output=True,
            text=True,
            check=False,
            input=f"{sudo_password}\n",
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "sudo install failed").strip()
            raise OSError(f"cannot write {path} via sudo: {detail}")
    finally:
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass


def _run_sudo_no_output(args: list[str], sudo_password: str) -> None:
    result = subprocess.run(
        ["sudo", "-S", "-p", "", "--", *args],
        capture_output=True,
        text=True,
        check=False,
        input=f"{sudo_password}\n",
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or f"sudo {args[0]} failed").strip()
        raise OSError(detail)


def _remove_file_maybe_sudo(path: Path, sudo_password: str | None = None) -> None:
    try:
        path.unlink()
        return
    except FileNotFoundError:
        return
    except PermissionError:
        if sudo_password is None:
            raise

    _run_sudo_no_output(["rm", "-f", str(path)], sudo_password)


def _restore_descriptors_from_snapshot(
    source: Path,
    target: Path,
    sudo_password: str | None = None,
) -> None:
    tmp_target = target.with_name(f".{target.name}.tmp")
    backup_target = target.with_name(f".{target.name}.rollback")
    if sudo_password is not None:
        _run_sudo_no_output(["rm", "-rf", "--one-file-system", str(tmp_target)], sudo_password)
        _run_sudo_no_output(
            ["rm", "-rf", "--one-file-system", str(backup_target)],
            sudo_password,
        )
        try:
            _run_sudo_no_output(
                ["install", "-d", "-m", "0755", str(tmp_target)],
                sudo_password,
            )
            _run_sudo_no_output(
                ["cp", "-R", "--no-preserve=ownership", f"{source}/.", str(tmp_target)],
                sudo_password,
            )
            _run_sudo_no_output(
                [
                    "sh",
                    "-c",
                    """
set -eu
target=$1
tmp_target=$2
backup_target=$3
rm -rf --one-file-system "$backup_target"
if [ -e "$target" ]; then
    mv "$target" "$backup_target"
fi
if mv "$tmp_target" "$target"; then
    rm -rf --one-file-system "$backup_target"
    exit 0
fi
if [ -e "$backup_target" ] && [ ! -e "$target" ]; then
    mv "$backup_target" "$target"
fi
exit 1
""",
                    "agmind-restore-descriptors",
                    str(target),
                    str(tmp_target),
                    str(backup_target),
                ],
                sudo_password,
            )
        except Exception:
            try:
                _run_sudo_no_output(
                    ["rm", "-rf", "--one-file-system", str(tmp_target)],
                    sudo_password,
                )
            except OSError:
                pass
            raise
        return
    shutil.rmtree(tmp_target, ignore_errors=True)
    shutil.rmtree(backup_target, ignore_errors=True)
    try:
        shutil.copytree(source, tmp_target)
        if target.exists():
            target.replace(backup_target)
        tmp_target.replace(target)
    except Exception:
        shutil.rmtree(tmp_target, ignore_errors=True)
        if backup_target.exists() and not target.exists():
            backup_target.replace(target)
        raise
    finally:
        shutil.rmtree(backup_target, ignore_errors=True)


def _validate_compose_config(
    compose_text: str,
    install_dir: Path,
    sudo_password: str | None = None,
) -> tuple[int, str]:
    """Validate rendered compose before replacing the deployed compose file."""
    handle = tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        prefix=".agmind-compose-",
        suffix=".yml",
        delete=False,
    )
    tmp_path = Path(handle.name)
    try:
        with handle:
            handle.write(compose_text)
        rc, _stdout, stderr = _run_compose_maybe_sudo(
            ["-f", str(tmp_path), "config", "--quiet"],
            cwd=install_dir,
            sudo_password=sudo_password,
        )
        return rc, stderr
    finally:
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass


def _compose_service_names(compose_text: str) -> list[str]:
    """Return rendered service names in Compose order."""
    data = yaml.safe_load(compose_text) or {}
    services = data.get("services") if isinstance(data, dict) else None
    if not isinstance(services, dict):
        return []
    return [str(name) for name in services]


def _compose_ps_containers(stdout: str) -> list[dict[str, object]]:
    """Normalize docker compose ps JSON output across Compose versions."""
    text = stdout.strip()
    if not text:
        return []

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = None

    if isinstance(parsed, list):
        return [item for item in parsed if isinstance(item, dict)]
    if isinstance(parsed, dict):
        return [parsed]

    containers: list[dict[str, object]] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            containers.append(item)
        elif isinstance(item, list):
            containers.extend(entry for entry in item if isinstance(entry, dict))
    return containers


def _wait_healthy(
    install_dir: Path,
    timeout: int,
    sudo_password: str | None = None,
) -> tuple[bool, list[str]]:
    """Wait until все сервисы помечены healthy (или running без healthcheck).

    Returns (success, unhealthy_names).
    """
    deadline = time.monotonic() + timeout
    last_unhealthy: list[str] = []

    while time.monotonic() < deadline:
        rc, stdout, _ = _run_compose_maybe_sudo(
            ["ps", "--format", "json"],
            cwd=install_dir,
            sudo_password=sudo_password,
        )
        if rc != 0:
            time.sleep(2)
            continue

        unhealthy: list[str] = []
        for container in _compose_ps_containers(stdout):
            name = str(container.get("Service") or container.get("Name") or "")
            health = str(container.get("Health") or "")
            state = str(container.get("State") or "")
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
    services: list[str] | None = None,
    progress: ProgressCallback | None = None,
    sudo_password: str | None = None,
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
        services: explicit service names; when set, service selection takes
            precedence over profile selection in the renderer

    Returns DeployResult.
    """
    if services == []:
        return DeployResult(success=False, message="no selected services for deploy")

    def _emit(step: str, msg: str) -> None:
        if progress is not None:
            try:
                progress(step, msg)
            except Exception as exc:  # noqa: BLE001
                log.debug("progress callback raised: %s (ignored)", exc)

    # 1. Validate deploy-level conflicts before rendering/diffing. Compose config
    # validates syntax and interpolation, but not mutually exclusive host ports.
    _emit("validate", "checking deploy-level service conflicts")
    try:
        descriptors = load_descriptors()
        if services is not None:
            missing = sorted(set(services).difference(descriptors))
            if missing:
                msg = "unknown selected services for deploy: " + ", ".join(missing)
                _emit("error", msg)
                return DeployResult(success=False, message=msg)
        if services is None:
            missing_profiles = unknown_profiles(descriptors, profiles)
            if missing_profiles:
                msg = "unknown selected profiles for deploy: " + ", ".join(missing_profiles)
                _emit("error", msg)
                return DeployResult(success=False, message=msg)
        selected = select_services(descriptors, profiles=profiles, services=services)
        conflict_report = check_deploy_conflicts(selected)
    except Exception as exc:
        _emit("error", f"deploy conflict check failed: {exc}")
        return DeployResult(success=False, message=f"deploy conflict check failed: {exc}")

    if conflict_report.has_errors:
        messages = [issue.message for issue in conflict_report.issues if issue.severity == "error"]
        msg = "deploy conflict(s): " + "; ".join(messages)
        log.error("%s", msg)
        _emit("error", msg)
        return DeployResult(success=False, message=msg)

    compose_file = install_dir / "docker-compose.yml"

    # 2. Render new compose
    selection_label = f"services={services}" if services is not None else f"profiles={profiles}"
    _emit("render", f"rendering compose for {selection_label}, domain={domain}")
    log.info("rendering compose for %s, domain=%s", selection_label, domain)
    try:
        new_compose = render_to_string(
            profiles=profiles,
            services=services,
            traefik_enabled=True,
            domain=domain,
        )
    except Exception as exc:
        _emit("error", f"render failed: {exc}")
        return DeployResult(success=False, message=f"render failed: {exc}")

    # 3. Compute diff vs current
    _emit("diff", "computing diff vs current deployment")
    diff = compute_diff_from_files(compose_file, new_compose)

    if not diff.has_changes:
        _emit("success", "no changes — current matches rendered")
        return DeployResult(
            success=True,
            diff=diff,
            message="no changes — current deployment matches rendered",
        )

    _emit(
        "diff",
        f"{diff.total_changes} change(s): +{len(diff.added)} -{len(diff.removed)} ~{len(diff.image_changed) + len(diff.config_changed)}",
    )

    if not apply:
        # Dry run mode — return diff for caller to display
        return DeployResult(
            success=True,
            diff=diff,
            message=f"{diff.total_changes} pending change(s) — re-run with --apply to deploy",
        )

    try:
        install_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        log.error("prepare install dir failed: %s", exc)
        _emit("error", f"prepare install dir failed: {exc}")
        return DeployResult(
            success=False,
            diff=diff,
            message=f"prepare install dir failed: {exc}",
        )

    # Validate interpolation, required env, and Compose syntax before replacing
    # the deployed file. This keeps a missing .env from leaving a broken compose
    # file behind on fresh hosts.
    _emit("validate", "validating rendered compose with docker compose config")
    try:
        if sudo_password is None:
            rc, stderr = _validate_compose_config(new_compose, install_dir)
        else:
            rc, stderr = _validate_compose_config(
                new_compose,
                install_dir,
                sudo_password=sudo_password,
            )
    except OSError as exc:
        rc, stderr = 127, str(exc)
    if rc != 0:
        msg = stderr.strip() or "docker compose config failed"
        log.error("docker compose config failed (rc=%d): %s", rc, msg)
        _emit("error", f"docker compose config failed: {msg[-200:]}")
        return DeployResult(
            success=False,
            diff=diff,
            message=f"docker compose config failed: {msg[-500:]}",
        )

    # 3. Snapshot текущее состояние (только если что-то deployed)
    snapshot: Snapshot | None = None
    if compose_file.exists():
        _emit("snapshot", "saving snapshot of current state")
        snap_mgr = SnapshotManager(sudo_password=sudo_password)
        env_file_for_snapshot: Path | None = None
        snapshot_env_tmp: Path | None = None
        version_env_file_for_snapshot: Path | None = None
        snapshot_version_env_tmp: Path | None = None
        try:
            try:
                env_path = install_dir / ".env"
                if env_path.exists():
                    if sudo_password is None:
                        env_file_for_snapshot = env_path
                    else:
                        with tempfile.NamedTemporaryFile(
                            "w",
                            encoding="utf-8",
                            prefix=".agmind-env-snapshot-",
                            delete=False,
                        ) as handle:
                            snapshot_env_tmp = Path(handle.name)
                            handle.write(
                                _read_text_maybe_sudo(env_path, sudo_password=sudo_password)
                            )
                        snapshot_env_tmp.chmod(0o600)
                        env_file_for_snapshot = snapshot_env_tmp
                version_env_path = install_dir / "version.env"
                if version_env_path.exists():
                    if sudo_password is None:
                        version_env_file_for_snapshot = version_env_path
                    else:
                        with tempfile.NamedTemporaryFile(
                            "w",
                            encoding="utf-8",
                            prefix=".agmind-version-env-snapshot-",
                            delete=False,
                        ) as handle:
                            snapshot_version_env_tmp = Path(handle.name)
                            handle.write(
                                _read_text_maybe_sudo(
                                    version_env_path,
                                    sudo_password=sudo_password,
                                )
                            )
                        snapshot_version_env_tmp.chmod(0o644)
                        version_env_file_for_snapshot = snapshot_version_env_tmp
            except OSError as exc:
                log.error("snapshot prep failed: %s", exc)
                _emit("error", f"snapshot prep failed: {exc}")
                return DeployResult(
                    success=False,
                    diff=diff,
                    message=f"snapshot prep failed: {exc}",
                )

            try:
                snapshot = snap_mgr.save(
                    compose_text=_read_text_maybe_sudo(compose_file, sudo_password=sudo_password),
                    profile=",".join(profiles),
                    reason=snapshot_reason or f"pre-deploy {','.join(profiles)}",
                    descriptors_dir=install_dir / "templates" / "services"
                    if (install_dir / "templates" / "services").exists()
                    else None,
                    env_file=env_file_for_snapshot,
                    version_env_file=version_env_file_for_snapshot,
                )
            except OSError as exc:
                log.error("snapshot failed: %s", exc)
                _emit("error", f"snapshot failed: {exc}")
                return DeployResult(
                    success=False,
                    diff=diff,
                    message=f"snapshot failed: {exc}",
                )
        finally:
            if snapshot_env_tmp is not None:
                try:
                    snapshot_env_tmp.unlink()
                except FileNotFoundError:
                    pass
            if snapshot_version_env_tmp is not None:
                try:
                    snapshot_version_env_tmp.unlink()
                except FileNotFoundError:
                    pass
        log.info("snapshot created: %s", snapshot.id)
        _emit("snapshot", f"snapshot saved: {snapshot.id}")

    # 4. Write new compose
    try:
        _write_text_maybe_sudo(compose_file, new_compose, sudo_password=sudo_password)
    except OSError as exc:
        log.error("write compose failed: %s", exc)
        _emit("error", f"write compose failed: {exc}")
        rolled_back = False
        if snapshot is not None:
            _emit("rollback", "rolling back to snapshot")
            rolled_back = _rollback_to_snapshot(snapshot, install_dir, sudo_password=sudo_password)
        return DeployResult(
            success=False,
            diff=diff,
            snapshot=snapshot,
            message=f"write compose failed: {exc}",
            rollback_performed=rolled_back,
        )
    log.info("wrote new compose to %s", compose_file)
    _emit("render", f"wrote {compose_file}")

    # 5. docker compose up -d --remove-orphans
    service_names = _compose_service_names(new_compose)
    _emit(
        "compose_up",
        f"running: docker compose up -d --remove-orphans ({len(service_names)} services)",
    )
    log.info("running docker compose up -d --remove-orphans for %d services", len(service_names))
    rc, stdout, stderr = _run_compose_maybe_sudo(
        ["up", "-d", "--remove-orphans", "--quiet-pull", *service_names],
        cwd=install_dir,
        sudo_password=sudo_password,
    )
    if rc != 0:
        log.error("docker compose up failed (rc=%d): %s", rc, stderr)
        _emit("error", f"docker compose up failed: {stderr[-200:]}")
        rolled_back = False
        if snapshot is not None:
            _emit("rollback", "rolling back to snapshot")
            rolled_back = _rollback_to_snapshot(snapshot, install_dir, sudo_password=sudo_password)
        return DeployResult(
            success=False,
            diff=diff,
            snapshot=snapshot,
            message=f"docker compose up failed: {stderr[-500:]}",
            rollback_performed=rolled_back,
        )

    # 6. Wait for healthy
    _emit("wait_healthy", f"waiting for healthy state (timeout={healthcheck_timeout}s)")
    log.info("waiting for healthy state (timeout=%ds)...", healthcheck_timeout)
    if sudo_password is None:
        healthy, unhealthy = _wait_healthy(install_dir, healthcheck_timeout)
    else:
        healthy, unhealthy = _wait_healthy(
            install_dir,
            healthcheck_timeout,
            sudo_password=sudo_password,
        )

    if not healthy:
        log.error("healthcheck timeout — unhealthy: %s", unhealthy)
        _emit("error", f"healthcheck timeout; unhealthy: {unhealthy}")
        rolled_back = False
        if snapshot is not None:
            _emit("rollback", "rolling back to snapshot")
            rolled_back = _rollback_to_snapshot(snapshot, install_dir, sudo_password=sudo_password)
        return DeployResult(
            success=False,
            diff=diff,
            snapshot=snapshot,
            message=f"healthcheck timeout after {healthcheck_timeout}s; unhealthy: {unhealthy}",
            rollback_performed=rolled_back,
        )

    _emit("success", f"deployed {diff.total_changes} change(s) — all healthy")
    return DeployResult(
        success=True,
        diff=diff,
        snapshot=snapshot,
        message=f"deployed {diff.total_changes} change(s) — all healthy",
    )


def rollback(
    snapshot_id: str | None = None,
    install_dir: Path = DEFAULT_INSTALL_DIR,
    sudo_password: str | None = None,
) -> DeployResult:
    """Restore deployment to previous snapshot.

    Args:
        snapshot_id: ID конкретного snapshot (omit → latest)
        install_dir: где живёт docker-compose.yml
    """
    snap_mgr = SnapshotManager(sudo_password=sudo_password)
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
    success = _rollback_to_snapshot(snap, install_dir, sudo_password=sudo_password)

    return DeployResult(
        success=success,
        snapshot=snap,
        message=f"rolled back to {snap.id}" if success else "rollback failed",
        rollback_performed=success,
    )


def _rollback_to_snapshot(
    snapshot: Snapshot,
    install_dir: Path,
    sudo_password: str | None = None,
) -> bool:
    """Replace install_dir state from snapshot. Returns True on success."""
    try:
        compose_file = install_dir / "docker-compose.yml"
        compose_text: str | None = None
        if snapshot.compose_file.exists():
            compose_text = snapshot.compose_file.read_text(encoding="utf-8")
            _write_text_maybe_sudo(
                compose_file,
                compose_text,
                sudo_password=sudo_password,
            )

        env_file = install_dir / ".env"
        if snapshot.env_file.exists():
            _write_text_maybe_sudo(
                env_file,
                snapshot.env_file.read_text(encoding="utf-8"),
                sudo_password=sudo_password,
                mode="0600",
            )

        version_env_file = install_dir / "version.env"
        if snapshot.version_env_file.exists():
            _write_text_maybe_sudo(
                version_env_file,
                snapshot.version_env_file.read_text(encoding="utf-8"),
                sudo_password=sudo_password,
                mode="0644",
            )
        else:
            _remove_file_maybe_sudo(version_env_file, sudo_password=sudo_password)

        # Restore descriptors (optional — sometimes not present)
        if snapshot.descriptors_dir.exists():
            target = install_dir / "templates" / "services"
            _restore_descriptors_from_snapshot(
                snapshot.descriptors_dir,
                target,
                sudo_password=sudo_password,
            )

        # Re-apply the rolled-back compose
        if compose_text is None:
            compose_text = _read_text_maybe_sudo(compose_file, sudo_password=sudo_password)
        service_names = _compose_service_names(compose_text)
        rc, _, stderr = _run_compose_maybe_sudo(
            ["up", "-d", "--remove-orphans", *service_names],
            cwd=install_dir,
            sudo_password=sudo_password,
        )
        if rc != 0:
            log.error("rollback compose up failed: %s", stderr)
            return False
        log.info("rollback complete to snapshot %s", snapshot.id)
        return True
    except Exception as exc:  # noqa: BLE001
        log.error("rollback failed: %s", exc)
        return False
