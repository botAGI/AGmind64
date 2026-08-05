"""Phase N: concrete install steps.

Каждый step стримит stdout/stderr субпроцесса через callback (LOG events),
обновляет progress percent если можно вычислить, и возвращает
InstallStepResult с success / message / elapsed.

Historically one 2770-line module; split into a package (SPEC-17.1) with the
per-area submodules below. This ``__init__`` keeps the FULL historical import
surface — public and private — so ``from agmind.install.steps import X`` and
``monkeypatch.setattr(steps, "X", ...)`` behave exactly as before the split.
"""

from __future__ import annotations

import json
import os
import shutil  # noqa: F401  # re-export: tests patch `steps.shutil.{copytree,move,copy2,disk_usage}`
import subprocess
import tempfile
import threading
import time
import urllib.request  # noqa: F401  # re-export: tests patch `steps.urllib.request.urlopen`
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path

from agmind.config.env import write_env
from agmind.core.docker_auth import user_docker_config_dir
from agmind.core.env import compose_env_quote, parse_env_file, parse_env_text
from agmind.core.proc import sudo_argv, sudo_stdin_text

# Not used by this module after the split — re-exported because the config-materialization
# chain in `configs.py` resolves it through the package so tests can patch it here
# (`monkeypatch.setattr(steps, "write_private_text", ...)`).
from agmind.core.secrets import write_private_text  # noqa: F401
from agmind.install.ansible_tools import resolve_ansible_command
from agmind.install.orchestrator import (
    DEFAULT_REPO_ROOT,
    InstallConfig,
    InstallStep,
    InstallStepResult,
    ProgressCallback,
    ProgressEvent,  # noqa: F401  # re-export: historical `steps.ProgressEvent`
    ProgressKind,
)
from agmind.install.secret_keys import AUTHELIA_SECRET_KEYS as _AUTHELIA_SECRET_KEYS
from agmind.install.secret_keys import RUNTIME_SECRET_KEYS as _RUNTIME_SECRET_KEYS
from agmind.install.secret_keys import generate_for as _generate_runtime_secret

from ._common import _make_event, _sudo_stdin_payload
from .boot_unit import (
    _AGMIND_STACK_SERVICE_PATH as _AGMIND_STACK_SERVICE_PATH,
)
from .boot_unit import (
    BootUnitStep,
)
from .boot_unit import (
    _agmind_stack_unit as _agmind_stack_unit,
)
from .boot_unit import (
    _selected_compose_profiles as _selected_compose_profiles,
)
from .cloudflare import (
    _CLOUDFLARE_API_BASE as _CLOUDFLARE_API_BASE,
)
from .cloudflare import (
    CloudflareTokenStep,
)
from .cloudflare import (
    _cloudflare_payload_errors as _cloudflare_payload_errors,
)
from .cloudflare import (
    _cloudflare_request_json as _cloudflare_request_json,
)
from .cloudflare import (
    _cloudflare_zone_candidates as _cloudflare_zone_candidates,
)
from .configs import (
    _ALERTMANAGER_MULTICHANNEL_KEYS,
    _ALERTMANAGER_TELEGRAM_KEYS,
    _redact_install_secrets,
    _write_private_text_maybe_sudo,
    _write_runtime_payload_local,
    _write_runtime_payload_sudo,
)
from .configs import (
    _RUNTIME_TARGET_GUARD_SCRIPT as _RUNTIME_TARGET_GUARD_SCRIPT,
)
from .configs import (
    _SMTP_PASSWORD_FILE as _SMTP_PASSWORD_FILE,
)
from .configs import (
    _WEBHOOK_URL_FILE as _WEBHOOK_URL_FILE,
)
from .configs import (
    _assert_sudo_runtime_targets_safe as _assert_sudo_runtime_targets_safe,
)
from .configs import (
    _authelia_argon2_hash as _authelia_argon2_hash,
)
from .configs import (
    _cleanup_path as _cleanup_path,
)
from .configs import (
    _copy_file_atomic as _copy_file_atomic,
)
from .configs import (
    _copytree_atomic as _copytree_atomic,
)
from .configs import (
    _copytree_contents as _copytree_contents,
)
from .configs import (
    _ensure_models_dir as _ensure_models_dir,
)
from .configs import (
    _materialize_runtime_files as _materialize_runtime_files,
)
from .configs import (
    _replace_authelia_password_hash as _replace_authelia_password_hash,
)
from .configs import (
    _replace_path_atomic as _replace_path_atomic,
)
from .configs import (
    _run_sudo_runtime_command as _run_sudo_runtime_command,
)
from .configs import (
    _stage_alertmanager_config as _stage_alertmanager_config,
)
from .configs import (
    _stage_authelia_config as _stage_authelia_config,
)
from .configs import (
    _stage_directory_contents as _stage_directory_contents,
)
from .configs import (
    _stage_prometheus_config as _stage_prometheus_config,
)
from .configs import (
    _stage_runtime_payload as _stage_runtime_payload,
)
from .configs import (
    _stage_single_file_config as _stage_single_file_config,
)
from .configs import (
    _stage_squid_config as _stage_squid_config,
)
from .configs import (
    _sudo_runtime_target_args as _sudo_runtime_target_args,
)
from .configs import (
    _write_secret_file as _write_secret_file,
)
from .configs import (
    build_alertmanager_config as build_alertmanager_config,
)
from .gpu_metrics import (
    _GPU_METRICS_SERVICE_PATH as _GPU_METRICS_SERVICE_PATH,
)
from .gpu_metrics import (
    _GPU_METRICS_TEXTFILE_DIR as _GPU_METRICS_TEXTFILE_DIR,
)
from .gpu_metrics import (
    _GPU_METRICS_TIMER_PATH as _GPU_METRICS_TIMER_PATH,
)
from .gpu_metrics import (
    _GPU_METRICS_TIMER_UNIT as _GPU_METRICS_TIMER_UNIT,
)
from .gpu_metrics import (
    GpuMetricsStep,
)
from .gpu_metrics import (
    _gpu_metrics_service_unit as _gpu_metrics_service_unit,
)
from .gpu_metrics import (
    _host_has_amd_gpu as _host_has_amd_gpu,
)
from .models import ModelDownloadStep

