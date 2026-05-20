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
            None, "--component", "-c",
            help="Service name (e.g. ragflow). Bump его image tag.",
        ),
        version: str | None = typer.Option(
            None, "--version", "-v",
            help="Target tag (e.g. v0.25.5). Required с --component.",
        ),
        digest: str | None = typer.Option(
            None, "--digest",
            help="Optional sha256 digest (без `sha256:` prefix).",
        ),
        check: bool = typer.Option(
            False, "--check",
            help="Run version_check.py scanner и выйти.",
        ),
        apply: bool = typer.Option(
            False, "--apply",
            help="Re-deploy after bump (uses Phase L.B runner).",
        ),
        rollback: bool = typer.Option(
            False, "--rollback",
            help="Revert last bump (read latest state + restore template).",
        ),
        force: bool = typer.Option(
            False, "--force",
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
                service=component, version=version, force=force, digest=digest,
            )
            if rc != 0 or not apply:
                raise typer.Exit(code=rc)
            raise typer.Exit(code=cmd_apply())

        typer.echo("Usage: agmind upgrade [--check | --component X --version Y "
                  "[--apply] | --apply | --rollback]")
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
            True, "--local/--catalog",
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
            None, help="Curated model id (см. `agmind install --list-models`)",
        ),
        file: str | None = typer.Option(
            None, "--file", help="Inspect local file by name (in models dir or absolute path)",
        ),
    ) -> None:
        """Show details для curated model OR local file."""
        from agmind.cli.models_cmd import cmd_info

        raise typer.Exit(code=cmd_info(model_id=model_id, file=file))

    @models_app.command("pull")
    def models_pull(
        model_id: str | None = typer.Argument(
            None, help="Curated id (e.g. qwen36-a3b-q4km)",
        ),
        repo: str | None = typer.Option(
            None, "--repo", help="HF repo (для custom — combine with --file)",
        ),
        file: str | None = typer.Option(
            None, "--file", help="GGUF filename in HF repo",
        ),
        force: bool = typer.Option(
            False, "--force", help="Re-download даже если уже есть",
        ),
    ) -> None:
        """Download GGUF model from curated catalog или custom HF repo."""
        from agmind.cli.models_cmd import cmd_pull

        raise typer.Exit(code=cmd_pull(
            model_id=model_id, repo=repo, file=file, force=force,
        ))

    @models_app.command("rm")
    def models_rm(
        model_id: str | None = typer.Argument(
            None, help="Curated id (resolves to filename in models dir)",
        ),
        file: str | None = typer.Option(
            None, "--file", help="Remove by filename (resolved against models dir)",
        ),
        force: bool = typer.Option(
            False, "--force",
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
            None, "--domain", envvar="AGMIND_DOMAIN",
            help="Public domain для Traefik TLS (skip prompt if set).",
        ),
        cf_token_file: Path | None = typer.Option(
            None, "--cf-token-file",
            help="File с Cloudflare API token (skip prompt if set, chmod 600).",
        ),
        model_id: str = typer.Option(
            "", "--model-id",
            help="Curated model id (см. `agmind install --list-models`) или 'custom'.",
        ),
        model_repo: str = typer.Option(
            "", "--model-repo",
            help="HF repo (для custom). Перекрывает curated.",
        ),
        model_file: str = typer.Option(
            "", "--model-file",
            help="GGUF filename. Empty + non-custom id → resolved из catalog.",
        ),
        ctx_size: int = typer.Option(
            0, "--ctx-size",
            help="Context size override (0 = use wizard / model suggested).",
        ),
        kv_cache: str = typer.Option(
            "", "--kv-cache",
            help="KV cache quant (q8_0 / q4_0 / f16). Empty = wizard default.",
        ),
        list_models: bool = typer.Option(
            False, "--list-models",
            help="Print curated model catalog и выйти.",
        ),
        no_tui: bool = typer.Option(
            False, "--no-tui",
            help="CLI-only run без Textual UI (для CI / headless).",
        ),
        dry_run: bool = typer.Option(
            False, "--dry-run",
            help="Только preflight + wizard, без bootstrap/pull/deploy.",
        ),
    ) -> None:
        """Phase N: end-to-end install (preflight → bootstrap → pull → deploy).

        Запрашивает sudo password один раз для bootstrap step (apt, usermod,
        mkdir в /var/lib и /opt). После bootstrap всё остальное идёт от user.
        """
        import getpass

        from agmind.cli.tui.setup_wizard import (
            STATE_PATH,
            SetupState,
            run_setup_wizard,
        )
        from agmind.install.models import CURATED_MODELS, find_by_id
        from agmind.install.orchestrator import (
            InstallConfig,
            InstallOrchestrator,
        )
        from agmind.install.steps import default_steps

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

        # 1. Sudo password — раньше чем что-либо.
        try:
            sudo_pw = getpass.getpass("Sudo password (для apt/usermod/mkdir): ")
        except (EOFError, KeyboardInterrupt):
            typer.echo("\naborted: sudo password не введён", err=True)
            raise typer.Exit(code=2)
        if not sudo_pw:
            typer.echo("aborted: empty sudo password", err=True)
            raise typer.Exit(code=2)

        # 2. Wizard для domain/token/services (или skip если no_tui).
        initial = SetupState(
            domain=domain or "",
            cf_api_token=cf_token_file.read_text().strip() if cf_token_file else "",
            model_id=model_id or "qwen36-a3b-q4km",
            model_repo=model_repo,
            model_file=model_file,
            ctx_size=ctx_size or 16384,
            kv_cache_type=kv_cache or "q8_0",
        )
        if not no_tui:
            wizard_state = run_setup_wizard(initial_state=initial, auto_deploy=False)
            if wizard_state is None:
                typer.echo("aborted: wizard cancelled", err=True)
                raise typer.Exit(code=1)
        else:
            wizard_state = initial

        # 3. Resolve final model repo/file (curated or custom).
        final_repo, final_file = wizard_state.resolve_model_repo_file()
        # CLI flags override wizard values if provided.
        if model_repo:
            final_repo = model_repo
        if model_file:
            final_file = model_file

        config = InstallConfig(
            domain=wizard_state.domain,
            cf_api_token=wizard_state.cf_api_token,
            services=wizard_state.services,
            backend=wizard_state.backend,
            model_repo=final_repo if final_file else None,
            model_file=final_file if final_file else None,
            ctx_size=ctx_size or wizard_state.ctx_size,
            kv_cache_type=kv_cache or wizard_state.kv_cache_type,
            sudo_password=sudo_pw,
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
            raise typer.Exit(code=0 if result.success else 1)

        from agmind.cli.tui.install_screen import InstallProgressScreen
        from textual.app import App

        class _InstallShell(App[None]):
            CSS_PATH = None

            def on_mount(self) -> None:
                self.push_screen(InstallProgressScreen(config=config, steps=steps))

        _InstallShell().run()

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
