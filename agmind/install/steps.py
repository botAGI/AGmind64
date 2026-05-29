"""Phase N: concrete install steps.

Каждый step стримит stdout/stderr субпроцесса через callback (LOG events),
обновляет progress percent если можно вычислить, и возвращает
InstallStepResult с success / message / elapsed.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

from agmind.config.env import write_env
from agmind.core.env import compose_env_quote, parse_env_file, parse_env_text
from agmind.core.secrets import generate_secret, write_private_text
from agmind.install.ansible_tools import resolve_ansible_command
from agmind.install.orchestrator import (
    DEFAULT_REPO_ROOT,
    InstallConfig,
    InstallStep,
    InstallStepResult,
    ProgressCallback,
    ProgressEvent,
    ProgressKind,
)

# ---------- helpers ----------


_RUNTIME_SECRET_KEYS = (
    "POSTGRES_PASSWORD",
    "GRAFANA_PASSWORD",
    "MYSQL_ROOT_PASSWORD",
    "MINIO_ROOT_PASSWORD",
    "REDIS_PASSWORD",
    "N8N_ENCRYPTION_KEY",
    "HOMARR_SECRET_ENCRYPTION_KEY",
)

_RUNTIME_TARGET_GUARD_SCRIPT = r"""
set -eu
while [ "$#" -gt 0 ]; do
    kind=$1
    path=$2
    shift 2
    if [ -L "$path" ]; then
        echo "runtime ${kind} target must not be a symlink: ${path}" >&2
        exit 1
    fi
    if [ ! -e "$path" ]; then
        continue
    fi
    case "$kind" in
        directory)
            if [ ! -d "$path" ]; then
                echo "runtime directory target must be a real directory: ${path}" >&2
                exit 1
            fi
            ;;
        file)
            if [ ! -f "$path" ]; then
                echo "runtime file target must be a regular file: ${path}" >&2
                exit 1
            fi
            ;;
        *)
            echo "unknown runtime target kind: ${kind}" >&2
            exit 1
            ;;
    esac