# ---------- helpers ----------


def _env_line(key: str, value: str) -> str:
    # docker-compose env-file quoting (NOT shell): bare for simple values,
    # double-quoted+escaped for space/quote/special so writer↔reader round-trip.
    return f"{key}={compose_env_quote(value) if value else ''}"


def _runtime_env(existing_env: dict[str, str]) -> dict[str, str]:
    values: dict[str, str] = {
        "MINIO_ROOT_USER": existing_env.get("MINIO_ROOT_USER") or "agmind",
        "N8N_TIMEZONE": existing_env.get("N8N_TIMEZONE") or "UTC",
    }
    # Per-key generators live in agmind.install.secret_keys (single source of
    # truth shared with `agmind ops rotate-secrets`): 32-byte token, Authelia
    # 64-char, homarr 64-hex (the base64 default aborts homarr at boot).
    for key in (*_RUNTIME_SECRET_KEYS, *_AUTHELIA_SECRET_KEYS):
        values[key] = existing_env.get(key) or _generate_runtime_secret(key)
    for key in (*_ALERTMANAGER_TELEGRAM_KEYS, *_ALERTMANAGER_MULTICHANNEL_KEYS):
        values[key] = existing_env.get(key, "")
    return values


def _version_key(service_name: str) -> str:
    return f"{service_name.upper().replace('-', '_')}_VERSION"


def _image_tag(image: str) -> str:
    image_without_digest = image.split("@", 1)[0]
    last_slash = image_without_digest.rfind("/")
    last_colon = image_without_digest.rfind(":")
    if last_colon <= last_slash or last_colon == len(image_without_digest) - 1:
        return ""
    return image_without_digest[last_colon + 1 :]


def _image_digest(image: str) -> str:
    if "@" not in image:
        return ""
    return image.rsplit("@", 1)[1].removeprefix("sha256:")


def _runtime_version_env(service_names: list[str]) -> str:
    from agmind import __version__
    from agmind.services.renderer import load_descriptors

    descriptors = load_descriptors()
    if not service_names:
        raise ValueError("no selected services for version.env")
    selected_names = sorted(set(service_names))
    missing = [name for name in selected_names if name not in descriptors]
    if missing:
        raise ValueError(f"unknown selected services for version.env: {', '.join(missing)}")

    lines = [
        "# AGmind runtime version manifest — written by `agmind install`.",
        "# Non-secret file used by operators, backup, and drift reviews.",
        _env_line("AGMIND_VERSION", __version__),
        "",
    ]
    for service_name in selected_names:
        descriptor = descriptors[service_name]
        tag = _image_tag(descriptor.image)
        digest = (descriptor.digest or _image_digest(descriptor.image)).removeprefix("sha256:")
        if not tag and not digest:
            continue
        key = _version_key(service_name)
        lines.append(_env_line(key, tag))
        lines.append(_env_line(f"{key}_IMAGE", descriptor.image))
        if digest:
            lines.append(_env_line(f"{key}_DIGEST", f"sha256:{digest}"))
    return "\n".join(lines) + "\n"


def _parse_existing_runtime_env(config: InstallConfig, env_path: Path) -> dict[str, str]:
    try:
        return parse_env_file(env_path)
    except PermissionError as exc:
        if config.sudo_password is None:
            raise
        result = subprocess.run(
            sudo_argv(["cat", str(env_path)]),
            capture_output=True,
            text=True,
            check=False,
            input=sudo_stdin_text(config.sudo_password),
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or str(exc)).strip()
            raise PermissionError(f"cannot read existing runtime env via sudo: {detail}") from exc
        return parse_env_text(result.stdout)


def _kill_on_cancel(
    proc: subprocess.Popen[str],
    cancel_event: threading.Event | None,
) -> None:
    """Spawn a daemon watchdog that terminates *proc* promptly when *cancel_event*
    fires.

    Without this the worker thread blocks in ``proc.stdout`` / ``proc.wait()`` and the
    whole TUI hangs on Cancel/Close — Textual cannot force-kill a thread worker, so app
    exit waits (up to ~300s) for the blocked thread. The watchdog is a daemon thread,
    so it never itself blocks interpreter shutdown, and it exits on its own when the
    process finishes normally.
    """
    if cancel_event is None:
        return

    def _watch() -> None:
        while not cancel_event.wait(0.25):
            if proc.poll() is not None:
                return  # finished normally
        # cancel fired — terminate, then hard-kill if it lingers
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(5)
            except subprocess.TimeoutExpired:
                proc.kill()

    threading.Thread(target=_watch, name="agmind-cancel-watch", daemon=True).start()


def _stream_subprocess(
    cmd: list[str],
    callback: ProgressCallback,
    step_id: str,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    stdin_payload: bytes | None = None,
    extra_emit: Callable[[str], None] | None = None,
    cancel_event: threading.Event | None = None,
) -> tuple[int, list[str]]:
    """Run subprocess, stream stdout+stderr line-by-line via callback.

    Returns (returncode, captured_lines). `extra_emit` callable получает
    каждую строку и может emit'ить дополнительные ProgressEvent (e.g.
    парсить progress %). Все строки также эмитятся как ProgressKind.LOG.

    If `cancel_event` is provided, a daemon watchdog kills the child when it fires so
    Cancel/Close never hangs the worker on a long subprocess.
    """
    proc_env = os.environ.copy()
    if env:
        proc_env.update(env)
    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        stdin=subprocess.PIPE if stdin_payload is not None else subprocess.DEVNULL,
        cwd=str(cwd) if cwd else None,
        env=proc_env,
        text=True,
        bufsize=1,
    )
    _kill_on_cancel(proc, cancel_event)
    try:
        if stdin_payload is not None and proc.stdin is not None:
            try:
                proc.stdin.write(stdin_payload.decode("utf-8"))
                proc.stdin.close()
            except BrokenPipeError:
                pass

        captured: list[str] = []
        if proc.stdout is not None:
            for raw in proc.stdout:
                line = raw.rstrip()
                if not line:
                    continue
                captured.append(line)
                callback(_make_event(step_id, ProgressKind.LOG, line))
                if extra_emit is not None:
                    try:
                        extra_emit(line)
                    except Exception:
                        pass
        rc = proc.wait()
        return rc, captured
    finally:
        # Never leave a child running / pipes open if the loop or a callback
        # raised before proc.wait().
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        if proc.stdout is not None:
            proc.stdout.close()


