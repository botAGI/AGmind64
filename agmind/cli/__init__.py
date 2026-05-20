"""AGmind CLI — entry point typer app.

Команды:
    agmind doctor      — preflight diagnostics
    agmind status      — backend / engine info
    agmind version     — pkg version + git rev
    agmind audit       — wrapper над scripts/audit_forbidden.py

Установка typer/click — soft dependency. Если typer не установлен,
`app()` падает с понятной инструкцией.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from agmind import __version__
from agmind.log import setup as setup_logging

# Lazy import typer чтобы import agmind.cli не валился без typer.
try:
    import typer
    _HAS_TYPER = True
except ImportError:
    _HAS_TYPER = False


def _make_app() -> "typer.Typer":  # type: ignore[name-defined]
    """Build typer app. Calls typer at import-time only if available."""
    app = typer.Typer(
        name="agmind",
        help="Private LLM/RAG platform for AMD Strix Halo / x86_64.",
        no_args_is_help=True,
        add_completion=False,
    )

    @app.callback()
    def _global_options(
        verbose: bool = typer.Option(False, "--verbose", "-v", help="Debug logging"),
    ) -> None:
        setup_logging("DEBUG" if verbose else "INFO")

    @app.command()
    def doctor(
        as_json: bool = typer.Option(False, "--json", help="JSON output"),
    ) -> None:
        """Run preflight diagnostics."""
        from agmind.diagnostics import doctor_report

        out = doctor_report(as_json=as_json)
        typer.echo(out)
        from agmind.diagnostics.doctor import run_preflight
        report = run_preflight()
        if report.has_failures:
            raise typer.Exit(code=2)
        if report.has_warnings:
            raise typer.Exit(code=1)

    @app.command()
    def status(
        as_json: bool = typer.Option(False, "--json", help="JSON output"),
        tui: bool = typer.Option(
            False, "--tui", help="Launch live deployment dashboard (Phase J.2)"
        ),
        install_dir: Path = typer.Option(
            Path("/opt/agmind"),
            "--install-dir",
            help="Deployment dir (only for --tui)",
        ),
        refresh: float = typer.Option(
            5.0, "--refresh", help="Refresh interval seconds (only for --tui)"
        ),
    ) -> None:
        """Show selected backend + device info, или live dashboard с --tui."""
        if tui:
            from agmind.cli.tui.status_dashboard import run_dashboard

            run_dashboard(install_dir=install_dir, refresh_interval=refresh)
            return

        from agmind.compute import get_backend, list_available_backends

        backend = get_backend()
        info = backend.device_info()
        payload = {
            "available_backends": list_available_backends(),
            "selected": {
                "backend": info.backend,
                "engine": info.engine,
                "device_id": info.device_id,
                "name": info.name,
                "total_memory_gib": info.total_memory_bytes / 1024**3,
                "capabilities": info.capabilities,
            },
        }
        if as_json:
            typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))
        else:
            typer.echo(f"Available: {payload['available_backends']}")
            typer.echo(f"Selected:  {info.backend} / {info.engine}")
            typer.echo(f"Device:    {info.name}")
            typer.echo(f"Memory:    {info.total_memory_bytes / 1024**3:.1f} GiB")
            typer.echo("Capabilities:")
            for k, v in info.capabilities.items():
                typer.echo(f"  {k}: {v}")

    @app.command()
    def version() -> None:
        """Print agmind version."""
        typer.echo(f"agmind {__version__}")

    @app.command()
    def audit() -> None:
        """Run audit_forbidden.py (forbid legacy patterns in main tree)."""  # audit: allow rule-self-reference
        import subprocess

        result = subprocess.run(
            ["python3", "scripts/audit_forbidden.py", "--fail"],
            check=False,
        )
        raise typer.Exit(code=result.returncode)

    # ---- deploy subcommand group (Phase L.B) ----
    deploy_app = typer.Typer(
        name="deploy",
        help="Idempotent deploy с automatic snapshot + healthcheck + rollback",
        no_args_is_help=False,
        invoke_without_command=True,
    )
    app.add_typer(deploy_app)

    @deploy_app.callback(invoke_without_command=True)
    def deploy_cmd(
        ctx: typer.Context,
        profile: str = typer.Option(
            "core,observability",
            "--profile",
            "-p",
            help="Comma-separated profiles to deploy",
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
            help="Skip interactive confirmation (CI mode)",
        ),
        healthcheck_timeout: int = typer.Option(
            300,
            "--healthcheck-timeout",
            help="Seconds to wait for healthy state",
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
        from agmind.cli.deploy_cmd import cmd_deploy

        profiles = [p.strip() for p in profile.split(",") if p.strip()]
        rc = cmd_deploy(
            profiles=profiles,
            install_dir=install_dir,
            domain=domain,
            apply=apply,
            no_prompt=no_prompt,
            healthcheck_timeout=healthcheck_timeout,
            verbose=verbose,
        )
        raise typer.Exit(code=rc)

    # ---- rollback (top-level command) ----
    @app.command()
    def rollback(
        snapshot_id: str | None = typer.Argument(
            None,
            help="Snapshot ID (omit for latest)",
        ),
        install_dir: Path = typer.Option(
            Path("/opt/agmind"),
            "--install-dir",
            help="Install directory",
        ),
    ) -> None:
        """Restore deployment from snapshot."""
        from agmind.cli.deploy_cmd import cmd_rollback

        raise typer.Exit(code=cmd_rollback(snapshot_id, install_dir))

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
        from agmind.cli.deploy_cmd import cmd_snapshots_list

        raise typer.Exit(code=cmd_snapshots_list())

    # ---- gc (Phase L.C) ----
    @app.command()
    def gc(
        dry_run: bool = typer.Option(
            False, "--dry-run", help="Show what would be removed (don't delete)"
        ),
        aggressive: bool = typer.Option(
            False, "--aggressive",
            help="Remove ALL unused volumes (default: только labeled agmind.gc=auto)",
        ),
        older_than_hours: int = typer.Option(
            72, "--older-than-hours",
            help="Image age cutoff в часах (default: 72)",
        ),
        include_models: bool = typer.Option(
            False, "--include-models",
            help="Также удалить GGUF/safetensors не упомянутые в descriptors",
        ),
    ) -> None:
        """Garbage collection: containers + images + volumes + networks (+ models opt-in)."""
        from agmind.deploy import format_gc_report, gc_all

        reports = gc_all(
            aggressive=aggressive,
            older_than_hours=older_than_hours,
            dry_run=dry_run,
            include_models=include_models,
        )
        sys.stdout.write(format_gc_report(reports))

    # ---- setup TUI wizard (Phase J) ----
    @app.command()
    def setup(
        from_state: Path | None = typer.Option(
            None,
            "--from-state",
            help="Load saved state from JSON (non-interactive mode)",
        ),
        deploy: bool = typer.Option(
            False,
            "--deploy",
            help="Сразу запустить `agmind deploy --apply` после wizard exit",
        ),
    ) -> None:
        """Interactive setup wizard (TUI). Wizard собирает config + token.

        После Apply config сохраняется в ~/.local/share/agmind/. Реальный
        deploy запускается отдельной командой (показывается в финальном box).
        Используй `--deploy` чтобы сразу применить после wizard.
        """
        from agmind.cli.tui import run_setup_wizard
        from agmind.cli.tui.setup_wizard import STATE_PATH, SetupState

        initial: SetupState | None = None
        if from_state is not None and from_state.exists():
            try:
                initial = SetupState.from_json(from_state)
            except Exception as exc:  # noqa: BLE001
                typer.echo(f"WARNING: failed to load state from {from_state}: {exc}")

        # auto_deploy=True если флаг --deploy: Apply внутри TUI запускает
        # DeployProgressScreen с live прогрессом (вместо shell post-exit deploy).
        result = run_setup_wizard(initial_state=initial, auto_deploy=deploy)
        if result is None:
            typer.echo("Setup cancelled.")
            raise typer.Exit(code=1)

        # Phase J.1.6: всё показывается внутри TUI (SummaryScreen). Здесь —
        # минимальный shell echo + exit code на основе deploy_result если был.
        # User уже видел full summary в TUI.
        deploy_result = getattr(result, "_deploy_result", None)
        if deploy_result is not None:
            # Auto-deploy mode — exit code reflects result
            typer.echo(
                f"\n{'✓' if deploy_result.success else '✗'} {deploy_result.message}"
            )
            raise typer.Exit(code=0 if deploy_result.success else 1)
        # Wizard-only mode — user уже видел next-steps в TUI SummaryScreen
        typer.echo(
            f"\n✓ Config saved to {STATE_PATH}. "
            f"Use `agmind setup --deploy` to apply, или см. инструкции в TUI summary."
        )

    # ---- service subcommand group (Phase H'.E) ----
    service_app = typer.Typer(
        name="service",
        help="Manage service descriptors (templates/services/*.yaml)",
        no_args_is_help=True,
    )
    app.add_typer(service_app)

    @service_app.command("list")
    def service_list() -> None:
        """List all service descriptors with tier и profiles."""
        from agmind.cli.service_cmd import cmd_list

        raise typer.Exit(code=cmd_list())

    @service_app.command("status")
    def service_status(
        name: str | None = typer.Argument(None, help="Service name (omit for summary)"),
    ) -> None:
        """Show tier breakdown или детали одного сервиса."""
        from agmind.cli.service_cmd import cmd_status

        raise typer.Exit(code=cmd_status(name))

    @service_app.command("validate")
    def service_validate(
        name: str | None = typer.Argument(None, help="Service name (omit to validate all)"),
    ) -> None:
        """Validate descriptors against Pydantic schema."""
        from agmind.cli.service_cmd import cmd_validate

        raise typer.Exit(code=cmd_validate(name))

    @service_app.command("scaffold")
    def service_scaffold(
        name: str = typer.Argument(..., help="New service name (a-z0-9-)"),
        tier: str = typer.Option(..., "--tier", "-t", help="edge|inference|app|storage|ops"),
        force: bool = typer.Option(False, "--force", help="Overwrite existing file"),
    ) -> None:
        """Scaffold new templates/services/<name>.yaml из шаблона."""
        from agmind.cli.service_cmd import cmd_scaffold

        raise typer.Exit(code=cmd_scaffold(name, tier, force=force))  # type: ignore[arg-type]

    # ---- render subcommand group (Phase H'.C) ----
    render_app = typer.Typer(
        name="render",
        help="Render compose / configs from templates/services/*.yaml descriptors",
        no_args_is_help=True,
    )
    app.add_typer(render_app)

    @render_app.command("compose")
    def render_compose(
        profile: str = typer.Option(
            "core",
            "--profile",
            "-p",
            help="Comma-separated profile names (core,rag,full,...)",
        ),
        output: Path | None = typer.Option(
            None, "--output", "-o", help="Output file (default: stdout)"
        ),
        no_traefik: bool = typer.Option(
            False, "--no-traefik", help="Skip Traefik labels generation"
        ),
        diff: Path | None = typer.Option(
            None, "--diff", help="Diff against existing compose file (no write)"
        ),
        domain: str | None = typer.Option(
            None,
            "--domain",
            envvar="AGMIND_DOMAIN",
            help="Override agmind.dev placeholder (e.g. yourdomain.com)",
        ),
    ) -> None:
        """Render docker-compose.yml from ServiceDescriptor catalog."""
        from agmind.cli.render_cmd import cmd_render_compose

        profiles = [p.strip() for p in profile.split(",") if p.strip()]
        rc = cmd_render_compose(
            profiles=profiles,
            output=output,
            traefik=not no_traefik,
            diff=diff,
            domain=domain,
        )
        raise typer.Exit(code=rc)

    # ---- ops subcommands: logs / shell / backup / restore (Phase L.E) ----

    @app.command()
    def logs(
        service: str | None = typer.Argument(
            None, help="Service name (omit для всех сервисов)."
        ),
        install_dir: Path = typer.Option(
            Path("/opt/agmind"), "--install-dir", help="Deployment dir."
        ),
        tail: int = typer.Option(200, "--tail", help="Initial backlog lines."),
        follow: bool = typer.Option(False, "-f", "--follow", help="Stream new logs."),
    ) -> None:
        """Stream docker compose logs."""
        from agmind.cli.ops_cmd import cmd_logs

        raise typer.Exit(code=cmd_logs(service, install_dir, tail, follow))

    @app.command()
    def shell(
        service: str = typer.Argument(..., help="Service name."),
        install_dir: Path = typer.Option(
            Path("/opt/agmind"), "--install-dir", help="Deployment dir."
        ),
        cmd: str = typer.Option(
            "/bin/sh", "--cmd", help="Command to run (default /bin/sh)."
        ),
        workdir: str | None = typer.Option(
            None, "--workdir", "-w", help="Working dir внутри container."
        ),
    ) -> None:
        """Open shell inside running service container (docker compose exec)."""
        from agmind.cli.ops_cmd import cmd_shell

        # shlex.split чтобы поддержать `--cmd "python -m foo"`
        import shlex

        cmd_list = shlex.split(cmd) if cmd else None
        raise typer.Exit(code=cmd_shell(service, install_dir, cmd_list, workdir))

    @app.command()
    def backup(
        output: Path = typer.Option(
            ...,
            "--output",
            "-o",
            help="Path для .tar.gz (e.g. agmind-2026-05-20.tar.gz).",
        ),
    ) -> None:
        """Create tar.gz backup of compose / .env / state / snapshots (Phase L.E)."""
        from agmind.cli.ops_cmd import cmd_backup

        raise typer.Exit(code=cmd_backup(output))

    @app.command()
    def restore(
        backup_file: Path = typer.Argument(..., help="Path to .tar.gz backup."),
        yes: bool = typer.Option(
            False, "-y", "--yes", help="Skip interactive confirmation."
        ),
    ) -> None:
        """Restore deployment from `agmind backup` archive (Phase L.E)."""
        from agmind.cli.ops_cmd import cmd_restore

        raise typer.Exit(code=cmd_restore(backup_file, yes=yes))

    # ---- migrate subcommand group (Phase L.D) ----
    migrate_app = typer.Typer(
        name="migrate",
        help="Manage AGmind state schema migrations (Phase L.D).",
        no_args_is_help=True,
    )
    app.add_typer(migrate_app)

    @migrate_app.command("status")
    def migrate_status(
        as_json: bool = typer.Option(False, "--json", help="JSON output"),
    ) -> None:
        """Show current schema version + applied/pending migrations."""
        from agmind.cli.migrate_cmd import cmd_status

        raise typer.Exit(code=cmd_status(as_json=as_json))

    @migrate_app.command("list")
    def migrate_list(
        as_json: bool = typer.Option(False, "--json", help="JSON output"),
    ) -> None:
        """List all known migrations (registered in agmind.migrations.versions)."""
        from agmind.cli.migrate_cmd import cmd_list

        raise typer.Exit(code=cmd_list(as_json=as_json))

    @migrate_app.command("up")
    def migrate_up(
        target: int | None = typer.Option(
            None, "--target", help="Apply migrations up to this version (inclusive)."
        ),
    ) -> None:
        """Apply pending migrations."""
        from agmind.cli.migrate_cmd import cmd_up

        raise typer.Exit(code=cmd_up(target=target))

    @migrate_app.command("down")
    def migrate_down(
        steps: int = typer.Option(1, "--steps", help="How many migrations to roll back."),
        target: int | None = typer.Option(
            None, "--target", help="Roll back everything above this version."
        ),
    ) -> None:
        """Roll back applied migrations."""
        from agmind.cli.migrate_cmd import cmd_down

        raise typer.Exit(code=cmd_down(steps=steps, target=target))

    return app


def app() -> None:
    """Entry point: `python -m agmind` → cli.app()."""
    if not _HAS_TYPER:
        sys.stderr.write(
            "typer is not installed. Install with: pip install 'agmind[dev]'\n"
            "Or directly: pip install typer rich\n"
        )
        sys.exit(2)
    _make_app()()