done
"""


def _redact_install_secrets(text: str, config: InstallConfig) -> str:
    redacted = text
    for secret in (config.cf_api_token, config.sudo_password):
        if secret:
            redacted = redacted.replace(secret, "***")
    return redacted


def _copytree_contents(source: Path, target: Path) -> None:
    if not source.is_dir():
        raise FileNotFoundError(f"required runtime template directory missing: {source}")
    target.mkdir(parents=True, exist_ok=True)
    for item in source.iterdir():
        destination = target / item.name
        if item.is_dir():
            _copytree_atomic(item, destination)
        else:
            _copy_file_atomic(item, destination)


def _cleanup_path(path: Path) -> None:
    if path.is_symlink():
        path.unlink(missing_ok=True)
        return
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
        return
    try:
        path.unlink()
    except FileNotFoundError:
        pass


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


def _copytree_atomic(source: Path, target: Path) -> None:
    staged = target.with_name(f".{target.name}.tmp")
    _cleanup_path(staged)
    try:
        shutil.copytree(source, staged)
        _replace_path_atomic(staged, target)
    except Exception:
        _cleanup_path(staged)
        raise


def _copy_file_atomic(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    staged = target.with_name(f".{target.name}.tmp")
    _cleanup_path(staged)
    try:
        shutil.copy2(source, staged)
        _replace_path_atomic(staged, target)
    except Exception:
        _cleanup_path(staged)
        raise


def _write_secret_file(path: Path, value: str) -> None:
    secret_dir = path.parent
    if secret_dir.exists() and (secret_dir.is_symlink() or not secret_dir.is_dir()):
        raise OSError(f"runtime secret directory must be a real directory: {secret_dir}")
    secret_dir.mkdir(parents=True, exist_ok=True)
    secret_dir.chmod(0o700)
    write_private_text(path, value)


def _stage_prometheus_config(observability_dir: Path, prometheus_dir: Path) -> None:
    staged = prometheus_dir.with_name(f".{prometheus_dir.name}.tmp")
    _cleanup_path(staged)
    try:
        staged.mkdir(parents=True, exist_ok=True)
        _copy_file_atomic(observability_dir / "prometheus.yml", staged / "prometheus.yml")
        _copytree_contents(observability_dir / "prometheus" / "rules", staged / "rules")
        _replace_path_atomic(staged, prometheus_dir)
    except Exception:
        _cleanup_path(staged)
        raise


def _stage_single_file_config(source: Path, target_dir: Path, target_name: str) -> None:
    staged = target_dir.with_name(f".{target_dir.name}.tmp")
    _cleanup_path(staged)
    try:
        staged.mkdir(parents=True, exist_ok=True)
        _copy_file_atomic(source, staged / target_name)
        _replace_path_atomic(staged, target_dir)
    except Exception:
        _cleanup_path(staged)
        raise


def _stage_directory_contents(source: Path, target: Path) -> None:
    staged = target.with_name(f".{target.name}.tmp")
    _cleanup_path(staged)
    try:
        _copytree_contents(source, staged)
        _replace_path_atomic(staged, target)
    except Exception:
        _cleanup_path(staged)
        raise


def _materialize_runtime_files(
    config: InstallConfig,
    callback: ProgressCallback,
    step_id: str,
) -> None:
    selected = set(config.services)
    templates_dir = DEFAULT_REPO_ROOT / "templates"
    data_dir = config.models_dir.parent

    if "traefik" in selected:
        if config.cf_api_token:
            _write_secret_file(data_dir / "secrets" / "cf_dns_api_token", config.cf_api_token)
        _stage_directory_contents(
            templates_dir / "traefik" / "dynamic",
            data_dir / "traefik" / "dynamic",
        )
        (data_dir / "traefik" / "letsencrypt").mkdir(parents=True, exist_ok=True)

    observability_dir = templates_dir / "observability"
    if "prometheus" in selected:
        _stage_prometheus_config(observability_dir, config.config_dir / "prometheus")
    if "grafana" in selected:
        _stage_directory_contents(
            observability_dir / "grafana" / "provisioning",
            config.config_dir / "grafana" / "provisioning",
        )
    if "loki" in selected:
        _stage_directory_contents(observability_dir / "loki", config.config_dir / "loki")
    if "alloy" in selected:
        _stage_directory_contents(observability_dir / "alloy", config.config_dir / "alloy")
    if "alertmanager" in selected:
        _stage_single_file_config(
            observability_dir / "alertmanager.yml",
            config.config_dir / "alertmanager",
            "alertmanager.yml",
        )

    callback(
        _make_event(
            step_id,
            ProgressKind.LOG,
            f"runtime files ready: data={data_dir} config={config.config_dir}",
        )
    )


def _stage_runtime_payload(
    config: InstallConfig,
    env_text: str,
    version_env_text: str,
    callback: ProgressCallback,
    step_id: str,
) -> None:
    config.install_dir.mkdir(parents=True, exist_ok=True)
    _materialize_runtime_files(config, callback, step_id)
    write_env(config.install_dir / ".env", env_text, mode=0o600)
    write_env(config.install_dir / "version.env", version_env_text, mode=0o644)


def _write_runtime_payload_local(
    config: InstallConfig,
    env_text: str,
    version_env_text: str,
    callback: ProgressCallback,
    step_id: str,
) -> None:
    _stage_runtime_payload(config, env_text, version_env_text, callback, step_id)


def _run_sudo_runtime_command(
    config: InstallConfig,
    cmd: list[str],
    callback: ProgressCallback,
    step_id: str,
) -> None:
    if config.sudo_password is None:
        raise PermissionError("sudo password not provided for root-owned runtime paths")
    rc, _ = _stream_subprocess(
        ["sudo", "-S", "-p", "", "--", *cmd],
        callback,
        step_id,
        stdin_payload=_sudo_stdin_payload(config),
    )
    if rc != 0:
        raise OSError(f"sudo command failed rc={rc}: {cmd[0]}")


def _sudo_runtime_target_args(staged_root: Path, target_root: Path) -> list[str]:
    args: list[str] = ["directory", str(target_root)]
    if not staged_root.exists():
        return args
    for item in staged_root.rglob("*"):
        if item.is_dir():
            kind = "directory"
        elif item.is_file():
            kind = "file"
        else:
            continue
        args.extend([kind, str(target_root / item.relative_to(staged_root))])
    return args


def _assert_sudo_runtime_targets_safe(
    config: InstallConfig,
    staged_install: Path,
    staged_data: Path,
    staged_config: Path,
    callback: ProgressCallback,
    step_id: str,
) -> None:
    args = [
        "directory",
        str(config.install_dir),
        "file",
        str(config.install_dir / ".env"),
        "file",
        str(config.install_dir / "version.env"),
        *_sudo_runtime_target_args(staged_data, config.models_dir.parent),
        *_sudo_runtime_target_args(staged_config, config.config_dir),
    ]
    rc, lines = _stream_subprocess(
        [
            "sudo",
            "-S",
            "-p",
            "",
            "--",
            "sh",
            "-c",
            _RUNTIME_TARGET_GUARD_SCRIPT,
            "agmind-runtime-target-guard",
            *args,
        ],
        callback,
        step_id,
        stdin_payload=_sudo_stdin_payload(config),
    )
    if rc != 0:
        detail = lines[-1] if lines else "unsafe runtime target path"
        raise OSError(detail)


def _write_runtime_payload_sudo(
    config: InstallConfig,
    env_text: str,
    version_env_text: str,
    callback: ProgressCallback,
    step_id: str,
) -> None:
    data_dir = config.models_dir.parent
    with tempfile.TemporaryDirectory(prefix="agmind-runtime-payload-") as tmp:
        stage = Path(tmp)
        staged_install = stage / "install"
        staged_data = stage / "data"
        staged_config = stage / "config"
        staged_config.mkdir(parents=True, exist_ok=True)
        staged = replace(
            config,
            install_dir=staged_install,
            models_dir=staged_data / "models",
            config_dir=staged_config,
        )
        _stage_runtime_payload(staged, env_text, version_env_text, callback, step_id)

        _assert_sudo_runtime_targets_safe(
            config,
            staged_install,
            staged_data,
            staged_config,
            callback,
            step_id,
        )
        _run_sudo_runtime_command(
            config,
            [
                "install",
                "-d",
                "-m",
                "0755",
                str(config.install_dir),
                str(data_dir),
                str(config.config_dir),
            ],
            callback,
            step_id,
        )
        _run_sudo_runtime_command(
            config,
            [
                "install",
                "-m",
                "0600",
                str(staged_install / ".env"),
                str(config.install_dir / ".env"),
            ],
            callback,
            step_id,
        )
        _run_sudo_runtime_command(
            config,
            [
                "install",
                "-m",
                "0644",
                str(staged_install / "version.env"),
                str(config.install_dir / "version.env"),
            ],
            callback,
            step_id,
        )
        if staged_data.exists():
            _run_sudo_runtime_command(
                config,
                ["cp", "-R", "--no-preserve=ownership", f"{staged_data}/.", str(data_dir)],
                callback,
                step_id,
            )
        if staged_config.exists():
            _run_sudo_runtime_command(
                config,
                [
                    "cp",
                    "-R",
                    "--no-preserve=ownership",
                    f"{staged_config}/.",
                    str(config.config_dir),
                ],
                callback,
                step_id,
            )
        secret_file = data_dir / "secrets" / "cf_dns_api_token"
        if (staged_data / "secrets" / "cf_dns_api_token").exists():
            _run_sudo_runtime_command(
                config,
                ["chmod", "0700", str(data_dir / "secrets")],
                callback,
                step_id,
            )
            _run_sudo_runtime_command(
                config,
                ["chmod", "0600", str(secret_file)],
                callback,
                step_id,
            )


def _env_line(key: str, value: str) -> str:
    # docker-compose env-file quoting (NOT shell): bare for simple values,
    # double-quoted+escaped for space/quote/special so writer↔reader round-trip.
    return f"{key}={compose_env_quote(value) if value else ''}"


def _runtime_env(existing_env: dict[str, str]) -> dict[str, str]:
    values: dict[str, str] = {
        "MINIO_ROOT_USER": existing_env.get("MINIO_ROOT_USER") or "agmind",
        "N8N_TIMEZONE": existing_env.get("N8N_TIMEZONE") or "UTC",
    }
    for key in _RUNTIME_SECRET_KEYS:
        values[key] = existing_env.get(key) or generate_secret(32)
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
            ["sudo", "-S", "-p", "", "--", "cat", str(env_path)],
            capture_output=True,
            text=True,
            check=False,
            input=f"{config.sudo_password}\n",
        )
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or str(exc)).strip()
            raise PermissionError(f"cannot read existing runtime env via sudo: {detail}") from exc
        return parse_env_text(result.stdout)


def _stream_subprocess(
    cmd: list[str],
    callback: ProgressCallback,
    step_id: str,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    stdin_payload: bytes | None = None,
    extra_emit: Callable[[str], None] | None = None,
) -> tuple[int, list[str]]:
    """Run subprocess, stream stdout+stderr line-by-line via callback.

    Returns (returncode, captured_lines). `extra_emit` callable получает
    каждую строку и может emit'ить дополнительные ProgressEvent (e.g.
    парсить progress %). Все строки также эмитятся как ProgressKind.LOG.
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
                    except Exception:  # noqa: BLE001
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