def _docker_compose_cmd(
    config: InstallConfig,
    args: list[str],
    env_file: Path | None = None,
    progress: str | None = None,
) -> list[str]:
    # `--progress`/`--env-file` are GLOBAL flags — they go before the subcommand.
    global_flags = ["--progress", progress] if progress is not None else []
    env_args = ["--env-file", str(env_file)] if env_file is not None else []
    compose = ["docker", "compose", *global_flags, *env_args, *args]
    if config.sudo_password is None:
        return compose
    docker_config = _user_docker_config_dir()
    if docker_config:
        compose = ["env", f"DOCKER_CONFIG={docker_config}", *compose]
    return sudo_argv(compose)


def _user_docker_config_dir() -> str | None:
    return user_docker_config_dir()


def _pull_progress_pct(line: str, services: set[str], pulled: set[str]) -> int | None:
    """Coarse pull progress from a `docker compose --progress plain` line.

    Compose emits ``<service> Pulled`` when a service image is fully pulled (and
    layer lines like ``<sha> Downloading``/``Pull complete`` in between). Count
    distinct completed *services* over the total; returns the new pct on a fresh
    completion, else None. Best-effort and side-effecting (mutates *pulled*).
    """
    parts = line.split()
    if len(parts) < 2 or parts[-1] != "Pulled":
        return None
    service = parts[0]
    if service not in services or service in pulled:
        return None
    pulled.add(service)
    total = len(services)
    return int(len(pulled) / total * 100) if total else 0


def _write_compose_env_file(path: Path, values: dict[str, str]) -> None:
    content = "".join(_env_line(key, values[key]) + "\n" for key in sorted(values))
    write_env(path, content, mode=0o600)


# ---------- Step 1: doctor ----------


class DoctorStep(InstallStep):
    """Preflight — `agmind doctor`. Hard fail if any check returns 'fail'."""

    step_id = "doctor"
    label = "Preflight diagnostics"

    def run(self, callback: ProgressCallback, config: InstallConfig) -> InstallStepResult:
        start = time.monotonic()
        from agmind.diagnostics.doctor import run_preflight

        try:
            report = run_preflight()
        except Exception as exc:
            return InstallStepResult(
                step_id=self.step_id,
                success=False,
                message=f"doctor crashed: {exc}",
                elapsed=timedelta(seconds=time.monotonic() - start),
            )

        for check in report.checks:
            glyph = {"ok": "✓", "warn": "⚠", "fail": "✗", "skip": "·"}.get(check.status, "?")
            callback(
                _make_event(
                    self.step_id,
                    ProgressKind.LOG,
                    f"  {glyph}  {check.name:<22} {check.message}",
                )
            )

        elapsed = timedelta(seconds=time.monotonic() - start)
        ok_n = sum(1 for c in report.checks if c.status == "ok")
        warn_n = sum(1 for c in report.checks if c.status == "warn")
        if report.has_failures:
            failed = [c.name for c in report.checks if c.status == "fail"]
            return InstallStepResult(
                step_id=self.step_id,
                success=False,
                message=f"hard fail in checks: {', '.join(failed)}",
                elapsed=elapsed,
            )
        return InstallStepResult(
            step_id=self.step_id,
            success=True,
            message=f"{ok_n} ok / {warn_n} warn / 0 fail",
            elapsed=elapsed,
        )


# ---------- Step 3: bootstrap (Ansible, sudo) ----------


