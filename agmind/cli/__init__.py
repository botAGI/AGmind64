"""AGmind CLI — entry point typer app.

Команды:
    agmind doctor      — preflight diagnostics
    agmind status      — backend / engine info
    agmind version     — pkg version + git rev
    agmind audit       — wrapper над scripts/checks/audit_forbidden.py

Установка typer/click — soft dependency. Если typer не установлен,
`app()` падает с понятной инструкцией.
"""

from __future__ import annotations

import json
import stat
import sys
from pathlib import Path

from agmind import __version__
from agmind.core.domain import validate_domain
from agmind.core.logging import setup as setup_logging

# Lazy import typer чтобы import agmind.cli не валился без typer.
try:
    import typer

    _HAS_TYPER = True
except ImportError:
    _HAS_TYPER = False


def _make_app() -> typer.Typer:
    """Build typer app. Calls typer at import-time only if available."""
    app = typer.Typer(
        name="agmind",
        help="Private LLM/RAG platform for AMD Strix Halo / x86_64.",
        no_args_is_help=True,
        add_completion=False,
    )

    def _read_option_text_file(
        path: Path,
        option_name: str,
        *,
        require_mode: int | None = None,
    ) -> str:
        try:
            if require_mode is not None:
                mode = stat.S_IMODE(path.stat().st_mode)
                if mode != require_mode:
                    typer.echo(
                        f"ERROR: {option_name} {path} has mode {oct(mode)}, "
                        f"must be chmod {require_mode:o}",
                        err=True,
                    )
                    raise typer.Exit(code=2)
        except OSError as exc:
            typer.echo(f"ERROR: cannot read {option_name} {path}: {exc}", err=True)
            raise typer.Exit(code=2) from exc
        try:
            return path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            typer.echo(f"ERROR: cannot read {option_name} {path}: {exc}", err=True)
            raise typer.Exit(code=2) from exc

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
            ["python3", "scripts/checks/audit_forbidden.py", "--fail"],
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
            help="Skip interactive confirmation (CI mode)",
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
        from agmind.cli.deploy_cmd import cmd_deploy

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
        from agmind.cli.deploy_cmd import cmd_up

        raise typer.Exit(code=cmd_up(profile=profile, detach=detach))

    @deploy_app.command("down")
    def deploy_down(
        volumes: bool = typer.Option(False, "--volumes", help="Also remove named volumes."),
    ) -> None:
        """Backward-compatible docker compose down wrapper."""
        from agmind.cli.deploy_cmd import cmd_down

        raise typer.Exit(code=cmd_down(volumes=volumes))

    @deploy_app.command("status")
    def deploy_status() -> None:
        """Backward-compatible docker compose ps wrapper."""
        from agmind.cli.deploy_cmd import cmd_status

        raise typer.Exit(code=cmd_status())

    @deploy_app.command("ps")
    def deploy_ps(
        as_json: bool = typer.Option(False, "--json", help="JSON output"),
    ) -> None:
        """Backward-compatible docker compose ps wrapper."""
        from agmind.cli.deploy_cmd import cmd_ps

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
        from agmind.cli.deploy_cmd import cmd_logs

        raise typer.Exit(code=cmd_logs(service=service, follow=follow, lines=lines))

    @deploy_app.command("restart")
    def deploy_restart(
        service: str | None = typer.Argument(None, help="Service name."),
    ) -> None:
        """Backward-compatible docker compose restart wrapper."""
        from agmind.cli.deploy_cmd import cmd_restart

        raise typer.Exit(code=cmd_restart(service=service))

    @deploy_app.command("pull")
    def deploy_pull() -> None:
        """Backward-compatible docker compose pull wrapper."""
        from agmind.cli.deploy_cmd import cmd_pull

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
        from agmind.cli.deploy_cmd import cmd_rollback

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
        from agmind.cli.deploy_cmd import cmd_snapshots_list

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

    # ---- verify subcommand group (fresh-install/product gates) ----
    verify_app = typer.Typer(
        name="verify",
        help="Run non-destructive product readiness gates.",
        no_args_is_help=True,
    )
    app.add_typer(verify_app)

    @verify_app.command("install")
    def verify_install_cmd(
        domain: str = typer.Option(
            "lab.example.com",
            "--domain",
            help="Domain used for render/config validation.",
        ),
        scenario: list[str] | None = typer.Option(
            None,
            "--scenario",
            "-s",
            help="Fresh-install scenario to run; can be repeated.",
        ),
        as_json: bool = typer.Option(False, "--json", help="JSON output"),
        skip_ansible: bool = typer.Option(
            False,
            "--skip-ansible",
            help="Skip ansible-galaxy and ansible-playbook syntax checks.",
        ),
        skip_compose: bool = typer.Option(
            False,
            "--skip-compose",
            help="Skip docker compose config validation.",
        ),
        skip_galaxy: bool = typer.Option(
            False,
            "--skip-galaxy",
            help="Skip ansible-galaxy collection install before syntax check.",
        ),
        timeout_seconds: int = typer.Option(
            240,
            "--timeout",
            min=1,
            help="Per-command timeout in seconds.",
        ),
        work_dir: Path | None = typer.Option(
            None,
            "--work-dir",
            help="Keep verification artifacts under this directory.",
        ),
    ) -> None:
        """Prove `agmind setup` inputs can render/deploy cleanly without applying."""
        from agmind.cli.verify_cmd import cmd_install

        raise typer.Exit(
            code=cmd_install(
                domain=domain,
                scenarios=scenario,
                as_json=as_json,
                skip_ansible=skip_ansible,
                skip_compose=skip_compose,
                skip_galaxy=skip_galaxy,
                timeout_seconds=timeout_seconds,
                work_dir=work_dir,
            )
        )

    # ---- setup TUI wizard (full install entrypoint) ----
    @app.command()
    def setup(
        from_state: Path | None = typer.Option(
            None,
            "--from-state",
            help="Load saved state from JSON (non-interactive mode)",
        ),
        domain: str | None = typer.Option(
            None,
            "--domain",
            envvar="AGMIND_DOMAIN",
            help="Public domain для Traefik TLS (skip prompt if set).",
        ),
        cf_token_file: Path | None = typer.Option(
            None,
            "--cf-token-file",
            help="File с Cloudflare API token (skip prompt if set, chmod 600).",
        ),
        model_id: str = typer.Option(
            "",
            "--model-id",
            help="Curated model id (см. `agmind install --list-models`) или 'custom'.",
        ),
        model_repo: str = typer.Option("", "--model-repo", help="HF repo для custom LLM."),
        model_file: str = typer.Option("", "--model-file", help="GGUF filename для LLM."),
        ctx_size: int = typer.Option(0, "--ctx-size", help="Context size override."),
        kv_cache: str = typer.Option("", "--kv-cache", help="KV cache quant override."),
        list_models: bool = typer.Option(False, "--list-models", help="Print model catalog."),
        lang: str = typer.Option("", "--lang", help="UI language (en|ru)."),
        legacy_wizard: bool = typer.Option(False, "--legacy-wizard", help="Use legacy wizard."),
        no_tui: bool = typer.Option(False, "--no-tui", help="Headless install for CI."),
        dry_run: bool = typer.Option(
            False,
            "--dry-run",
            help="Только wizard/config, без bootstrap/pull/deploy.",
        ),
    ) -> None:
        """End-to-end setup: wizard → bootstrap → pulls → deploy, all in TUI by default."""
        install(
            domain=domain,
            cf_token_file=cf_token_file,
            model_id=model_id,
            model_repo=model_repo,
            model_file=model_file,
            ctx_size=ctx_size,
            kv_cache=kv_cache,
            list_models=list_models,
            lang=lang,
            legacy_wizard=legacy_wizard,
            no_tui=no_tui,
            dry_run=dry_run,
            from_state=from_state,
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

    # ---- tools subcommand group (optional homelab/enterprise integrations) ----
    tools_app = typer.Typer(
        name="tools",
        help="Inspect optional homelab/enterprise tool candidates",
        no_args_is_help=True,
    )
    app.add_typer(tools_app)

    # ---- ci subcommand group (GitHub Actions/self-hosted runner visibility) ----
    ci_app = typer.Typer(
        name="ci",
        help="Inspect GitHub Actions runs and self-hosted runners",
        no_args_is_help=True,
    )
    app.add_typer(ci_app)

    @ci_app.command("status")
    def ci_status(
        repository: str | None = typer.Option(
            None,
            "--repo",
            help="GitHub repository slug owner/name (default: git remote or AGMIND_GITHUB_REPO)",
        ),
        run_limit: int = typer.Option(10, "--limit", "-n", min=1, max=100),
        as_json: bool = typer.Option(False, "--json", help="JSON output"),
    ) -> None:
        """Show GitHub Actions queue and self-hosted runner state."""
        from agmind.cli.ci_cmd import cmd_status

        raise typer.Exit(
            code=cmd_status(repository=repository, run_limit=run_limit, as_json=as_json)
        )

    @tools_app.command("list")
    def tools_list(
        as_json: bool = typer.Option(False, "--json", help="JSON output"),
    ) -> None:
        """List optional tool candidates."""
        from agmind.cli.tools_cmd import cmd_list

        raise typer.Exit(code=cmd_list(as_json=as_json))

    @tools_app.command("status")
    def tools_status(
        name: str = typer.Argument(..., help="Tool candidate id"),
        as_json: bool = typer.Option(False, "--json", help="JSON output"),
    ) -> None:
        """Show one optional tool candidate."""
        from agmind.cli.tools_cmd import cmd_status

        raise typer.Exit(code=cmd_status(name=name, as_json=as_json))

    @tools_app.command("validate")
    def tools_validate() -> None:
        """Validate tool candidates and accepted runtime admission."""
        from agmind.cli.tools_cmd import cmd_validate

        raise typer.Exit(code=cmd_validate())

    # ---- targets subcommand group (universal deployment lanes) ----
    targets_app = typer.Typer(
        name="targets",
        help="Inspect deployment target contracts",
        no_args_is_help=True,
    )
    app.add_typer(targets_app)

    @targets_app.command("list")
    def targets_list(
        as_json: bool = typer.Option(False, "--json", help="JSON output"),
    ) -> None:
        """List deployment targets."""
        from agmind.cli.targets_cmd import cmd_list

        raise typer.Exit(code=cmd_list(as_json=as_json))

    @targets_app.command("status")
    def targets_status(
        name: str = typer.Argument(..., help="Deployment target id"),
        as_json: bool = typer.Option(False, "--json", help="JSON output"),
    ) -> None:
        """Show one deployment target."""
        from agmind.cli.targets_cmd import cmd_status

        raise typer.Exit(code=cmd_status(name=name, as_json=as_json))

    @targets_app.command("validate")
    def targets_validate(
        as_json: bool = typer.Option(False, "--json", help="JSON output"),
    ) -> None:
        """Validate deployment target contracts."""
        from agmind.cli.targets_cmd import cmd_validate

        raise typer.Exit(code=cmd_validate(as_json=as_json))

    # ---- governance subcommand group (aggregate M7 checks) ----
    governance_app = typer.Typer(
        name="governance",
        help="Run aggregate component/deploy/tool/dependency governance checks",
        no_args_is_help=True,
    )
    app.add_typer(governance_app)

    @governance_app.command("validate")
    def governance_validate(
        as_json: bool = typer.Option(False, "--json", help="JSON output"),
    ) -> None:
        """Run aggregate governance checks."""
        from agmind.cli.governance_cmd import cmd_validate

        raise typer.Exit(code=cmd_validate(as_json=as_json))

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
            help="Comma-separated profile names (ignored when --service is used)",
        ),
        service: list[str] | None = typer.Option(
            None,
            "--service",
            "-s",
            help="Explicit service name; can be repeated",
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
            services=service,
            output=output,
            traefik=not no_traefik,
            diff=diff,
            domain=domain,
        )
        raise typer.Exit(code=rc)

    @render_app.command("kubernetes")
    def render_kubernetes(
        profile: str = typer.Option(
            "core",
            "--profile",
            "-p",
            help="Comma-separated profile names (ignored when --service is used)",
        ),
        service: list[str] | None = typer.Option(
            None,
            "--service",
            "-s",
            help="Explicit service name; can be repeated",
        ),
        output: Path | None = typer.Option(
            None, "--output", "-o", help="Output file (default: stdout)"
        ),
        namespace: str = typer.Option(
            "agmind",
            "--namespace",
            "-n",
            help="Kubernetes namespace for rendered objects",
        ),
        strict: bool = typer.Option(
            False,
            "--strict",
            help="Fail if selected descriptors contain Docker-only fields",
        ),
        target: str | None = typer.Option(
            None,
            "--target",
            help="Deployment target id; uses target profiles and exclusions",
        ),
        no_namespace: bool = typer.Option(
            False,
            "--no-namespace",
            help="Do not emit a Namespace object",
        ),
    ) -> None:
        """Render Kubernetes manifests from ServiceDescriptor catalog."""
        from agmind.cli.render_cmd import cmd_render_kubernetes

        profiles = [p.strip() for p in profile.split(",") if p.strip()]
        rc = cmd_render_kubernetes(
            profiles=profiles,
            services=service,
            output=output,
            namespace=namespace,
            strict=strict,
            include_namespace=not no_namespace,
            target_id=target,
        )
        raise typer.Exit(code=rc)

    @render_app.command("topology")
    def render_topology(
        profile: str = typer.Option(
            "core",
            "--profile",
            "-p",
            help="Comma-separated profile names (ignored when --service is used)",
        ),
        service: list[str] | None = typer.Option(
            None,
            "--service",
            "-s",
            help="Explicit service name; can be repeated",
        ),
        as_json: bool = typer.Option(False, "--json", help="JSON output"),
        fail_on_warning: bool = typer.Option(
            False,
            "--fail-on-warning",
            help="Exit 2 when topology warnings are present",
        ),
    ) -> None:
        """Render selected-service topology warnings and RAG storage plan."""
        from agmind.cli.render_cmd import cmd_render_topology

        profiles = [p.strip() for p in profile.split(",") if p.strip()]
        rc = cmd_render_topology(
            profiles=profiles,
            services=service,
            as_json=as_json,
            fail_on_warning=fail_on_warning,
        )
        raise typer.Exit(code=rc)

    # ---- cluster subcommand group (Phase M4.U.1 — mDNS auto-detect) ----
    cluster_app = typer.Typer(
        name="cluster",
        help="Multi-node coordination — mDNS-based peer discovery.",
        no_args_is_help=True,
    )
    app.add_typer(cluster_app)

    @cluster_app.command("detect")
    def cluster_detect(
        timeout: float = typer.Option(
            3.0,
            "--timeout",
            "-t",
            help="Discovery duration в секундах",
        ),
        as_json: bool = typer.Option(False, "--json", help="JSON output"),
    ) -> None:
        """Browse LAN для agmind peers via mDNS (one-shot)."""
        from agmind.cli.cluster_cmd import cmd_detect

        raise typer.Exit(code=cmd_detect(timeout=timeout, as_json=as_json))

    @cluster_app.command("advertise")
    def cluster_advertise(
        port: int = typer.Option(
            41423,
            "--port",
            "-p",
            help="Port для service advertisement",
        ),
        duration: float = typer.Option(
            0.0,
            "--duration",
            "-d",
            help="Stop после N seconds (0 = forever / Ctrl+C)",
        ),
    ) -> None:
        """Publish this node как `_agmind._tcp.local.` service (daemon mode)."""
        from agmind.cli.cluster_cmd import cmd_advertise

        raise typer.Exit(code=cmd_advertise(port=port, duration=duration))

    @cluster_app.command("status")
    def cluster_status(
        timeout: float = typer.Option(3.0, "--timeout", "-t"),
        as_json: bool = typer.Option(False, "--json", help="JSON output"),
    ) -> None:
        """Show this node info + discovered peers."""
        from agmind.cli.cluster_cmd import cmd_status

        raise typer.Exit(code=cmd_status(timeout=timeout, as_json=as_json))

    @cluster_app.command("inspect")
    def cluster_inspect(
        timeout: float = typer.Option(3.0, "--timeout", "-t"),
        as_json: bool = typer.Option(False, "--json", help="JSON output"),
    ) -> None:
        """Inspect local runtime/cluster environment and recommend deploy target."""
        from agmind.cli.cluster_cmd import cmd_inspect

        raise typer.Exit(code=cmd_inspect(timeout=timeout, as_json=as_json))

    # ---- upgrade subcommand group (Phase M3.R) ----
    upgrade_app = typer.Typer(
        name="upgrade",
        help="Bump pinned image versions + safely redeploy с rollback.",
        no_args_is_help=False,
        invoke_without_command=True,
    )
    app.add_typer(upgrade_app)

    @upgrade_app.callback(invoke_without_command=True)
    def upgrade_cb(
        ctx: typer.Context,
        component: str | None = typer.Option(
            None,
            "--component",
            "-c",
            help="Service name (e.g. ragflow). Bump его image tag.",
        ),
        version: str | None = typer.Option(
            None,
            "--version",
            "-v",
            help="Target tag (e.g. v0.25.5). Required с --component.",
        ),
        digest: str | None = typer.Option(
            None,
            "--digest",
            help="Optional sha256 digest (без `sha256:` prefix).",
        ),
        check: bool = typer.Option(
            False,
            "--check",
            help="Run version_check.py scanner и выйти.",
        ),
        apply: bool = typer.Option(
            False,
            "--apply",
            help="Re-deploy after bump (uses Phase L.B runner).",
        ),
        plan: bool = typer.Option(
            False,
            "--plan",
            help="Print component update plan without editing files.",
        ),
        rollback: bool = typer.Option(
            False,
            "--rollback",
            help="Revert last bump (read latest state + restore template).",
        ),
        force: bool = typer.Option(
            False,
            "--force",
            help="Bump даже если pin в version_holds.yaml.",
        ),
    ) -> None:
        """Phase M3.R: upgrade lifecycle."""
        if ctx.invoked_subcommand is not None:
            return

        from agmind.cli.upgrade_cmd import (
            cmd_apply,
            cmd_check,
            cmd_component,
            cmd_rollback,
        )

        if check:
            raise typer.Exit(code=cmd_check())
        if rollback:
            raise typer.Exit(code=cmd_rollback())
        if apply and not component:
            raise typer.Exit(code=cmd_apply())
        if component:
            if version is None:
                typer.echo("ERROR: --component requires --version", err=True)
                raise typer.Exit(code=2)
            rc = cmd_component(
                service=component,
                version=version,
                force=force,
                digest=digest,
                plan_only=plan,
            )
            if rc != 0 or not apply or plan:
                raise typer.Exit(code=rc)
            raise typer.Exit(code=cmd_apply())

        typer.echo(
            "Usage: agmind upgrade [--check | --component X --version Y "
            "[--plan] [--apply] | --apply | --rollback]"
        )
        raise typer.Exit(code=2)

    # ---- models subcommand group (Phase M3.Q) ----
    models_app = typer.Typer(
        name="models",
        help="Manage GGUF model files в /var/lib/agmind/models/.",
        no_args_is_help=True,
    )
    app.add_typer(models_app)

    @models_app.command("list")
    def models_list(
        local: bool = typer.Option(
            True,
            "--local/--catalog",
            help="--local: scan models dir (default). --catalog: legacy registry.",
        ),
        as_json: bool = typer.Option(False, "--json", help="JSON output"),
    ) -> None:
        """List local *.gguf files OR legacy registry tiers."""
        from agmind.cli.models_cmd import cmd_list, cmd_list_local

        if local:
            raise typer.Exit(code=cmd_list_local(as_json=as_json))
        raise typer.Exit(code=cmd_list(as_json=as_json))

    @models_app.command("info")
    def models_info(
        model_id: str | None = typer.Argument(
            None,
            help="Curated model id (см. `agmind install --list-models`)",
        ),
        file: str | None = typer.Option(
            None,
            "--file",
            help="Inspect local file by name (in models dir or absolute path)",
        ),
    ) -> None:
        """Show details для curated model OR local file."""
        from agmind.cli.models_cmd import cmd_info

        raise typer.Exit(code=cmd_info(model_id=model_id, file=file))

    @models_app.command("pull")
    def models_pull(
        model_id: str | None = typer.Argument(
            None,
            help="Curated id (e.g. qwen36-a3b-q4km)",
        ),
        repo: str | None = typer.Option(
            None,
            "--repo",
            help="HF repo (для custom — combine with --file)",
        ),
        file: str | None = typer.Option(
            None,
            "--file",
            help="GGUF filename in HF repo",
        ),
        force: bool = typer.Option(
            False,
            "--force",
            help="Re-download даже если уже есть",
        ),
    ) -> None:
        """Download GGUF model from curated catalog или custom HF repo."""
        from agmind.cli.models_cmd import cmd_pull

        raise typer.Exit(
            code=cmd_pull(
                model_id=model_id,
                repo=repo,
                file=file,
                force=force,
            )
        )

    @models_app.command("rm")
    def models_rm(
        model_id: str | None = typer.Argument(
            None,
            help="Curated id (resolves to filename in models dir)",
        ),
        file: str | None = typer.Option(
            None,
            "--file",
            help="Remove by filename (resolved against models dir)",
        ),
        force: bool = typer.Option(
            False,
            "--force",
            help="Remove даже если referenced в /opt/agmind/.env",
        ),
    ) -> None:
        """Delete model file. Warns если used by running compose."""
        from agmind.cli.models_cmd import cmd_rm

        raise typer.Exit(code=cmd_rm(model_id=model_id, file=file, force=force))

    @models_app.command("path")
    def models_path(
        name: str = typer.Argument(..., help="llm | embed | rerank | vlm"),
        tier: str | None = typer.Option(None, "--tier", help="S/M/L/XL/XXL"),
    ) -> None:
        """Print local path для named model (legacy registry)."""
        from agmind.cli.models_cmd import cmd_path

        raise typer.Exit(code=cmd_path(name=name, tier=tier))

    @models_app.command("verify")
    def models_verify(
        tier: str | None = typer.Option(None, "--tier", help="S/M/L/XL/XXL"),
    ) -> None:
        """Verify locally downloaded models (size + existence)."""
        from agmind.cli.models_cmd import cmd_verify

        raise typer.Exit(code=cmd_verify(tier=tier))

    # ---- install command (Phase N) ----

    @app.command()
    def install(
        domain: str | None = typer.Option(
            None,
            "--domain",
            envvar="AGMIND_DOMAIN",
            help="Public domain для Traefik TLS (skip prompt if set).",
        ),
        cf_token_file: Path | None = typer.Option(
            None,
            "--cf-token-file",
            help="File с Cloudflare API token (skip prompt if set, chmod 600).",
        ),
        model_id: str = typer.Option(
            "",
            "--model-id",
            help="Curated model id (см. `agmind install --list-models`) или 'custom'.",
        ),
        model_repo: str = typer.Option(
            "",
            "--model-repo",
            help="HF repo (для custom). Перекрывает curated.",
        ),
        model_file: str = typer.Option(
            "",
            "--model-file",
            help="GGUF filename. Empty + non-custom id → resolved из catalog.",
        ),
        ctx_size: int = typer.Option(
            0,
            "--ctx-size",
            help="Context size override (0 = use wizard / model suggested).",
        ),
        kv_cache: str = typer.Option(
            "",
            "--kv-cache",
            help="KV cache quant (q8_0 / q4_0 / f16). Empty = wizard default.",
        ),
        list_models: bool = typer.Option(
            False,
            "--list-models",
            help="Print curated model catalog и выйти.",
        ),
        lang: str = typer.Option(
            "",
            "--lang",
            help="UI language (en|ru). Default — auto-detect via LANG env.",
        ),
        legacy_wizard: bool = typer.Option(
            False,
            "--legacy-wizard",
            help="Force legacy single-screen wizard (default — multi-step с Phase M4).",
        ),
        no_tui: bool = typer.Option(
            False,
            "--no-tui",
            help="CLI-only run без Textual UI (для CI / headless).",
        ),
        dry_run: bool = typer.Option(
            False,
            "--dry-run",
            help="Только preflight + wizard, без bootstrap/pull/deploy.",
        ),
        from_state: Path | None = typer.Option(
            None,
            "--from-state",
            help="Load saved setup state JSON before opening wizard.",
        ),
    ) -> None:
        """Phase N: end-to-end install (wizard → bootstrap → pull → deploy).

        В TUI sudo password собирается скрытым input внутри wizard и нужен
        только для bootstrap step. В `--no-tui` режиме пароль запрашивается
        обычным terminal prompt.
        """
        import getpass

        from agmind.cli.tui.setup_wizard import (
            SetupState,
            run_setup_wizard,
        )
        from agmind.install.models import CURATED_MODELS
        from agmind.install.orchestrator import (
            InstallConfig,
            InstallOrchestrator,
        )
        from agmind.install.steps import default_steps

        # Phase M3.T: set AGMIND_LANG для i18n.detect_lang()
        if lang:
            import os as _os

            _os.environ["AGMIND_LANG"] = lang.strip().lower()

        # 0. --list-models — print catalog и выйти
        if list_models:
            typer.echo(f"{'ID':<22} {'NAME':<35} {'SIZE':>8} {'QUANT':<8} CTX")
            typer.echo("-" * 90)
            for m in CURATED_MODELS:
                marker = "★" if m.strix_tested else " "
                typer.echo(
                    f"{marker} {m.id:<20} {m.name:<35} {m.size_gib:>6.1f}GB "
                    f"{m.quant:<8} {m.suggested_ctx}"
                )
            typer.echo("\n★ = measured on Strix Halo (Phase H verified)")
            raise typer.Exit(code=0)

        # 1. Wizard для domain/token/services/sudo (или skip если no_tui).
        initial = SetupState(
            domain=domain or "",
            cf_api_token=_read_option_text_file(
                cf_token_file,
                "--cf-token-file",
                require_mode=0o600,
            )
            if cf_token_file
            else "",
            model_id=model_id or "qwen36-a3b-q4km",
            model_repo=model_repo,
            model_file=model_file,
            ctx_size=ctx_size or 16384,
            kv_cache_type=kv_cache or "q8_0",
        )
        if from_state is not None:
            try:
                from_state.stat()
            except OSError as exc:
                typer.echo(f"ERROR: cannot read --from-state {from_state}: {exc}", err=True)
                raise typer.Exit(code=2) from exc
            try:
                loaded = SetupState.from_json(from_state)
            except Exception as exc:  # noqa: BLE001
                typer.echo(f"ERROR: cannot load --from-state {from_state}: {exc}", err=True)
                raise typer.Exit(code=2) from exc
            else:
                initial = loaded
                if not initial.services and initial.profiles:
                    from agmind.services.renderer import (
                        load_descriptors,
                        select_services,
                        unknown_profiles,
                    )

                    descriptors = load_descriptors()
                    missing_profiles = unknown_profiles(descriptors, initial.profiles)
                    if missing_profiles:
                        typer.echo(
                            "ERROR: unknown selected profiles in --from-state: "
                            + ", ".join(missing_profiles),
                            err=True,
                        )
                        raise typer.Exit(code=2)
                    selected_services = sorted(
                        select_services(descriptors, profiles=initial.profiles)
                    )
                    if not selected_services:
                        profile_text = ", ".join(initial.profiles)
                        typer.echo(
                            f"ERROR: no services match profiles in --from-state: {profile_text}",
                            err=True,
                        )
                        raise typer.Exit(code=2)
                    initial.services = selected_services
                    initial.profiles = []
                if initial.services:
                    from agmind.services.renderer import load_descriptors

                    descriptors = load_descriptors()
                    missing_services = sorted(set(initial.services).difference(descriptors))
                    if missing_services:
                        typer.echo(
                            "ERROR: unknown selected services in --from-state: "
                            + ", ".join(missing_services),
                            err=True,
                        )
                        raise typer.Exit(code=2)
                if not initial.services and not initial.profiles:
                    typer.echo("ERROR: no selected services in --from-state", err=True)
                    raise typer.Exit(code=2)
                if domain:
                    initial.domain = domain
                if cf_token_file:
                    initial.cf_api_token = _read_option_text_file(
                        cf_token_file,
                        "--cf-token-file",
                        require_mode=0o600,
                    )
                if model_id:
                    initial.model_id = model_id
                if model_repo:
                    initial.model_repo = model_repo
                if model_file:
                    initial.model_file = model_file
                if ctx_size:
                    initial.ctx_size = ctx_size
                if kv_cache:
                    initial.kv_cache_type = kv_cache
        if not no_tui:
            # M4.1: multi-step wizard default; --legacy-wizard для escape hatch
            ms = False if legacy_wizard else None  # None = default (multi-step)
            wizard_state = run_setup_wizard(
                initial_state=initial,
                auto_deploy=False,
                multi_step=ms,
                install_mode=not dry_run,
                require_sudo_password=not dry_run,
            )
            if wizard_state is None:
                typer.echo("aborted: wizard cancelled", err=True)
                raise typer.Exit(code=1)
            if not dry_run:
                install_result = getattr(wizard_state, "_install_result", None)
                if install_result is None:
                    typer.echo("aborted: install did not return a result", err=True)
                    raise typer.Exit(code=1)
                typer.echo(f"\n{'✓' if install_result.success else '✗'} {install_result.message}")
                raise typer.Exit(code=0 if install_result.success else 1)
        else:
            wizard_state = initial
            if not dry_run:
                validation_errors: list[str] = []
                try:
                    wizard_state.domain = validate_domain(wizard_state.domain)
                except ValueError as exc:
                    validation_errors.append(f"domain invalid: {exc}")
                if len(wizard_state.cf_api_token) < 20:
                    validation_errors.append(
                        "CF API token < 20 chars — provide --cf-token-file with chmod 600"
                    )
                if not wizard_state.services and not wizard_state.profiles:
                    validation_errors.append("Выбери хотя бы один service")
                if validation_errors:
                    for error in validation_errors:
                        typer.echo(f"ERROR: {error}", err=True)
                    raise typer.Exit(code=2)
                try:
                    sudo_pw = getpass.getpass("Sudo password (для apt/usermod/mkdir): ")
                except (EOFError, KeyboardInterrupt):
                    typer.echo("\naborted: sudo password не введён", err=True)
                    raise typer.Exit(code=2)
                if not sudo_pw:
                    typer.echo("aborted: empty sudo password", err=True)
                    raise typer.Exit(code=2)
                wizard_state.sudo_password = sudo_pw

        # 3. Resolve final model repo/file (curated or custom) — для каждого role.
        final_repo, final_file = wizard_state.resolve_model_repo_file()
        # CLI flags override wizard LLM values if provided (kept legacy semantics).
        if model_repo:
            final_repo = model_repo
        if model_file:
            final_file = model_file
        embed_repo, embed_file = wizard_state.resolve_embed_repo_file()
        rerank_repo, rerank_file = wizard_state.resolve_rerank_repo_file()

        config = InstallConfig(
            domain=wizard_state.domain,
            cf_api_token=wizard_state.cf_api_token,
            services=wizard_state.services,
            backend=wizard_state.backend,
            install_dir=Path(wizard_state.install_dir),
            model_repo=final_repo if final_file else None,
            model_file=final_file if final_file else None,
            ctx_size=ctx_size or wizard_state.ctx_size,
            kv_cache_type=kv_cache or wizard_state.kv_cache_type,
            threads=wizard_state.threads,
            parallel_slots=wizard_state.parallel_slots,
            embed_repo=embed_repo if embed_file else None,
            embed_file=embed_file if embed_file else None,
            embed_ctx_size=wizard_state.embed_ctx_size,
            embed_kv_cache=wizard_state.embed_kv_cache,
            embed_parallel=wizard_state.embed_parallel,
            rerank_repo=rerank_repo if rerank_file else None,
            rerank_file=rerank_file if rerank_file else None,
            rerank_ctx_size=wizard_state.rerank_ctx_size,
            sudo_password=wizard_state.sudo_password,
        )

        if dry_run:
            typer.echo("dry-run: stopping после wizard")
            typer.echo(json.dumps(config.redact(), indent=2, ensure_ascii=False))
            raise typer.Exit(code=0)

        # 4. Orchestrator + progress.
        steps = default_steps()
        if no_tui:

            def cli_cb(event) -> None:  # type: ignore[no-untyped-def]
                from agmind.install.orchestrator import ProgressKind

                glyph = {
                    ProgressKind.STEP_START: "▶",
                    ProgressKind.STEP_DONE: "✓",
                    ProgressKind.STEP_ERROR: "✗",
                    ProgressKind.LOG: " ",
                    ProgressKind.PROGRESS: "%",
                }.get(event.kind, "·")
                typer.echo(f"[{glyph}] {event.step_id}: {event.text}")

            orchestrator = InstallOrchestrator(config=config, steps=steps, callback=cli_cb)
            result = orchestrator.run()
            typer.echo(f"Runtime credentials: {config.install_dir / '.env'} (chmod 600)")
            typer.echo("Values are not printed in the installer summary.")
            raise typer.Exit(code=0 if result.success else 1)

        from textual.app import App

        from agmind.cli.tui.install_screen import InstallProgressScreen

        class _InstallShell(App[None]):
            CSS_PATH = None

            def on_mount(self) -> None:
                self.push_screen(InstallProgressScreen(config=config, steps=steps))

        _InstallShell().run()

    # ---- ops subcommands: logs / shell / backup / restore (Phase L.E) ----

    @app.command()
    def logs(
        service: str | None = typer.Argument(None, help="Service name (omit для всех сервисов)."),
        install_dir: Path = typer.Option(
            Path("/opt/agmind"), "--install-dir", help="Deployment dir."
        ),
        tail: int = typer.Option(200, "--tail", help="Initial backlog lines."),
        follow: bool = typer.Option(False, "-f", "--follow", help="Stream new logs."),
        ask_sudo_password: bool = typer.Option(
            False,
            "--ask-sudo-password",
            help="Prompt for sudo password for root-owned install paths",
        ),
    ) -> None:
        """Stream docker compose logs."""
        from agmind.cli.ops_cmd import cmd_logs

        raise typer.Exit(
            code=cmd_logs(
                service,
                install_dir,
                tail,
                follow,
                ask_sudo_password=ask_sudo_password,
            )
        )

    @app.command()
    def shell(
        service: str = typer.Argument(..., help="Service name."),
        install_dir: Path = typer.Option(
            Path("/opt/agmind"), "--install-dir", help="Deployment dir."
        ),
        cmd: str = typer.Option("/bin/sh", "--cmd", help="Command to run (default /bin/sh)."),
        workdir: str | None = typer.Option(
            None, "--workdir", "-w", help="Working dir внутри container."
        ),
        ask_sudo_password: bool = typer.Option(
            False,
            "--ask-sudo-password",
            help="Prompt for sudo password for root-owned install paths",
        ),
    ) -> None:
        """Open shell inside running service container (docker compose exec)."""
        import shlex

        from agmind.cli.ops_cmd import cmd_shell

        cmd_list = shlex.split(cmd) if cmd else None
        raise typer.Exit(
            code=cmd_shell(
                service,
                install_dir,
                cmd_list,
                workdir,
                ask_sudo_password=ask_sudo_password,
            )
        )

    @app.command()
    def backup(
        output: Path = typer.Option(
            ...,
            "--output",
            "-o",
            help="Path для .tar.gz (e.g. agmind-2026-05-20.tar.gz).",
        ),
        ask_sudo_password: bool = typer.Option(
            False,
            "--ask-sudo-password",
            help="Prompt for sudo password for root-owned install/snapshot paths",
        ),
    ) -> None:
        """Create tar.gz backup of compose / .env / state / snapshots (Phase L.E)."""
        from agmind.cli.ops_cmd import cmd_backup

        raise typer.Exit(code=cmd_backup(output, ask_sudo_password=ask_sudo_password))

    @app.command()
    def restore(
        backup_file: Path = typer.Argument(..., help="Path to .tar.gz backup."),
        yes: bool = typer.Option(False, "-y", "--yes", help="Skip interactive confirmation."),
        ask_sudo_password: bool = typer.Option(
            False,
            "--ask-sudo-password",
            help="Prompt for sudo password for root-owned install/snapshot paths",
        ),
    ) -> None:
        """Restore deployment from `agmind backup` archive (Phase L.E)."""
        from agmind.cli.ops_cmd import cmd_restore

        raise typer.Exit(
            code=cmd_restore(backup_file, yes=yes, ask_sudo_password=ask_sudo_password)
        )

    ops_app = typer.Typer(
        name="ops",
        help="Run day-2 operator helpers.",
        no_args_is_help=True,
    )
    ops_smoke_app = typer.Typer(
        name="smoke",
        help="Run non-destructive operator smoke checks.",
        no_args_is_help=True,
    )
    ops_app.add_typer(ops_smoke_app)
    app.add_typer(ops_app)

    @ops_smoke_app.command("backup-root-owned")
    def ops_smoke_backup_root_owned(
        root: Path = typer.Option(
            Path("/tmp/agmind-root-owned-smoke"),
            "--root",
            help="Temporary smoke root under /tmp.",
        ),
        output: Path = typer.Option(
            Path("/tmp/agmind-root-owned-smoke.tar.gz"),
            "--output",
            "-o",
            help="Backup archive path under /tmp.",
        ),
        dry_run: bool = typer.Option(False, "--dry-run", help="Print plan without sudo."),
        keep: bool = typer.Option(False, "--keep", help="Keep temporary smoke tree."),
    ) -> None:
        """Smoke backup/restore against root-owned temporary paths."""
        from agmind.cli.ops_cmd import cmd_root_owned_backup_smoke

        raise typer.Exit(
            code=cmd_root_owned_backup_smoke(
                root=root,
                output=output,
                dry_run=dry_run,
                keep=keep,
            )
        )

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
