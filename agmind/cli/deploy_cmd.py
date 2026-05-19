"""`agmind deploy` subcommand — orchestrate docker compose stack.

Wrapper над `docker compose` (или `ansible-playbook install.yml -t services`).
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from agmind.log import logger

log = logger(__name__)


def _install_dir() -> Path:
    """Resolve installation dir from env or default."""
    return Path(os.environ.get("AGMIND_INSTALL_DIR", "/opt/agmind"))


def _compose_file() -> Path:
    """Compose file path. Должен быть pre-rendered Ansible'ом."""
    return _install_dir() / "docker-compose.yml"


def _run_compose(*args: str, check: bool = True) -> int:
    """Run `docker compose ...` в install dir."""
    compose = _compose_file()
    if not compose.exists():
        print(
            f"ERROR: {compose} не существует.\n"
            "Сначала запустите: ansible-playbook ansible/install.yml -t services",
            file=sys.stderr,
        )
        return 2

    cmd = ["docker", "compose", "-f", str(compose), *args]
    log.info("$ %s", " ".join(cmd))
    result = subprocess.run(cmd, check=False)
    if check and result.returncode != 0:
        return result.returncode
    return result.returncode


def cmd_up(*, profile: str | None = None, detach: bool = True) -> int:
    """Bring stack up. Optional profile override (compose --profile)."""
    args = ["up"]
    if detach:
        args.append("-d")
    if profile:
        args = ["--profile", profile, *args]
    return _run_compose(*args)


def cmd_down(*, volumes: bool = False) -> int:
    """Stop stack. --volumes also removes named volumes (destructive)."""
    args = ["down"]
    if volumes:
        args.append("--volumes")
    return _run_compose(*args)


def cmd_status() -> int:
    """`docker compose ps` — show running services."""
    return _run_compose("ps")


def cmd_ps(as_json: bool = False) -> int:
    args = ["ps"]
    if as_json:
        args.append("--format=json")
    return _run_compose(*args)


def cmd_logs(service: str | None = None, *, follow: bool = False, lines: int = 100) -> int:
    args = ["logs", f"--tail={lines}"]
    if follow:
        args.append("-f")
    if service:
        args.append(service)
    return _run_compose(*args)


def cmd_restart(service: str | None = None) -> int:
    args = ["restart"]
    if service:
        args.append(service)
    return _run_compose(*args)


def cmd_pull() -> int:
    """Pre-fetch latest images (semver pinned per services.yaml)."""
    return _run_compose("pull")