class BootstrapStep(InstallStep):
    """Run `ansible-playbook install.yml --tags bootstrap` с sudo password.

    Sudo password передаётся как `ansible_become_password` внутри extra-vars,
    записанных в РЕАЛЬНЫЙ temp-файл с правами 0600 на tmpfs (`/dev/shm`, в RAM —
    не на персистентный диск) и переданный через `--extra-vars @<path>`. Файл
    удаляется в `finally`. В argv видно только путь, не сами секреты.

    ВАЖНО: НЕ передавать секреты Ansible через pipe-FD (`/dev/fd/N`) ни в одном
    file-аргументе. Ansible прогоняет такие пути через `os.path.realpath`, а realpath
    не резолвит pipe-FD: `/dev/fd/N` → `/proc/<pid>/fd/pipe:[inode]` (не существует),
    что ломает И `--become-password-file /dev/fd/N` ("The password file ... was not
    found"), И `--extra-vars @/dev/fd/N` ("Unable to retrieve file contents ...
    /proc/<pid>/fd/pipe:[inode]"). Оба падают rc=1. Отсюда — реальный temp-файл.
    """

    step_id = "bootstrap"
    label = "System bootstrap (apt, groups, dirs)"

    PLAYBOOK_RELATIVE = "ansible/install.yml"
    ANSIBLE_DIR_RELATIVE = "ansible"
    GALAXY_DIR_NAME = ".galaxy"
    ANSIBLE_TAGS = "bootstrap"

    def run(self, callback: ProgressCallback, config: InstallConfig) -> InstallStepResult:
        start = time.monotonic()
        if config.sudo_password is None:
            return InstallStepResult(
                step_id=self.step_id,
                success=False,
                message="sudo password not provided (cannot run apt/usermod)",
                elapsed=timedelta(seconds=time.monotonic() - start),
            )

        ansible_dir = DEFAULT_REPO_ROOT / self.ANSIBLE_DIR_RELATIVE
        playbook = DEFAULT_REPO_ROOT / self.PLAYBOOK_RELATIVE
        if not playbook.exists():
            return InstallStepResult(
                step_id=self.step_id,
                success=False,
                message=f"playbook not found: {playbook}",
                elapsed=timedelta(seconds=time.monotonic() - start),
            )

        requirements = ansible_dir / "requirements.yml"
        if requirements.exists():
            galaxy_dir = ansible_dir / self.GALAXY_DIR_NAME
            galaxy_cmd = [
                resolve_ansible_command("ansible-galaxy"),
                "collection",
                "install",
                "-r",
                str(requirements),
                "-p",
                str(galaxy_dir),
            ]
            # requirements.yml pins ranges, so even pre-staged collections trigger a network
            # version-resolution call to galaxy.ansible.com. BootstrapStep is in default_steps
            # and runs BEFORE the offline-aware DeployStep, so without --offline an air-gap
            # install aborts HERE (same orphaned-guard class as the earlier offline-pull bug).
            # --offline (ansible-core ≥2.16) uses only the collections pre-staged into ansible/
            # .galaxy — see docs/installation/offline-install.md.
            if _offline_install_enabled():
                galaxy_cmd.append("--offline")
            rc, _ = _stream_subprocess(
                galaxy_cmd,
                callback,
                self.step_id,
                cwd=ansible_dir,
                cancel_event=self.cancel_event,
            )
            if rc != 0:
                return InstallStepResult(
                    step_id=self.step_id,
                    success=False,
                    message=f"ansible-galaxy collection install failed with rc={rc}",
                    elapsed=timedelta(seconds=time.monotonic() - start),
                )

        # Sudo password + sensitive vars reach Ansible via `--extra-vars @<file>`. We
        # write a REAL temp file (0600) on a tmpfs (/dev/shm, RAM-backed — never hits
        # persistent disk) and unlink it in `finally`. We must NOT use a pipe FD
        # (`/dev/fd/N`) for ANY Ansible file argument: Ansible realpath-canonicalizes
        # those paths and cannot resolve a pipe FD — `/dev/fd/N` becomes
        # `/proc/<pid>/fd/pipe:[inode]` (ENOENT), which breaks BOTH
        # `--become-password-file` and `--extra-vars @/dev/fd/N`. The visible argv
        # contains only the temp-file path, never the secret values.
        secrets_dir = (
            "/dev/shm" if os.path.isdir("/dev/shm") and os.access("/dev/shm", os.W_OK) else None
        )
        vars_fd, vars_path = tempfile.mkstemp(
            prefix=".agmind-bootstrap-", suffix=".json", dir=secrets_dir
        )
        proc: subprocess.Popen[str] | None = None
        try:
            extra_vars_payload = json.dumps(
                {
                    "agmind_domain": config.domain,
                    "agmind_cf_api_token": config.cf_api_token,
                    "ansible_become_password": config.sudo_password,
                    # ansible/install.yml gates its domain/CF-token asserts on this: a
                    # no-traefik headless install has no public edge to TLS-terminate, so
                    # an empty domain/token (already permitted by install_cmd.py) must not
                    # die in the playbook (P0.7).
                    "agmind_edge_enabled": "traefik" in config.services,
                },
                ensure_ascii=False,
            ).encode("utf-8")
            # mkstemp created vars_path with 0600 perms; write the secret through its fd.
            os.write(vars_fd, extra_vars_payload)
            os.close(vars_fd)
            vars_fd = -1

            cmd = [
                resolve_ansible_command("ansible-playbook"),
                str(playbook),
                "--tags",
                self.ANSIBLE_TAGS,
                "--extra-vars",
                f"@{vars_path}",
                # Use the repo inventory (localhost ∈ agmind_nodes/agmind_master
                # + agmind_* vars). A bare `-i localhost,` puts localhost only in
                # the implicit `all` group, so the bootstrap play (hosts:
                # agmind_nodes) matches ZERO hosts and silently no-ops every task
                # incl. the data-dir chown — leaving dirs root:root → crash-loops.
                "-i",
                "inventory/hosts.yml",
            ]

            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                text=True,
                bufsize=1,
                cwd=str(ansible_dir),
            )
            _kill_on_cancel(proc, self.cancel_event)
            if proc.stdout is not None:
                for raw in proc.stdout:
                    line = raw.rstrip()
                    if not line:
                        continue
                    line = _redact_install_secrets(line, config)
                    callback(_make_event(self.step_id, ProgressKind.LOG, line))
            rc = proc.wait()
        finally:
            if proc is not None:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait()
                if proc.stdout is not None:
                    proc.stdout.close()
            if vars_fd >= 0:
                try:
                    os.close(vars_fd)
                except OSError:
                    pass
            try:
                os.unlink(vars_path)
            except OSError:
                pass

        elapsed = timedelta(seconds=time.monotonic() - start)
        if rc != 0:
            return InstallStepResult(
                step_id=self.step_id,
                success=False,
                message=f"ansible-playbook failed with rc={rc}",
                elapsed=elapsed,
            )
        return InstallStepResult(
            step_id=self.step_id,
            success=True,
            message="apt prereqs + groups + dirs ready",
            elapsed=elapsed,
        )


# ---------- Step 3: docker image pull ----------


