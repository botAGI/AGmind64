"""`agmind deploy` subcommand — orchestrate docker compose stack.

Wrapper над `docker compose` (или `ansible-playbook install.yml -t services`).
"""

from __future__ import annotations

import getpass
import os
import subprocess
import sys
from pathlib import Path

import typer

from agmind.core.logging import logger

log = logger(__name__)


def _install_dir() -> Path:
    """Resolve installation dir from env or default."""
    return Path(os.environ.get("AGMIND_INSTALL_DIR", "/opt/agmind"))


def _compose_file() -> Path:
    """Compose file path. Должен быть pre-rendered Ansible'ом."""
    return _install_dir() / "docker-compose.yml"


def _run_compose(*args: str) -> int:
    """Run `docker compose ...` в install dir."""
    install_dir = _install_dir()
    compose = _compose_file()
    if not compose.exists():
        print(
            f"ERROR: {compose} не существует.\n"
            "Сначала запустите: ansible-playbook ansible/install.yml -t services",
            file=sys.stderr,
        )
        return 2

    env_file = install_dir / ".env"
    env_args = ["--env-file", str(env_file)] if env_file.exists() else []
    cmd = ["docker", "compose", *env_args, "-f", str(compose), *args]
    log.info("$ %s", " ".join(cmd))
    try:
        result = subprocess.run(cmd, cwd=install_dir, check=False)
    except OSError as exc:
        print(f"ERROR: docker compose failed: {exc}", file=sys.stderr)
        return 1
    rc = result.returncode
    # A subprocess killed by a signal returns a NEGATIVE code; raising typer.Exit(-9) makes
    # POSIX mask it to 247, corrupting CI retry logic. Normalize signal death to the
    # conventional 128+signal so the exit code stays meaningful and in 0-255.
    return rc if rc >= 0 else 128 + (-rc)


def cmd_up(*, profile: str | None = None, detach: bool = True) -> int:
    """Bring stack up. Optional profile override (compose --profile)."""
    args = ["up"]
    if detach:
        args.append("-d")
    if profile:
        args = ["--profile", profile, *args]
    return _run_compose(*args)


def cmd_down(*, volumes: bool = False, yes: bool = False) -> int:
    """Stop stack. --volumes also removes named volumes (destructive)."""
    args = ["down"]
    if volumes:
        if not yes and not typer.confirm(
            "⚠️  --volumes will PERMANENTLY DELETE every named volume "
            "(postgres/qdrant/milvus/redis/minio data). Continue?",
            default=False,
        ):
            print(
                "aborted: volume deletion not confirmed (pass --yes to skip this prompt)",
                file=sys.stderr,
            )
            return 1
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


def _prompt_sudo_password(ask_sudo_password: bool) -> str | None:
    if not ask_sudo_password:
        return None
    return getpass.getpass("sudo password: ")


