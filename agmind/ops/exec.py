"""Phase L.E: docker compose proxy helpers — logs / shell.

Эти команды просто запускают `docker compose` под капотом, обрабатывая
edge cases (нет compose файла, неизвестный сервис, нет docker binary).
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from agmind.log import logger

log = logger(__name__)


def _check_prereqs(install_dir: Path) -> str | None:
    """Returns error message or None if все ок."""
    if shutil.which("docker") is None:
        return "docker binary not found in PATH"
    compose_file = install_dir / "docker-compose.yml"
    if not compose_file.exists():
        return f"no deployment at {compose_file} (run `agmind deploy --apply`)"
    return None


def known_services(install_dir: Path) -> list[str]:
    """Parse compose file для list сервисов. Returns empty list если YAML невалиден."""
    compose_file = install_dir / "docker-compose.yml"
    if not compose_file.exists():
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
) -> int:
    """Stream container logs. Returns subprocess exit code.

    Если service=None — логи всех сервисов. Иначе только указанного.
    `follow=True` блокирует процесс до Ctrl-C; tail управляет initial backlog.
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

    log.info("running: %s (cwd=%s)", " ".join(cmd), install_dir)
    try:
        result = subprocess.run(cmd, cwd=install_dir, check=False)
        return result.returncode
    except KeyboardInterrupt:
        return 130


def shell(
    install_dir: Path,
    service: str,
    cmd: list[str] | None = None,
    workdir: str | None = None,
) -> int:
    """`docker compose exec -it <service> <cmd>`. Returns subprocess exit code.

    Default cmd = ["/bin/sh"]. Контейнер должен быть running, иначе compose exec
    вернёт rc != 0.
    """
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

    log.info("running: %s (cwd=%s)", " ".join(docker_cmd), install_dir)
    try:
        result = subprocess.run(docker_cmd, cwd=install_dir, check=False)
        return result.returncode
    except KeyboardInterrupt:
        return 130