class ComposeConfigStep(InstallStep):
    """Validate the exact selected Compose config before real pulls/deploy."""

    step_id = "compose_config"
    label = "Validate Docker Compose config"

    def run(self, callback: ProgressCallback, config: InstallConfig) -> InstallStepResult:
        start = time.monotonic()
        if not config.services:
            return InstallStepResult(
                step_id=self.step_id,
                success=False,
                message="no selected services for compose config",
                elapsed=timedelta(seconds=time.monotonic() - start),
            )
        import tempfile

        from agmind.services.renderer import render_to_string

        try:
            # traefik_enabled selection-derived (P0.3 / 15-04): validate the exact posture
            # that will deploy — local set (no traefik) must not force routing labels.
            compose_text = render_to_string(
                services=config.services if config.services else None,
                domain=config.domain,
            )
        except Exception as exc:
            return InstallStepResult(
                step_id=self.step_id,
                success=False,
                message=f"compose render failed: {exc}",
                elapsed=timedelta(seconds=time.monotonic() - start),
            )

        with tempfile.TemporaryDirectory(prefix="agmind-compose-config-") as tmp:
            tmpdir = Path(tmp)
            (tmpdir / "docker-compose.yml").write_text(compose_text, encoding="utf-8")
            compose_env = _parse_existing_runtime_env(config, config.install_dir / ".env")
            compose_env_file = tmpdir / ".env"
            _write_compose_env_file(compose_env_file, compose_env)
            rc, _ = _stream_subprocess(
                _docker_compose_cmd(config, ["config", "--quiet"], env_file=compose_env_file),
                callback,
                self.step_id,
                cwd=tmpdir,
                stdin_payload=_sudo_stdin_payload(config),
                cancel_event=self.cancel_event,
            )

        elapsed = timedelta(seconds=time.monotonic() - start)
        if rc != 0:
            return InstallStepResult(
                step_id=self.step_id,
                success=False,
                message=f"docker compose config rc={rc}",
                elapsed=elapsed,
            )
        return InstallStepResult(
            step_id=self.step_id,
            success=True,
            message="compose config OK",
            elapsed=elapsed,
        )


def _offline_install_enabled() -> bool:
    """True when ``AGMIND_OFFLINE`` requests an air-gap install (no network pulls).

    Delegates to :func:`agmind.deploy.runner._offline_pull_enabled` — the single source
    of truth lives in the deploy layer (the REAL pull path) so the flag can't be honored
    in one place and ignored in another. docker save/load strips an image's RepoDigest, so
    a digest-pinned ``pull --policy missing`` re-pulls from the network and fails in an
    air-gap; offline switches the pull to ``--policy never`` (DeployStep then runs
    ``up --pull never``), using preloaded images.
    """
    from agmind.deploy.runner import _offline_pull_enabled

    return _offline_pull_enabled()


class ImagePullStep(InstallStep):
    """`docker compose pull` после bootstrap (user уже в docker group).

    Требует чтобы compose был отрендерен — обычно делается deploy step,
    но pull раньше = lower TTR (time to running). Используем temporary
    render через `agmind render compose` без write.
    """

    step_id = "image_pull"
    label = "Docker image pull"

    def run(self, callback: ProgressCallback, config: InstallConfig) -> InstallStepResult:
        start = time.monotonic()
        if not config.services:
            return InstallStepResult(
                step_id=self.step_id,
                success=False,
                message="no selected services for image pull",
                elapsed=timedelta(seconds=time.monotonic() - start),
            )
        # Lazy import чтобы не тянуть тяжёлый renderer на загрузке модуля.
        # Render compose в temp dir чтобы вызвать `docker compose pull`.
        import tempfile

        from agmind.services.renderer import render_to_string

        try:
            # traefik_enabled selection-derived (P0.3 / 15-04) — same posture as deploy.
            compose_text = render_to_string(
                services=config.services if config.services else None,
                domain=config.domain,
            )
        except Exception as exc:
            return InstallStepResult(
                step_id=self.step_id,
                success=False,
                message=f"compose render failed: {exc}",
                elapsed=timedelta(seconds=time.monotonic() - start),
            )

        with tempfile.TemporaryDirectory(prefix="agmind-pull-") as tmp:
            tmpdir = Path(tmp)
            (tmpdir / "docker-compose.yml").write_text(compose_text, encoding="utf-8")
            compose_env = _parse_existing_runtime_env(config, config.install_dir / ".env")
            compose_env_file = tmpdir / ".env"
            _write_compose_env_file(compose_env_file, compose_env)
            # NO --quiet: it suppressed every per-layer line and froze the bar at 0%
            # during multi-GB pulls. --progress plain (global flag) streams readable,
            # non-ANSI lines that RichLog can render; --policy missing keeps it idempotent.
            # AGMIND_OFFLINE → --policy never so an air-gap install never hits the network
            # (images are preloaded via `docker load`; see docs/installation/offline-install.md).
            from agmind.deploy.runner import resolve_pull_policy

            offline = _offline_install_enabled()
            if offline:
                callback(
                    _make_event(
                        self.step_id,
                        ProgressKind.LOG,
                        "AGMIND_OFFLINE: skipping network pull (--policy never); "
                        "images must be preloaded via `docker load`",
                    )
                )
            cmd = _docker_compose_cmd(
                config,
                ["pull", "--policy", resolve_pull_policy(offline)],
                env_file=compose_env_file,
                progress="plain",
            )
            service_set = set(config.services)
            pulled: set[str] = set()

            def emit_pull_progress(line: str) -> None:
                pct = _pull_progress_pct(line, service_set, pulled)
                if pct is None:
                    return
                callback(
                    _make_event(
                        self.step_id,
                        ProgressKind.PROGRESS,
                        f"pulled {len(pulled)}/{len(service_set)} images",
                        pct=pct,
                    )
                )

            rc, _ = _stream_subprocess(
                cmd,
                callback,
                self.step_id,
                cwd=tmpdir,
                stdin_payload=_sudo_stdin_payload(config),
                extra_emit=emit_pull_progress,
                cancel_event=self.cancel_event,
            )

        elapsed = timedelta(seconds=time.monotonic() - start)
        if rc != 0:
            return InstallStepResult(
                step_id=self.step_id,
                success=False,
                message=f"docker compose pull rc={rc}",
                elapsed=elapsed,
            )
        return InstallStepResult(
            step_id=self.step_id,
            success=True,
            message="images pulled",
            elapsed=elapsed,
        )


