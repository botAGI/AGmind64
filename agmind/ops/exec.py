"""Phase L.E: docker compose proxy helpers - logs / shell.

These commands proxy to `docker compose` and handle common edge cases:
missing compose file, unknown service, missing docker binary, and optional sudo.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from agmind.core.logging import logger
from agmind.core.proc import sudo_argv, sudo_stdin_text

log = logger(__name__)


def _compose_cmd(cmd: list[str], sudo_password: str | None = None) -> list[str]:
    if sudo_password is None:
        return cmd
    return sudo_argv(cmd)


def _sudo_stdin(sudo_password: str | None) -> str | None:
    if sudo_password is None:
        return None
    return sudo_stdin_text(sudo_password)


def _check_prereqs(install_dir: Path) -> str | None:
    """Returns error message or None if all prerequisites are present."""
    if shutil.which("docker") is None:
        return "docker binary not found in PATH"
    compose_file = install_dir / "docker-compose.yml"
    try:
        compose_exists = compose_file.exists()
    except OSError as exc:
        return f"cannot access deployment at {compose_file}: {exc}"
    if not compose_exists:
        return f"no deployment at {compose_file} (run `agmind deploy --apply`)"
    return None


def known_services(install_dir: Path) -> list[str]:
    """Parse compose file for service names. Returns empty list if YAML is invalid."""
    compose_file = install_dir / "docker-compose.yml"
    try:
        compose_exists = compose_file.exists()
    except OSError as exc:
        log.warning("compose access failed: %s", exc)
        return []
    if not compose_exists:
        return []
    try:
        import yaml

        data = yaml.safe_load(compose_file.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("services"), dict):
            return sorted(data["services"].keys())
    except (yaml.YAMLError, OSError) as exc:
        log.warning("compose parse failed: %s", exc)
    return []


def logs(
    install_dir: Path,
    service: str | None = None,
    tail: int = 200,
    follow: bool = False,
    sudo_password: str | None = None,
) -> int:
    """Stream container logs. Returns subprocess exit code.

    If service=None, streams all services. Otherwise streams one selected service.
    """
    err = _check_prereqs(install_dir)
    if err is not None:
        print(f"agmind logs: {err}")
        return 2

    if service is not None and service not in known_services(install_dir):
        known = ", ".join(known_services(install_dir)) or "<none>"
        print(f"agmind logs: unknown service '{service}'. Known: {known}")
        return 2

    cmd = ["docker", "compose", "logs", f"--tail={tail}"]
    if follow:
        cmd.append("--follow")
    if service is not None:
        cmd.append(service)

    run_cmd = _compose_cmd(cmd, sudo_password)
    log.info("running: %s (cwd=%s)", " ".join(run_cmd), install_dir)
    try:
        result = subprocess.run(
            run_cmd,
            cwd=install_dir,
            check=False,
            input=_sudo_stdin(sudo_password),
        )
        return result.returncode
    except OSError as exc:
        print(f"agmind logs: docker compose failed: {exc}")
        return 1
    except KeyboardInterrupt:
        return 130


def shell(
    install_dir: Path,
    service: str,
    cmd: list[str] | None = None,
    workdir: str | None = None,
    sudo_password: str | None = None,
) -> int:
    """Run `docker compose exec -it <service> <cmd>`. Returns subprocess exit code."""
    err = _check_prereqs(install_dir)
    if err is not None:
        print(f"agmind shell: {err}")
        return 2

    if service not in known_services(install_dir):
        known = ", ".join(known_services(install_dir)) or "<none>"
        print(f"agmind shell: unknown service '{service}'. Known: {known}")
        return 2

    docker_cmd = ["docker", "compose", "exec"]
    if workdir is not None:
        docker_cmd.extend(["-w", workdir])
    docker_cmd.append(service)
    docker_cmd.extend(cmd or ["/bin/sh"])

    run_cmd = _compose_cmd(docker_cmd, sudo_password)
    log.info("running: %s (cwd=%s)", " ".join(run_cmd), install_dir)
    try:
        result = subprocess.run(
            run_cmd,
            cwd=install_dir,
            check=False,
            input=_sudo_stdin(sudo_password),
        )
        return result.returncode
    except OSError as exc:
        print(f"agmind shell: docker compose failed: {exc}")
        return 1
    except KeyboardInterrupt:
        return 130
