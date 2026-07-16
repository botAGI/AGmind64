"""Deploy orchestrator (Phase L.B): snapshot → render → diff → apply → healthcheck → rollback.

Это main entry point для CLI `agmind deploy`. Idempotent: безопасно запускать N раз —
если ничего не изменилось, no-op. Если что-то меняется — automatic snapshot перед apply,
automatic rollback если healthcheck не прошёл за timeout.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import typer
import yaml

from agmind.components.checks import check_deploy_conflicts
from agmind.core.docker_auth import user_docker_config_dir
from agmind.core.logging import logger
from agmind.core.proc import sudo_argv, sudo_stdin_text
from agmind.deploy.diff import ComposeDiff, compute_diff_from_files
from agmind.deploy.snapshot import Snapshot, SnapshotManager
from agmind.services.renderer import (
    load_descriptors,
    render_to_string,
    select_services,
    unknown_profiles,
)

# Progress callback: (step_id, message) — used by TUI DeployProgressScreen
# step_id one of: 'render', 'diff', 'snapshot', 'pull', 'compose_up', 'healthcheck',
#                'wait_healthy', 'success', 'rollback', 'error'
ProgressCallback = Callable[[str, str], None]

log = logger(__name__)

DEFAULT_INSTALL_DIR = Path("/opt/agmind")
DEFAULT_HEALTHCHECK_TIMEOUT = 300  # 5 min
COMPOSE_SHORT_TIMEOUT = 60


@dataclass(frozen=True)
class DeployResult:
    """Outcome of deploy operation."""

    success: bool
    diff: ComposeDiff | None = None
    snapshot: Snapshot | None = None
    message: str = ""
    rollback_performed: bool = False


def _user_docker_config_dir() -> str | None:
    """The invoking user's docker config dir (with `docker login` creds), if present.

    The deploy runs `docker` under sudo (to read root-owned /opt/agmind/.env). Plain
    `sudo docker` uses root's empty /root/.docker and pulls ANONYMOUSLY → Docker Hub
    `toomanyrequests` mid-deploy on a 36-image stack. Pointing sudo-docker at the
    invoking user's authenticated config (via `env DOCKER_CONFIG=...`) makes the pulls
    authenticated and dodges the anon limit.
    """
    return user_docker_config_dir()


@contextmanager
def _deploy_lock(install_dir: Path) -> Iterator[bool]:
    """Advisory single-flight lock around a deploy apply, keyed on *install_dir*.

    Two concurrent `docker compose up` on the same project race to create the same
    container names (the `/agmind-watchtower` Conflict). The TUI guard stops in-app
    re-entry; this `flock` additionally serialises across PROCESSES (e.g. `agmind
    deploy` started while the installer is mid-deploy). Yields True if acquired, False
    if another deploy already holds it. The lock file lives under the system temp dir
    (always writable by the invoking user, unlike root-owned /opt/agmind).
    """
    digest = hashlib.sha256(str(install_dir.resolve()).encode("utf-8")).hexdigest()[:16]
    lock_path = Path(tempfile.gettempdir()) / f"agmind-deploy-{digest}.lock"
    fd: int | None = None
    try:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o666)
        except OSError as exc:
            # Cannot create the lock file — do not block the deploy on lock infra.
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


def _offline_pull_enabled() -> bool:
    """``AGMIND_OFFLINE`` requests an air-gap deploy (no network image pulls).

    Single source of truth for the offline flag, in the *deploy* layer so the real
    deploy path (this module) honors it. ``agmind.install.steps._offline_install_enabled``
    delegates here so there is exactly one reader (install may import deploy, not vice
    versa).
    """
    return os.environ.get("AGMIND_OFFLINE", "").strip().lower() in {"1", "true", "yes", "on"}


def resolve_pull_policy(offline: bool | None = None) -> str:
    """`docker compose pull --policy` value — the one place that decides it.

    Air-gap (``AGMIND_OFFLINE``) → ``never``: use images preloaded via ``docker load`` and
    never hit the network (a digest-pinned ``--policy missing`` would re-pull because
    docker save/load strips the RepoDigest). Otherwise ``missing`` — idempotent, pulls only
    what is absent. Pass ``offline`` explicitly to override the env.
    """
    if offline is None:
        offline = _offline_pull_enabled()
    return "never" if offline else "missing"


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
            # Keep sudo (root reads the root-owned .env) but make sudo-docker use the
            # invoking user's auth so pulls are authenticated, not anonymous.
            docker_cfg = _user_docker_config_dir()
            env_prefix = ["env", f"DOCKER_CONFIG={docker_cfg}"] if docker_cfg else []
            cmd = sudo_argv([*env_prefix, *compose_cmd])
            log.debug("running: %s (cwd=%s)", " ".join(cmd), cwd)
            result = subprocess.run(
                cmd,
                cwd=cwd,
                capture_output=True,
                text=True,
                check=False,
                input=sudo_stdin_text(sudo_password),
                timeout=COMPOSE_SHORT_TIMEOUT,
            )
        else:
            result = subprocess.run(
                cmd,
                cwd=cwd,
                capture_output=True,
                text=True,
                check=False,
                timeout=COMPOSE_SHORT_TIMEOUT,
            )
    except subprocess.TimeoutExpired as exc:
        return 124, "", f"docker compose {' '.join(args)} timed out after {exc.timeout}s"
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


def _kill_proc_group(proc: subprocess.Popen[str]) -> None:
    """Best-effort terminate a streamed compose child and its process group.

    The child may be `sudo` running docker as root; a non-root parent often cannot
    signal a root process (EPERM), so this is best-effort — the flock single-flight
    lock, not this kill, is the real guard against a concurrent `compose up`.
    """
    import signal

    def _signal(sig: int) -> None:
        try:
            os.killpg(os.getpgid(proc.pid), sig)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                (proc.kill if sig == signal.SIGKILL else proc.terminate)()
            except OSError:
                pass

    _signal(signal.SIGTERM)
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        _signal(signal.SIGKILL)


def _stream_compose(
    args: list[str],
    cwd: Path,
    sudo_password: str | None = None,
    on_line: Callable[[str], None] | None = None,
    cancel_event: threading.Event | None = None,
) -> tuple[int, str]:
    """Run `docker compose` streaming stdout line-by-line. Returns (rc, tail).

    Unlike the blocking :func:`_run_compose`, this uses Popen so the long pull/up
    phases are observable (per-line progress via *on_line*) and interruptible (a
    cancel watchdog + a between-line check kill the child when *cancel_event* fires).
    Keep the short config/ps calls on :func:`_run_compose`. ``tail`` is the last lines
    of output (for error messages).
    """
    env_file = cwd / ".env"
    env_args = ["--env-file", str(env_file)] if env_file.exists() else []
    compose_cmd = ["docker", "compose", *env_args, *args]
    if sudo_password is not None:
        docker_cfg = _user_docker_config_dir()
        env_prefix = ["env", f"DOCKER_CONFIG={docker_cfg}"] if docker_cfg else []
        cmd = ["sudo", "-S", "-p", "", "--", *env_prefix, *compose_cmd]
    else:
        cmd = compose_cmd
    log.debug("streaming: %s (cwd=%s)", " ".join(cmd), cwd)

    if cancel_event is not None and cancel_event.is_set():
        return 130, "cancelled by user"

    try:
        proc = subprocess.Popen(
            cmd,
            cwd=cwd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            start_new_session=True,
        )
    except OSError as exc:
        return 127, str(exc)

    tail: list[str] = []
    stop_watch = threading.Event()

    def _watchdog() -> None:
        # Covers the hung-with-no-output case the between-line check below can't.
        while not stop_watch.wait(0.25):
            if cancel_event is not None and cancel_event.is_set():
                _kill_proc_group(proc)
                return

    watcher: threading.Thread | None = None
    if cancel_event is not None:
        watcher = threading.Thread(target=_watchdog, name="agmind-compose-cancel", daemon=True)
        watcher.start()

    try:
        if sudo_password is not None and proc.stdin is not None:
            try:
                proc.stdin.write(f"{sudo_password}\n")
                proc.stdin.flush()
                proc.stdin.close()
            except (BrokenPipeError, OSError):
                pass
        if proc.stdout is not None:
            for raw in proc.stdout:
                if cancel_event is not None and cancel_event.is_set():
                    _kill_proc_group(proc)
                    break
                line = raw.rstrip("\n")
                tail.append(line)
                del tail[:-80]
                if on_line is not None and line:
                    try:
                        on_line(line)
                    except Exception as exc:
                        log.debug("on_line raised: %s (ignored)", exc)
        rc = proc.wait()
    finally:
        stop_watch.set()
        if watcher is not None:
            watcher.join(timeout=1)
        if proc.poll() is None:
            _kill_proc_group(proc)
        if proc.stdout is not None:
            try:
                proc.stdout.close()
            except OSError:
                pass

    if cancel_event is not None and cancel_event.is_set():
        return 130, "\n".join(tail) or "cancelled by user"
    return rc, "\n".join(tail)


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
        sudo_argv(["cat", str(path)]),
        capture_output=True,
        text=True,
        check=False,
        input=sudo_stdin_text(sudo_password),
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
            sudo_argv(["install", "-D", "-m", mode, str(tmp_path), str(path)]),
            capture_output=True,
            text=True,
            check=False,
            input=sudo_stdin_text(sudo_password),
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
        sudo_argv(args),
        capture_output=True,
        text=True,
        check=False,
        input=sudo_stdin_text(sudo_password),
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


def _interruptible_sleep(seconds: float, cancel_event: threading.Event | None) -> bool:
    """Sleep up to *seconds*, returning True if cancelled mid-sleep.

    With no cancel_event this is a plain sleep (returns False). With one, it wakes
    immediately when the event fires so the healthcheck poll can abort promptly.
    """
    if cancel_event is None:
        time.sleep(seconds)
        return False
    return cancel_event.wait(seconds)


def _wait_healthy(
    install_dir: Path,
    timeout: int,
    sudo_password: str | None = None,
    cancel_event: threading.Event | None = None,
    expected_services: list[str] | None = None,
) -> tuple[bool, list[str]]:
    """Wait until все сервисы помечены healthy (или running без healthcheck).

    Returns (success, unhealthy_names). If `cancel_event` fires, returns early
    (success=False) instead of blocking the worker for the full timeout.

    ``expected_services`` is the set of services that ``compose up`` was asked to start.
    Any expected service that produced NO container — an empty/partial ``ps`` right after
    ``up``, a service that exited and was reaped, or one that never created a container —
    counts as unhealthy. Without this an empty ``ps`` would return (True, []) and a stack
    that never came up would be reported "all healthy" (rollback skipped). See audit H#3.
    """
    deadline = time.monotonic() + timeout
    last_unhealthy: list[str] = []
    expected = set(expected_services or [])

    while time.monotonic() < deadline:
        if cancel_event is not None and cancel_event.is_set():
            return False, last_unhealthy or ["cancelled by user"]
        rc, stdout, _ = _run_compose_maybe_sudo(
            ["ps", "--format", "json"],
            cwd=install_dir,
            sudo_password=sudo_password,
        )
        if rc != 0:
            if _interruptible_sleep(2.0, cancel_event):
                return False, last_unhealthy or ["cancelled by user"]
            continue

        unhealthy: list[str] = []
        seen: set[str] = set()
        for container in _compose_ps_containers(stdout):
            name = str(container.get("Service") or container.get("Name") or "")
            seen.add(name)
            health = str(container.get("Health") or "")
            state = str(container.get("State") or "")
            # healthy / starting / unhealthy / "" (no healthcheck declared)
            if health == "unhealthy":
                unhealthy.append(name)
            elif health == "starting":
                unhealthy.append(f"{name} (starting)")
            elif health == "":
                # No Docker healthcheck → "running" is the only readiness signal we have.
                # This is sound ONLY because scripts/checks/healthcheck_coverage_check.py (A7)
                # forces every probe-less service to be a CONSCIOUS, classified exemption — a
                # stateful/web service can't silently land here without a probe or a reason.
                if state != "running":
                    unhealthy.append(f"{name} ({state})")

        # An expected service with no container at all is NOT ready — this is what stops an
        # empty/partial `ps` from being mistaken for "all healthy".
        for missing in sorted(expected - seen):
            unhealthy.append(f"{missing} (no container)")

        if not unhealthy:
            return True, []
        last_unhealthy = unhealthy
        if _interruptible_sleep(5.0, cancel_event):
            return False, last_unhealthy

    return False, last_unhealthy


# healthcheck timeout sizing — relocated here from agmind/install/steps.py (was defined
# upward-only in the install layer despite the deploy runner being the actual consumer of
# the wait budget; steps.py now re-exports this under the old private name). A flat 900s
# used to cover the three 600s-start_period llama servers, but it is blind to the actual
# selection: size the budget from the slowest selected service's start_period + a load
# margin, with 900s kept as a never-go-below floor. live install reliability 2026-06-09.
_HEALTHCHECK_TIMEOUT_FLOOR = 900
_HEALTHCHECK_LOAD_MARGIN = 600


def _parse_duration_seconds(raw: object) -> int:
    """Parse a Docker duration string (``"600s"`` / ``"5m"`` / ``"1h"``) to seconds.

    Best-effort: the registry only ever emits ``<int>s`` today, but accept the other
    plain Docker units defensively. Anything unparseable -> 0 (treated as "no hint").
    """
    if not isinstance(raw, str):
        return 0
    text = raw.strip()
    if not text:
        return 0
    units = {"s": 1, "m": 60, "h": 3600}
    unit = text[-1]
    if unit in units and text[:-1].isdigit():
        return int(text[:-1]) * units[unit]
    if text.isdigit():  # bare number -> seconds
        return int(text)
    return 0


def healthcheck_timeout_for(services: list[str]) -> tuple[int, str | None]:
    """Size the deploy healthcheck budget to the slowest selected service.

    Returns ``(timeout_seconds, driving_service)``. The timeout is
    ``max(floor, slowest_start_period + load_margin)``; *driving_service* names the
    service whose start_period set the budget, or None when the floor wins (no
    selected service declares a start_period, or all are small).
    """
    from agmind.services.registry import load_registry

    registry = load_registry()
    slowest_service: str | None = None
    slowest_start = 0
    for name in services:
        service = registry.get(name)
        if service is None:
            continue
        start = _parse_duration_seconds(service.health.get("start_period"))
        if start > slowest_start:
            slowest_start = start
            slowest_service = name

    data_driven = slowest_start + _HEALTHCHECK_LOAD_MARGIN if slowest_start else 0
    if data_driven > _HEALTHCHECK_TIMEOUT_FLOOR:
        return data_driven, slowest_service
    return _HEALTHCHECK_TIMEOUT_FLOOR, None


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
    cancel_event: threading.Event | None = None,
) -> DeployResult:
    """Single-flight wrapper around :func:`_deploy_impl`.

    A dry run (``apply=False``) is read-only and runs unguarded. An apply takes an
    advisory flock keyed on ``install_dir`` so a second concurrent apply (in-app or
    another process) cannot race the first into a duplicate ``docker compose up``.
    """
    if not apply:
        return _deploy_impl(
            profiles=profiles,
            install_dir=install_dir,
            domain=domain,
            apply=apply,
            no_prompt=no_prompt,
            healthcheck_timeout=healthcheck_timeout,
            snapshot_reason=snapshot_reason,
            services=services,
            progress=progress,
            sudo_password=sudo_password,
            cancel_event=cancel_event,
        )
    with _deploy_lock(install_dir) as acquired:
        if not acquired:
            if progress is not None:
                try:
                    progress("error", "another deploy is already in progress")
                except Exception as exc:
                    log.debug("progress callback raised: %s (ignored)", exc)
            return DeployResult(success=False, message="deploy already in progress")
        return _deploy_impl(
            profiles=profiles,
            install_dir=install_dir,
            domain=domain,
            apply=apply,
            no_prompt=no_prompt,
            healthcheck_timeout=healthcheck_timeout,
            snapshot_reason=snapshot_reason,
            services=services,
            progress=progress,
            sudo_password=sudo_password,
            cancel_event=cancel_event,
        )


def _deploy_impl(
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
    cancel_event: threading.Event | None = None,
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
            except Exception as exc:
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

    # G.1: confirmation gate for destructive applies. Removals/recreations destroy
    # containers + connected resources, so prompt before any mutation UNLESS no_prompt
    # is set (CI / Ansible / upgrade-apply). This block runs AFTER the dry-run early
    # return and BEFORE install_dir.mkdir / snapshot / write, so a declined prompt
    # makes ZERO mutating calls.
    if not no_prompt and diff.removed:
        removed_label = ", ".join(diff.removed)
        proceed = typer.confirm(
            f"⚠️  Apply will remove {len(diff.removed)} service(s) "
            f"({removed_label}) — containers + connected resources будут уничтожены. Continue?"
        )
        if not proceed:
            msg = "deploy aborted by user — no changes applied"
            log.info("%s", msg)
            _emit("error", msg)
            return DeployResult(success=False, diff=diff, message=msg)

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

    service_names = _compose_service_names(new_compose)

    # 5. Pull images as a VISIBLE, STREAMED phase (no --quiet-pull buried inside `up`).
    # During a multi-GB pull this streams per-layer lines so the TUI isn't a frozen 0%;
    # `up` then starts with --pull never (images are already local) and returns fast.
    # --ignore-buildable: services with a `build:` key and no `image:` (agent-agno,
    # agent-pydanticai, agent-ui) have no registry image to pull — without this flag,
    # a plain `pull` errors out on them. `up --pull never` still builds them locally
    # regardless of the pull policy, so skipping them here loses nothing.
    _emit("pull", f"pulling images for {len(service_names)} services")
    log.info("pulling images for %d services", len(service_names))
    pull_rc, pull_tail = _stream_compose(
        [
            "--progress",
            "plain",
            "pull",
            "--ignore-buildable",
            "--policy",
            resolve_pull_policy(),
            *service_names,
        ],
        cwd=install_dir,
        sudo_password=sudo_password,
        on_line=lambda line: _emit("pull", line),
        cancel_event=cancel_event,
    )
    if pull_rc != 0:
        log.error("docker compose pull failed (rc=%d): %s", pull_rc, pull_tail)
        _emit("error", f"docker compose pull failed: {pull_tail[-200:]}")
        rolled_back = False
        if snapshot is not None:
            _emit("rollback", "rolling back to snapshot")
            rolled_back = _rollback_to_snapshot(snapshot, install_dir, sudo_password=sudo_password)
        return DeployResult(
            success=False,
            diff=diff,
            snapshot=snapshot,
            message=f"docker compose pull failed: {pull_tail[-500:]}",
            rollback_performed=rolled_back,
        )

    # 6. docker compose up -d --remove-orphans --pull never (streamed + cancellable)
    _emit(
        "compose_up",
        f"running: docker compose up -d --remove-orphans ({len(service_names)} services)",
    )
    log.info("running docker compose up -d --remove-orphans for %d services", len(service_names))
    rc, up_tail = _stream_compose(
        ["up", "-d", "--remove-orphans", "--pull", "never", *service_names],
        cwd=install_dir,
        sudo_password=sudo_password,
        on_line=lambda line: _emit("compose_up", line),
        cancel_event=cancel_event,
    )
    if rc != 0:
        log.error("docker compose up failed (rc=%d): %s", rc, up_tail)
        _emit("error", f"docker compose up failed: {up_tail[-200:]}")
        rolled_back = False
        if snapshot is not None:
            _emit("rollback", "rolling back to snapshot")
            rolled_back = _rollback_to_snapshot(snapshot, install_dir, sudo_password=sudo_password)
        return DeployResult(
            success=False,
            diff=diff,
            snapshot=snapshot,
            message=f"docker compose up failed: {up_tail[-500:]}",
            rollback_performed=rolled_back,
        )

    # 6. Wait for healthy
    _emit("wait_healthy", f"waiting for healthy state (timeout={healthcheck_timeout}s)")
    log.info("waiting for healthy state (timeout=%ds)...", healthcheck_timeout)
    healthy, unhealthy = _wait_healthy(
        install_dir,
        healthcheck_timeout,
        sudo_password=sudo_password,
        cancel_event=cancel_event,
        expected_services=service_names,
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
    # Take the single-flight deploy lock: a manual `agmind rollback` (the TUI failure summary
    # instructs it) must not run `compose up` concurrently with a mid-flight deploy on the same
    # project — the exact Conflict race the flock prevents (review MEDIUM rollback-cli-outside-flock).
    # The in-deploy rollback path uses the lock-free _rollback_to_snapshot, so no re-entrancy.
    with _deploy_lock(install_dir) as acquired:
        if not acquired:
            return DeployResult(
                success=False,
                message="deploy already in progress — rollback refused (another deploy holds the lock)",
            )

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
        # Stream with NO 60s short-timeout: a full-stack rollback `up` routinely exceeds it,
        # which used to report rollback FAILURE when it had actually half-succeeded (H#8).
        # --pull from resolve_pull_policy(): offline → never (no network pull during an
        # air-gap rollback), online → missing (recover an image pruned since the snapshot) (H#9).
        rc, tail = _stream_compose(
            ["up", "-d", "--remove-orphans", "--pull", resolve_pull_policy(), *service_names],
            cwd=install_dir,
            sudo_password=sudo_password,
            on_line=lambda line: log.info("rollback up: %s", line),
        )
        if rc != 0:
            log.error("rollback compose up failed: %s", tail)
            return False
        log.info("rollback complete to snapshot %s", snapshot.id)
        return True
    except Exception as exc:
        log.error("rollback failed: %s", exc)
        return False