# ---------- Step 5: compose deploy ----------


# First-run model load (multi-GB GGUF -> unified memory) can take many minutes; the
# runner default of 300s would false-rollback an otherwise-healthy stack (BREA02). The
# sizing helper lives in agmind.deploy.runner (the actual consumer of the wait budget) —
# re-exported here under the historical private name so existing callers/tests are
# unaffected by the relocation.
from agmind.deploy.runner import _HEALTHCHECK_TIMEOUT_FLOOR
from agmind.deploy.runner import healthcheck_timeout_for as _healthcheck_timeout_for


class DeployStep(InstallStep):
    """Run `agmind deploy --apply` (reuse Phase L.B runner)."""

    # Floor for the first-run healthcheck budget; the actual timeout is sized per
    # selection by `_healthcheck_timeout_for` (slowest start_period + load margin).
    HEALTHCHECK_TIMEOUT = _HEALTHCHECK_TIMEOUT_FLOOR

    step_id = "deploy"
    label = "Deploy compose stack + healthcheck"

    def run(self, callback: ProgressCallback, config: InstallConfig) -> InstallStepResult:
        start = time.monotonic()
        if not config.services:
            return InstallStepResult(
                step_id=self.step_id,
                success=False,
                message="no selected services for deploy",
                elapsed=timedelta(seconds=time.monotonic() - start),
            )
        from agmind.deploy.runner import deploy as _deploy

        def deploy_progress(step: str, msg: str) -> None:
            safe_step = _redact_install_secrets(step, config)
            safe_msg = _redact_install_secrets(msg, config)
            callback(_make_event(self.step_id, ProgressKind.LOG, f"[{safe_step}] {safe_msg}"))

        healthcheck_timeout, driver = _healthcheck_timeout_for(config.services)
        driver_note = f"driven by {driver}" if driver else f"floor ({self.HEALTHCHECK_TIMEOUT}s)"
        callback(
            _make_event(
                self.step_id,
                ProgressKind.LOG,
                f"healthcheck timeout: {healthcheck_timeout} s, {driver_note}",
            )
        )

        try:
            result = _deploy(
                profiles=[],  # render по services list, см. config
                install_dir=config.install_dir,
                domain=config.domain,
                apply=True,
                no_prompt=True,
                progress=deploy_progress,
                services=config.services,
                # config.services is ALREADY closure-resolved (expand_selected_services_for_setup)
                # AND model-normalized (normalize_model_fields_and_services removed llama-llm for
                # model_id='skip'). Re-expanding in the runner would re-pull llama-llm's
                # llm_inference provider and re-add the skipped LLM → model-less llama-llm →
                # unhealthy → install fails (P0 deploy-render-divergence).
                expand_closure=False,
                sudo_password=config.sudo_password,
                # First-run deploy must outlast a multi-GB LLM load; the runner default
                # (300s) false-rolls-back an otherwise-healthy stack (BREA02). Sized
                # per-selection from the slowest start_period (see _healthcheck_timeout_for).
                healthcheck_timeout=healthcheck_timeout,
                # Let Cancel break out of the long healthcheck wait promptly.
                cancel_event=self.cancel_event,
            )
        except Exception as exc:
            return InstallStepResult(
                step_id=self.step_id,
                success=False,
                message=f"deploy crashed: {exc}",
                elapsed=timedelta(seconds=time.monotonic() - start),
            )

        elapsed = timedelta(seconds=time.monotonic() - start)
        if not result.success:
            extra = " (rolled back)" if result.rollback_performed else ""
            return InstallStepResult(
                step_id=self.step_id,
                success=False,
                message=f"{result.message}{extra}",
                elapsed=elapsed,
            )
        return InstallStepResult(
            step_id=self.step_id,
            success=True,
            message=result.message,
            elapsed=elapsed,
        )


def _check_proxmox_config_staged(config: InstallConfig) -> str | None:
    """proxmox-exporter mounts ``/etc/agmind/proxmox-exporter/pve.yml`` :ro (a single FILE).
    ``agmind install`` never collects PVE credentials, so unless the operator provisioned that
    file (the ansible ``services`` role writes it from ``agmind_proxmox_exporter_*`` vars) Docker
    creates a DIRECTORY at the :ro source and the exporter crash-loops. Fail fast with guidance
    instead of shipping a crash-loop (review MEDIUM proxmox-pve-config-not-staged)."""
    if "proxmox-exporter" not in config.services:
        return None
    pve = config.config_dir / "proxmox-exporter" / "pve.yml"
    if pve.is_file():
        return None
    return (
        f"proxmox-exporter selected but {pve} is not provisioned. `agmind install` does not "
        "collect Proxmox API credentials — provision that file first (the ansible `services` "
        "role writes it from agmind_proxmox_exporter_user / token_name / token_value), or "
        "deselect the proxmox profile."
    )


# ---------- step list factory ----------


