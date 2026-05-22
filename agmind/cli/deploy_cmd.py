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


# ---- Phase L.B: idempotent deploy + snapshot/rollback ----


def cmd_deploy(
    profiles: list[str],
    install_dir: Path,
    domain: str | None,
    apply: bool,
    no_prompt: bool,
    healthcheck_timeout: int,
    verbose: bool = False,
) -> int:
    """Idempotent deploy (Phase L.B): dry-run by default, --apply to commit.

    Под капотом: snapshot → render → diff → docker compose up --remove-orphans
    → healthcheck wait → rollback at failure. См. agmind/deploy/.
    """
    from agmind.deploy import deploy as do_deploy
    from agmind.deploy import format_diff

    result = do_deploy(
        profiles=profiles,
        install_dir=install_dir,
        domain=domain,
        apply=apply,
        no_prompt=no_prompt,
        healthcheck_timeout=healthcheck_timeout,
    )

    if result.diff is not None:
        sys.stdout.write(format_diff(result.diff, verbose=verbose))

    if result.snapshot is not None:
        sys.stdout.write(f"📸 snapshot: {result.snapshot.id} ({result.snapshot.path})\n")

    icon = "✓" if result.success else "✗"
    sys.stdout.write(f"\n{icon} {result.message}\n")

    if result.rollback_performed:
        sys.stdout.write("↩️  rolled back to snapshot\n")

    return 0 if result.success else 1


def cmd_rollback(snapshot_id: str | None, install_dir: Path) -> int:
    """Restore deployment from snapshot (Phase L.B)."""
    from agmind.deploy import rollback as do_rollback

    result = do_rollback(snapshot_id=snapshot_id, install_dir=install_dir)
    icon = "✓" if result.success else "✗"
    sys.stdout.write(f"{icon} {result.message}\n")
    return 0 if result.success else 1


def cmd_snapshots_list() -> int:
    """List all available deployment snapshots (Phase L.B)."""
    from agmind.deploy import SnapshotManager

    snaps = SnapshotManager().list()
    if not snaps:
        print("No snapshots found.")
        return 0

    print(f"{'ID':<22} {'PROFILE':<25} REASON")
    print("-" * 80)
    for s in snaps:
        print(f"{s.id:<22} {s.profile:<25} {s.reason}")
    return 0
