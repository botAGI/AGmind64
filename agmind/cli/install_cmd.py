"""End-to-end `agmind install` / `agmind setup` commands (Phase N).

Registration only — the wizard, orchestrator, install steps and Textual UI are
imported lazily inside the command body so that building the app does not pull
in the full install stack.
"""

from __future__ import annotations

import json
import stat
from pathlib import Path

import typer

from agmind.core.domain import validate_domain


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


def register(app: typer.Typer) -> None:
    """Attach the ``setup`` and ``install`` commands to ``app``."""

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
            from agmind.cli.install_state import (
                StateResolveError,
                load_setup_state_from_file,
            )

            try:
                initial = load_setup_state_from_file(from_state)
            except StateResolveError as exc:
                typer.echo(f"ERROR: {exc.message}", err=True)
                raise typer.Exit(code=exc.code) from exc

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