class EnvWriteStep(InstallStep):
    """Write `/opt/agmind/.env` с runtime settings (model file, ctx, KV cache).

    `templates/services/llama-llm.yaml` ссылается на эти env vars через
    docker compose ${VAR} substitution.
    """

    step_id = "env_write"
    label = "Write runtime .env"

    def run(self, callback: ProgressCallback, config: InstallConfig) -> InstallStepResult:
        start = time.monotonic()
        env_path = config.install_dir / ".env"
        try:
            version_env_text = _runtime_version_env(config.services)
        except ValueError as exc:
            return InstallStepResult(
                step_id=self.step_id,
                success=False,
                message=str(exc),
                elapsed=timedelta(seconds=time.monotonic() - start),
            )
        proxmox_error = _check_proxmox_config_staged(config)
        if proxmox_error is not None:
            return InstallStepResult(
                step_id=self.step_id,
                success=False,
                message=proxmox_error,
                elapsed=timedelta(seconds=time.monotonic() - start),
            )
        runtime_env = _runtime_env(_parse_existing_runtime_env(config, env_path))

        # Phase M5.2: separate LLM/Embed/Rerank env vars так чтобы templates
        # параметризовали каждый llama-* service независимо. Legacy AGMIND_CTX_SIZE
        # сохраняется для backward compat с уже-deployed compose stacks (template
        # llama-llm.yaml имеет ${AGMIND_CTX_SIZE:-fallback}).
        lines = [
            "# AGmind runtime env — written by `agmind install` Phase M5.2.",
            "# Hand-edit allowed; existing runtime secrets are preserved on rerun.",
            _env_line("AGMIND_DOMAIN", config.domain),
            "",
            "# ---- LLM (token generation) ----",
            _env_line("AGMIND_MODEL_FILE", config.model_file or ""),
            _env_line("AGMIND_LLM_CTX_SIZE", str(config.ctx_size)),
            _env_line("AGMIND_LLM_KV_CACHE", config.kv_cache_type),
            _env_line("AGMIND_LLM_THREADS", str(config.threads)),
            _env_line("AGMIND_LLM_PARALLEL", str(config.parallel_slots)),
            "",
            "# Legacy aliases (pre-M5.2 templates) — same values as LLM block.",
            _env_line("AGMIND_CTX_SIZE", str(config.ctx_size)),
            _env_line("AGMIND_KV_CACHE", config.kv_cache_type),
            _env_line("AGMIND_THREADS", str(config.threads)),
            _env_line("AGMIND_PARALLEL", str(config.parallel_slots)),
            "",
            "# ---- Embed (dense embeddings for RAG) ----",
            _env_line("AGMIND_EMBED_FILE", config.embed_file or ""),
            _env_line("AGMIND_EMBED_CTX_SIZE", str(config.embed_ctx_size)),
            _env_line("AGMIND_EMBED_KV_CACHE", config.embed_kv_cache),
            _env_line("AGMIND_EMBED_PARALLEL", str(config.embed_parallel)),
            # Derived from CTX_SIZE/PARALLEL, not chosen: see InstallConfig.embed_batch.
            _env_line("AGMIND_EMBED_BATCH", str(config.embed_batch)),
            "",
            "# ---- Rerank (cross-encoder ordering) ----",
            _env_line("AGMIND_RERANK_FILE", config.rerank_file or ""),
            _env_line("AGMIND_RERANK_CTX_SIZE", str(config.rerank_ctx_size)),
            _env_line("AGMIND_RERANK_BATCH", str(config.rerank_batch)),
            "",
            "# ---- Alerting (optional Telegram receiver) ----",
            _env_line(
                "AGMIND_ALERT_TELEGRAM_CHAT_ID",
                runtime_env["AGMIND_ALERT_TELEGRAM_CHAT_ID"],
            ),
            _env_line(
                "AGMIND_ALERT_TELEGRAM_BOT_TOKEN",
                runtime_env["AGMIND_ALERT_TELEGRAM_BOT_TOKEN"],
            ),
            "# ---- Alerting (optional email + webhook channels; set then re-run install) ----",
            _env_line("SMTP_SMARTHOST", runtime_env["SMTP_SMARTHOST"]),
            _env_line("SMTP_FROM", runtime_env["SMTP_FROM"]),
            _env_line("SMTP_TO", runtime_env["SMTP_TO"]),
            _env_line("SMTP_AUTH_USERNAME", runtime_env["SMTP_AUTH_USERNAME"]),
            _env_line("SMTP_AUTH_PASSWORD", runtime_env["SMTP_AUTH_PASSWORD"]),
            _env_line("ALERT_WEBHOOK_URL", runtime_env["ALERT_WEBHOOK_URL"]),
            "",
            "# ---- Runtime service credentials (Compose requires non-empty values) ----",
            _env_line("POSTGRES_PASSWORD", runtime_env["POSTGRES_PASSWORD"]),
            _env_line("GRAFANA_PASSWORD", runtime_env["GRAFANA_PASSWORD"]),
            _env_line("MYSQL_ROOT_PASSWORD", runtime_env["MYSQL_ROOT_PASSWORD"]),
            _env_line("MINIO_ROOT_USER", runtime_env["MINIO_ROOT_USER"]),
            _env_line("MINIO_ROOT_PASSWORD", runtime_env["MINIO_ROOT_PASSWORD"]),
            _env_line("REDIS_PASSWORD", runtime_env["REDIS_PASSWORD"]),
            _env_line("N8N_ENCRYPTION_KEY", runtime_env["N8N_ENCRYPTION_KEY"]),
            _env_line("N8N_TIMEZONE", runtime_env["N8N_TIMEZONE"]),
            _env_line(
                "HOMARR_SECRET_ENCRYPTION_KEY",
                runtime_env["HOMARR_SECRET_ENCRYPTION_KEY"],
            ),
            _env_line(
                "DIFY_PLUGIN_DAEMON_KEY",
                runtime_env["DIFY_PLUGIN_DAEMON_KEY"],
            ),
            _env_line(
                "DIFY_PLUGIN_INNER_API_KEY",
                runtime_env["DIFY_PLUGIN_INNER_API_KEY"],
            ),
            "",
            "# ---- Authelia (security profile) — read by the container as AUTHELIA_* ----",
            _env_line("AUTHELIA_SESSION_SECRET", runtime_env["AUTHELIA_SESSION_SECRET"]),
            _env_line(
                "AUTHELIA_STORAGE_ENCRYPTION_KEY",
                runtime_env["AUTHELIA_STORAGE_ENCRYPTION_KEY"],
            ),
            _env_line(
                "AUTHELIA_IDENTITY_VALIDATION_RESET_PASSWORD_JWT_SECRET",
                runtime_env["AUTHELIA_IDENTITY_VALIDATION_RESET_PASSWORD_JWT_SECRET"],
            ),
        ]
        # Divergence guard (Правило #11): the hand-maintained list above must never
        # silently drop a generated secret when a new key is added to secret_keys.py.
        # Append every generated secret not already emitted, so EnvWriteStep and
        # RUNTIME_SECRET_KEYS cannot diverge — the exact gap class that lets a
        # generated-but-never-emitted secret fall through (compose `${VAR:?}` then reds
        # on a fresh deploy while CI hand-injected the values and stayed green).
        _emitted = {
            ln.split("=", 1)[0] for ln in lines if "=" in ln and not ln.lstrip().startswith("#")
        }
        _ungrouped = [
            key for key in (*_RUNTIME_SECRET_KEYS, *_AUTHELIA_SECRET_KEYS) if key not in _emitted
        ]
        if _ungrouped:
            lines.append("")
            lines.append("# ---- Additional generated secrets (divergence guard) ----")
            lines.extend(_env_line(key, runtime_env[key]) for key in _ungrouped)
        env_text = "\n".join(lines) + "\n"
        try:
            _write_runtime_payload_local(config, env_text, version_env_text, callback, self.step_id)
        except PermissionError as exc:
            if config.sudo_password is None:
                return InstallStepResult(
                    step_id=self.step_id,
                    success=False,
                    message=f"cannot write runtime files without sudo: {exc}",
                    elapsed=timedelta(seconds=time.monotonic() - start),
                )
            try:
                _write_runtime_payload_sudo(
                    config, env_text, version_env_text, callback, self.step_id
                )
            except OSError as sudo_exc:
                return InstallStepResult(
                    step_id=self.step_id,
                    success=False,
                    message=f"sudo runtime file write failed: {sudo_exc}",
                    elapsed=timedelta(seconds=time.monotonic() - start),
                )
        except OSError as exc:
            return InstallStepResult(
                step_id=self.step_id,
                success=False,
                message=f"cannot write runtime files: {exc}",
                elapsed=timedelta(seconds=time.monotonic() - start),
            )
        callback(
            _make_event(
                self.step_id,
                ProgressKind.LOG,
                f"wrote {env_path} with model={config.model_file} ctx={config.ctx_size}",
            )
        )
        return InstallStepResult(
            step_id=self.step_id,
            success=True,
            message=f".env written ({len(lines)} vars)",
            elapsed=timedelta(seconds=time.monotonic() - start),
        )