def _make_event(
    step_id: str,
    kind: ProgressKind,
    text: str,
    pct: int | None = None,
) -> ProgressEvent:
    """Local import to avoid circular: создать ProgressEvent без import outside."""
    from agmind.install.orchestrator import ProgressEvent

    return ProgressEvent(step_id=step_id, kind=kind, text=text, progress_pct=pct)


def _docker_compose_cmd(
    config: InstallConfig,
    args: list[str],
    env_file: Path | None = None,
) -> list[str]:
    env_args = ["--env-file", str(env_file)] if env_file is not None else []
    compose = ["docker", "compose", *env_args, *args]
    if config.sudo_password is None:
        return compose
    return ["sudo", "-S", "-p", "", "--", *compose]


def _write_compose_env_file(path: Path, values: dict[str, str]) -> None:
    content = "".join(_env_line(key, values[key]) + "\n" for key in sorted(values))
    write_env(path, content, mode=0o600)


def _sudo_stdin_payload(config: InstallConfig) -> bytes | None:
    if config.sudo_password is None:
        return None
    return f"{config.sudo_password}\n".encode()


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
        except Exception as exc:  # noqa: BLE001
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


# ---------- Step 2: bootstrap (Ansible, sudo) ----------


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
            rc, _ = _stream_subprocess(
                [
                    resolve_ansible_command("ansible-galaxy"),
                    "collection",
                    "install",
                    "-r",
                    str(requirements),
                    "-p",
                    str(galaxy_dir),
                ],
                callback,
                self.step_id,
                cwd=ansible_dir,
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
                "-i",
                "localhost,",
                "--connection",
                "local",
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
            compose_text = render_to_string(
                services=config.services if config.services else None,
                domain=config.domain,
                traefik_enabled=True,
            )
        except Exception as exc:  # noqa: BLE001
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
            compose_text = render_to_string(
                services=config.services if config.services else None,
                domain=config.domain,
                traefik_enabled=True,
            )
        except Exception as exc:  # noqa: BLE001
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
            cmd = _docker_compose_cmd(
                config,
                ["pull", "--policy", "missing", "--quiet"],
                env_file=compose_env_file,
            )
            # docker compose pull stderr содержит per-layer progress; stream его.
            rc, _ = _stream_subprocess(
                cmd,
                callback,
                self.step_id,
                cwd=tmpdir,
                stdin_payload=_sudo_stdin_payload(config),
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


# ---------- Step 4: model download ----------


class ModelDownloadStep(InstallStep):
    """Download up to 3 GGUF models from HF (LLM + Embed + Rerank).

    Phase M5.1: каждая role (llm/embed/rerank) скачивается отдельным
    call'ом — pair (repo, file) of empty/None → skipped. Order: LLM →
    Embed → Rerank (LLM blocking, embeds tiny, rerank optional).

    Detect logic per file (skip re-download если модель уже скачана):
      1. `{models_dir}/{file}` (default /var/lib/agmind/models/) → reuse
      2. User fallback `~/.local/share/agmind/models/{file}` → move в models_dir
      3. None of above → curl download с resume support

    Минимальный размер чтобы считать "real model" = 100 MiB. Embed/rerank
    модели могут быть < 100 MiB — для них порог снижен до 10 MiB.
    """

    step_id = "model_pull"
    label = "Model download"

    PROGRESS_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%")
    # M4.6: curl --progress-bar also writes speed/ETA, parse it for richer events
    SPEED_RE = re.compile(r"(\d+\.?\d*)\s*([KMG])\s")
    MIN_VALID_SIZE = 100 * 1024 * 1024  # 100 MiB — filter empty placeholders / partial
    MIN_VALID_SIZE_SMALL = 10 * 1024 * 1024  # 10 MiB — для embed/rerank (BGE-M3 = 600 MiB)

    @staticmethod
    def _fallback_dirs(config: InstallConfig) -> list[Path]:
        """Other locations to check for already-downloaded model."""
        from os.path import expanduser

        candidates = [
            Path(expanduser("~/.local/share/agmind/models")),  # XDG user fallback
            # Future: Hugging Face HOME cache directory if user has model there.
        ]
        # Drop duplicates / models_dir itself
        seen = {config.models_dir.resolve()}
        out: list[Path] = []
        for c in candidates:
            r = c.resolve() if c.exists() else c
            if r in seen:
                continue
            seen.add(r)
            out.append(c)
        return out

    def _detect_existing(
        self,
        models_dir: Path,
        file_name: str,
        min_size: int,
        config: InstallConfig,
    ) -> tuple[Path | None, str]:
        """Return (path, status_msg) — где модель уже есть. None если nowhere."""
        from agmind.models import safe_model_target

        target = safe_model_target(models_dir, file_name)
        if target.exists() and target.stat().st_size >= min_size:
            return target, f"already present в {target.parent}"
        for fb in self._fallback_dirs(config):
            cand = safe_model_target(fb, file_name)
            if cand.exists() and cand.stat().st_size >= min_size:
                return cand, f"found in fallback {fb}"
        return None, "not present anywhere"

    def _download_one(
        self,
        role: str,
        repo: str | None,
        file_name: str | None,
        config: InstallConfig,
        callback: ProgressCallback,
    ) -> tuple[bool, str]:
        """Download single (repo, file). Returns (success, message)."""
        if not repo or not file_name:
            return True, f"{role}: no model — skipped"

        from agmind.models import hf_resolve_url, safe_model_target

        min_size = self.MIN_VALID_SIZE if role == "llm" else self.MIN_VALID_SIZE_SMALL
        try:
            target = safe_model_target(config.models_dir, file_name)
            url = hf_resolve_url(repo, file_name)
        except ValueError as exc:
            return False, f"{role}: {exc}"
        config.models_dir.mkdir(parents=True, exist_ok=True)

        existing, status = self._detect_existing(config.models_dir, file_name, min_size, config)
        if existing is not None:
            size_mb = existing.stat().st_size // (1024 * 1024)
            if existing == target:
                callback(
                    _make_event(
                        self.step_id,
                        ProgressKind.LOG,
                        f"{role}: skip download {existing} ({size_mb} MiB) — {status}",
                    )
                )
                return True, f"{role}: reused {size_mb} MiB"
            callback(
                _make_event(
                    self.step_id,
                    ProgressKind.LOG,
                    f"{role}: moving {existing} → {target} (saves re-download {size_mb} MiB)",
                )
            )
            import shutil

            try:
                shutil.move(str(existing), str(target))
            except OSError as exc:
                try:
                    _copy_file_atomic(existing, target)
                    existing.unlink()
                except OSError as exc2:
                    return False, f"{role}: cannot relocate model: {exc2} (initial: {exc})"
            return True, f"{role}: relocated {size_mb} MiB"

        partial = target.with_name(f".{target.name}.part")
        if target.is_file() and target.stat().st_size < min_size:
            try:
                if partial.exists():
                    target.unlink()
                else:
                    target.replace(partial)
            except OSError as exc:
                return False, f"{role}: cannot stage partial model download: {exc}"
        cmd = [
            "curl",
            "-fL",
            "-C",
            "-",
            "-o",
            str(partial),
            "--progress-bar",
            "--retry",
            "3",
            url,
        ]
        last_pct = [-1]

        def parse_curl_pct(line: str) -> None:
            m = self.PROGRESS_RE.search(line)
            if not m:
                return
            try:
                pct = int(float(m.group(1)))
            except (ValueError, IndexError):
                return
            if pct == last_pct[0]:
                return
            last_pct[0] = pct
            speed_m = self.SPEED_RE.search(line)
            speed_label = ""
            if speed_m:
                speed_label = f" @ {speed_m.group(1)}{speed_m.group(2)}/s"
            try:
                callback(
                    _make_event(
                        self.step_id,
                        ProgressKind.PROGRESS,
                        f"{role} download {pct}%{speed_label}",
                        pct=pct,
                    )
                )
            except (ValueError, IndexError):
                pass

        rc, _ = _stream_subprocess(cmd, callback, self.step_id, extra_emit=parse_curl_pct)
        if rc != 0:
            return False, f"{role}: curl rc={rc} (download failed)"
        partial_size = partial.stat().st_size if partial.exists() else 0
        if partial_size < min_size:
            size_mb = partial_size // (1024 * 1024)
            min_mb = min_size // (1024 * 1024)
            return False, f"{role}: downloaded file too small ({size_mb} MiB < {min_mb} MiB)"
        partial.replace(target)
        size_mb = target.stat().st_size // (1024 * 1024)
        return True, f"{role}: downloaded {size_mb} MiB → {target.name}"

    def run(self, callback: ProgressCallback, config: InstallConfig) -> InstallStepResult:
        start = time.monotonic()

        roles = (
            ("llm", config.model_repo, config.model_file),
            ("embed", config.embed_repo, config.embed_file),
            ("rerank", config.rerank_repo, config.rerank_file),
        )

        messages: list[str] = []
        for role, repo, file_name in roles:
            ok, msg = self._download_one(role, repo, file_name, config, callback)
            messages.append(msg)
            if not ok:
                return InstallStepResult(
                    step_id=self.step_id,
                    success=False,
                    message=msg,
                    elapsed=timedelta(seconds=time.monotonic() - start),
                )

        return InstallStepResult(
            step_id=self.step_id,
            success=True,
            message="; ".join(messages),
            elapsed=timedelta(seconds=time.monotonic() - start),
        )


# ---------- Step 5: compose deploy ----------


class DeployStep(InstallStep):
    """Run `agmind deploy --apply` (reuse Phase L.B runner)."""

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

        try:
            result = _deploy(
                profiles=[],  # render по services list, см. config
                install_dir=config.install_dir,
                domain=config.domain,
                apply=True,
                no_prompt=True,
                progress=deploy_progress,
                services=config.services,
                sudo_password=config.sudo_password,
            )
        except Exception as exc:  # noqa: BLE001
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
            "",
            "# ---- Rerank (cross-encoder ordering) ----",
            _env_line("AGMIND_RERANK_FILE", config.rerank_file or ""),
            _env_line("AGMIND_RERANK_CTX_SIZE", str(config.rerank_ctx_size)),
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
        ]
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


def default_steps() -> list[InstallStep]:
    """Stock install pipeline. Order matters."""
    return [
        DoctorStep(),
        BootstrapStep(),
        EnvWriteStep(),  # before ImagePullStep — compose parses ${VAR:?} guards
        ComposeConfigStep(),  # fail fast before real image pulls
        ImagePullStep(),
        ModelDownloadStep(),
        DeployStep(),
    ]


__all__ = [
    "BootstrapStep",
    "ComposeConfigStep",
    "DeployStep",
    "DoctorStep",
    "EnvWriteStep",
    "ImagePullStep",
    "ModelDownloadStep",
    "default_steps",
]