def cmd_deploy(
    profiles: list[str],
    install_dir: Path,
    domain: str | None,
    apply: bool,
    no_prompt: bool,
    healthcheck_timeout: int,
    verbose: bool = False,
    ask_sudo_password: bool = False,
    services: list[str] | None = None,
) -> int:
    """Idempotent deploy (Phase L.B): dry-run by default, --apply to commit.

    Под капотом: snapshot → render → diff → docker compose up --remove-orphans
    → healthcheck wait → rollback at failure. См. agmind/deploy/.
    """
    from agmind.deploy import deploy as do_deploy
    from agmind.deploy import format_diff

    result = do_deploy(
        profiles=profiles,
        services=services,
        install_dir=install_dir,
        domain=domain,
        apply=apply,
        no_prompt=no_prompt,
        healthcheck_timeout=healthcheck_timeout,
        sudo_password=_prompt_sudo_password(ask_sudo_password),
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


def cmd_rollback(
    snapshot_id: str | None, install_dir: Path, ask_sudo_password: bool = False
) -> int:
    """Restore deployment from snapshot (Phase L.B)."""
    from agmind.deploy import rollback as do_rollback

    result = do_rollback(
        snapshot_id=snapshot_id,
        install_dir=install_dir,
        sudo_password=_prompt_sudo_password(ask_sudo_password),
    )
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


def register(app: typer.Typer) -> None:
    """Attach the deploy group, rollback, snapshots group and gc to ``app``."""

    # ---- deploy subcommand group (Phase L.B) ----
    deploy_app = typer.Typer(
        name="deploy",
        help="Idempotent deploy с automatic snapshot + healthcheck + rollback",
        no_args_is_help=False,
        invoke_without_command=True,
    )
    app.add_typer(deploy_app)

    @deploy_app.callback(invoke_without_command=True)
    def deploy_group(
        ctx: typer.Context,
        profile: str = typer.Option(
            "core,observability",
            "--profile",
            "-p",
            help="Comma-separated profiles to deploy (ignored when --service is used)",
        ),
        service: list[str] | None = typer.Option(
            None,
            "--service",
            "-s",
            help="Explicit service name; can be repeated",
        ),
        install_dir: Path = typer.Option(
            Path("/opt/agmind"),
            "--install-dir",
            help="Install directory (default: /opt/agmind)",
        ),
        domain: str | None = typer.Option(
            None,
            "--domain",
            envvar="AGMIND_DOMAIN",
            help="Override agmind.dev placeholder",
        ),
        apply: bool = typer.Option(
            False,
            "--apply",
            help="Actually apply changes (default: dry-run / diff only)",
        ),
        no_prompt: bool = typer.Option(
            False,
            "--no-prompt",
            "--yes",
            "-y",
            help="Skip interactive confirmation before destructive --apply (CI mode)",
        ),
        healthcheck_timeout: int = typer.Option(
            300,
            "--healthcheck-timeout",
            help="Seconds to wait for healthy state",
        ),
        ask_sudo_password: bool = typer.Option(
            False,
            "--ask-sudo-password",
            help="Prompt for sudo password for root-owned install/snapshot paths",
        ),
        verbose: bool = typer.Option(
            False,
            "--verbose",
            "-v",
            help="Show full unified diff",
        ),
    ) -> None:
        """Deploy (default: dry-run). Use --apply to commit changes."""
        if ctx.invoked_subcommand is not None:
            return

        profiles = [p.strip() for p in profile.split(",") if p.strip()]
        rc = cmd_deploy(
            profiles=profiles,
            services=service,
            install_dir=install_dir,
            domain=domain,
            apply=apply,
            no_prompt=no_prompt,
            healthcheck_timeout=healthcheck_timeout,
            verbose=verbose,
            ask_sudo_password=ask_sudo_password,
        )
        raise typer.Exit(code=rc)

    @deploy_app.command("up")
    def deploy_up(
        profile: str | None = typer.Option(None, "--profile", "-p"),
        detach: bool = typer.Option(True, "--detach/--no-detach"),
    ) -> None:
        """Backward-compatible docker compose up wrapper."""
        raise typer.Exit(code=cmd_up(profile=profile, detach=detach))

    @deploy_app.command("down")
    def deploy_down(
        volumes: bool = typer.Option(
            False, "--volumes", help="Also remove named volumes (DESTRUCTIVE)."
        ),
        yes: bool = typer.Option(
            False, "--yes", "-y", help="Skip the destructive-volume confirmation prompt."
        ),
    ) -> None:
        """Backward-compatible docker compose down wrapper."""
        raise typer.Exit(code=cmd_down(volumes=volumes, yes=yes))

    @deploy_app.command("status")
    def deploy_status() -> None:
        """Backward-compatible docker compose ps wrapper."""
        raise typer.Exit(code=cmd_status())

    @deploy_app.command("ps")
    def deploy_ps(
        as_json: bool = typer.Option(False, "--json", help="JSON output"),
    ) -> None:
        """Backward-compatible docker compose ps wrapper."""
        raise typer.Exit(code=cmd_ps(as_json=as_json))

    @deploy_app.command("logs")
    def deploy_logs(
        service: str | None = typer.Argument(None, help="Service name."),
        follow: bool = typer.Option(False, "-f", "--follow", help="Stream new logs."),
        ask_sudo_password: bool = typer.Option(
            False,
            "--ask-sudo-password",
            help="Prompt for sudo password for root-owned install paths",
        ),
        lines: int = typer.Option(100, "--lines", help="Initial backlog lines."),
    ) -> None:
        """Backward-compatible docker compose logs wrapper."""
        raise typer.Exit(code=cmd_logs(service=service, follow=follow, lines=lines))

    @deploy_app.command("restart")
    def deploy_restart(
        service: str | None = typer.Argument(None, help="Service name."),
    ) -> None:
        """Backward-compatible docker compose restart wrapper."""
        raise typer.Exit(code=cmd_restart(service=service))

    @deploy_app.command("pull")
    def deploy_pull() -> None:
        """Backward-compatible docker compose pull wrapper."""
        raise typer.Exit(code=cmd_pull())

    # ---- rollback (top-level command) ----
    @app.command()
    def rollback(
        snapshot_id: str | None = typer.Argument(
            None,
            help="Snapshot ID (omit for latest)",
        ),
        ask_sudo_password: bool = typer.Option(
            False,
            "--ask-sudo-password",
            help="Prompt for sudo password for root-owned install/snapshot paths",
        ),
        install_dir: Path = typer.Option(
            Path("/opt/agmind"),
            "--install-dir",
            help="Install directory",
        ),
    ) -> None:
        """Restore deployment from snapshot."""
        raise typer.Exit(
            code=cmd_rollback(snapshot_id, install_dir, ask_sudo_password=ask_sudo_password)
        )

    # ---- snapshots list (top-level) ----
    snapshots_app = typer.Typer(
        name="snapshots",
        help="Manage deployment snapshots",
        no_args_is_help=True,
    )
    app.add_typer(snapshots_app)

    @snapshots_app.command("list")
    def snapshots_list() -> None:
        """List available snapshots (newest first)."""
        raise typer.Exit(code=cmd_snapshots_list())

    # ---- gc (Phase L.C) ----
    @app.command()
    def gc(
        dry_run: bool = typer.Option(
            False, "--dry-run", help="Show what would be removed (don't delete)"
        ),
        aggressive: bool = typer.Option(
            False,
            "--aggressive",
            help="Remove ALL unused volumes (default: только labeled agmind.gc=auto)",
        ),
        older_than_hours: int = typer.Option(
            72,
            "--older-than-hours",
            help="Image age cutoff в часах (default: 72)",
        ),
        include_models: bool = typer.Option(
            False,
            "--include-models",
            help="Также удалить GGUF/safetensors не упомянутые в descriptors",
        ),
        yes: bool = typer.Option(
            False,
            "--yes",
            "-y",
            help="Skip confirmation for destructive --aggressive/--include-models.",
        ),
    ) -> None:
        """Garbage collection: containers + images + volumes + networks (+ models opt-in)."""
        from agmind.deploy import format_gc_report, gc_all

        # --aggressive (all unused volumes) and --include-models (GGUF/safetensors deletion)
        # are irreversible; require an explicit confirmation unless --dry-run or --yes.
        if (aggressive or include_models) and not dry_run and not yes:
            if not typer.confirm(
                "⚠️  Destructive GC: --aggressive removes ALL unused volumes and/or "
                "--include-models deletes unreferenced model weights. Continue?",
                default=False,
            ):
                print(
                    "aborted: destructive GC not confirmed (use --dry-run to preview or --yes)",
                    file=sys.stderr,
                )
                raise typer.Exit(code=1)

        reports = gc_all(
            aggressive=aggressive,
            older_than_hours=older_than_hours,
            dry_run=dry_run,
            include_models=include_models,
        )
        sys.stdout.write(format_gc_report(reports))