class CredentialsStep(InstallStep):
    """Write ``${install_dir}/credentials.txt`` (chmod 600) from descriptors + rendered ``.env``.

    Final, best-effort step: gives the operator a persistent, human-readable list of URLs /
    logins / passwords plus copy-paste OpenAI-compatible model-endpoint blocks. Never fails the
    install — the stack is already deployed; this is a convenience artifact.
    """

    step_id = "credentials"
    label = "Write credentials.txt"

    def run(self, callback: ProgressCallback, config: InstallConfig) -> InstallStepResult:
        start = time.monotonic()
        from datetime import UTC, datetime

        from agmind.services.access import build_access_report, render_credentials_txt
        from agmind.services.renderer import load_descriptors

        try:
            selected = set(config.services)
            descriptors = {n: d for n, d in load_descriptors().items() if n in selected}
            env_path = config.install_dir / ".env"
            # Read via the sudo-aware helper, NOT bare parse_env_file: the .env is root:root 0600
            # (written through the sudo payload path), so a non-root install user hits
            # PermissionError and credentials.txt is silently skipped — the operator then has no
            # record of the generated passwords. _parse_existing_runtime_env falls back to
            # `sudo cat` with the install's sudo password.
            env = _parse_existing_runtime_env(config, env_path) if env_path.exists() else {}
            report = build_access_report(descriptors, env, domain=config.domain)
            generated_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
            text = render_credentials_txt(
                report, generated_at=generated_at, llama_model=config.model_file
            )
            creds_path = config.install_dir / "credentials.txt"
            _write_private_text_maybe_sudo(config, creds_path, text, callback, self.step_id)
        except Exception as exc:
            return InstallStepResult(
                step_id=self.step_id,
                success=True,
                message=f"credentials.txt skipped ({exc})",
                elapsed=timedelta(seconds=time.monotonic() - start),
            )
        return InstallStepResult(
            step_id=self.step_id,
            success=True,
            message=f"credentials.txt written ({len(report)} endpoints)",
            elapsed=timedelta(seconds=time.monotonic() - start),
        )


def default_steps() -> list[InstallStep]:
    """Stock install pipeline. Order matters."""
    return [
        DoctorStep(),
        CloudflareTokenStep(),
        BootstrapStep(),
        EnvWriteStep(),  # before compose/deploy — compose parses ${VAR:?} guards
        ComposeConfigStep(),  # fail fast before deploy runner pulls images
        ModelDownloadStep(),
        DeployStep(),
        # After deploy: provision the host AMD-GPU textfile exporter timer (observability +
        # AMD only) so node-exporter's :ro textfile mount is fed and the Grafana GPU dashboard
        # populates. No-op on non-AMD / non-observability installs.
        GpuMetricsStep(),
        # After deploy: arm the systemd boot unit so an unclean reboot brings the whole stack back
        # depends_on-ordered (a profiled container like prometheus would otherwise stay down and
        # Grafana would lose its datasource — live 2026-06-13 power-loss).
        BootUnitStep(),
        CredentialsStep(),  # final: persist credentials.txt for the operator
    ]


__all__ = [
    "BootUnitStep",
    "BootstrapStep",
    "CloudflareTokenStep",
    "ComposeConfigStep",
    "CredentialsStep",
    "DeployStep",
    "DoctorStep",
    "EnvWriteStep",
    "GpuMetricsStep",
    "ImagePullStep",
    "ModelDownloadStep",
    "default_steps",
]
