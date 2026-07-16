"""End-to-end `agmind install` / `agmind setup` commands (Phase N).

Registration only — the wizard, orchestrator, install steps and Textual UI are
imported lazily inside the command body so that building the app does not pull
in the full install stack.
"""

from __future__ import annotations

import json
import stat
import subprocess
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


def _sudo_nopasswd_available() -> bool:
    try:
        completed = subprocess.run(
            ["sudo", "-n", "true"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0


def _traefik_in_selection(services: list[str], profiles: list[str]) -> bool:
    """True when traefik (the TLS edge) is in the effective deploy selection.

    Explicit ``services`` win over ``profiles`` (mirrors ``renderer.select_services``);
    traefik ships in the ``core``/``full`` profiles. The Cloudflare token and the public
    domain are required ONLY when this is True — a local / non-traefik install needs neither.
    """
    if services:
        return "traefik" in services
    from agmind.services.renderer import filter_by_profile, load_descriptors

    return "traefik" in filter_by_profile(load_descriptors(), profiles or [])


def _headless_validation_errors(
    services: list[str], profiles: list[str], domain: str, cf_api_token: str
) -> tuple[list[str], str]:
    """Validate a ``--no-tui`` selection. Returns ``(errors, normalized_domain)``.

    Mirrors ``CloudflareTokenStep`` so the CLI gate and the install step agree: the
    Cloudflare DNS token and the public domain are demanded only when traefik terminates
    TLS. A non-traefik install with an empty token/domain is valid.
    """
    errors: list[str] = []
    normalized_domain = domain
    if _traefik_in_selection(services, profiles):
        try:
            normalized_domain = validate_domain(domain)
        except ValueError as exc:
            errors.append(f"domain invalid: {exc}")
        if len(cf_api_token) < 20:
            errors.append("CF API token < 20 chars — provide --cf-token-file with chmod 600")
    if not services and not profiles:
        errors.append("Select at least one service")
    return errors, normalized_domain


def _run_cmd(cmd: list[str]) -> int:
    """Run a teardown command, streaming output; return its rc. Seam for tests."""
    try:
        return subprocess.run(cmd, check=False).returncode
    except OSError as exc:  # docker / sudo not on PATH
        typer.echo(f"  ! {' '.join(cmd[:3])}…: {exc}", err=True)
        return 1


def _docker_ids(filter_cmd: list[str]) -> list[str]:
    """Capture docker object ids for a list/ps query (empty on any failure). Seam for tests."""
    try:
        out = subprocess.run(filter_cmd, capture_output=True, text=True, timeout=15, check=False)
    except (OSError, subprocess.SubprocessError):
        return []
    return [tok for tok in out.stdout.split() if tok]


_FORBIDDEN_UNINSTALL_ANCESTORS: frozenset[Path] = frozenset(
    {Path("/"), Path("/home"), Path("/etc"), Path("/var"), Path("/opt"), Path.home()}
)


def _forbidden_uninstall_target(resolved: Path) -> str | None:
    """Refusal reason if ``resolved`` IS (not merely under) a forbidden ancestor, else None.

    EXACT equality only — NOT ``is_relative_to`` — so a real install dir that happens to be a
    CHILD of a forbidden ancestor (e.g. the default ``/opt/agmind``, a child of ``/opt``) is
    still allowed. Guards against a `--install-dir /` / `/etc` / `$HOME` operator typo feeding
    straight into `sudo rm -rf` (P0.10 / D-02).
    """
    if resolved in _FORBIDDEN_UNINSTALL_ANCESTORS:
        return f"{resolved} is a forbidden ancestor path, not a specific install directory"
    return None


_AGMIND_COMPOSE_PROJECT_LABEL_FILTER = "label=com.docker.compose.project=agmind"


def _sweep_agmind_runtime(*, volumes: bool) -> None:
    """Force-remove any lingering agmind containers / networks (and, with volumes, agmind
    volumes) that ``compose down`` missed — e.g. orphans from a prior project/state (a service no
    longer in the current compose, like a removed subsystem). Best-effort; no sudo (docker-group).

    Filters by the fixed ``agmind`` compose-project LABEL, not a `name=agmind*` substring
    (mirrors ``ops_cmd._running_compose_services``'s "don't trust a name substring" rationale).
    A name-substring filter would also force-remove a FOREIGN compose project that merely
    shares the ``agmind-`` name prefix — e.g. the live ``agmind-dify-cf`` Cloudflare tunnel,
    a separate compose deployment (P0.10 / D-02). The label filter is a fixed catalog project
    name (NOT derived per-install-dir): the sweep targets orphans left behind by ANY prior
    ``agmind`` compose project, which all carry this label."""
    cids = _docker_ids(["docker", "ps", "-aq", "--filter", _AGMIND_COMPOSE_PROJECT_LABEL_FILTER])
    if cids:
        _run_cmd(["docker", "rm", "-f", *cids])
    nids = _docker_ids(
        ["docker", "network", "ls", "-q", "--filter", _AGMIND_COMPOSE_PROJECT_LABEL_FILTER]
    )
    if nids:
        _run_cmd(["docker", "network", "rm", *nids])
    if volumes:
        vids = _docker_ids(
            ["docker", "volume", "ls", "-q", "--filter", _AGMIND_COMPOSE_PROJECT_LABEL_FILTER]
        )
        if vids:
            _run_cmd(["docker", "volume", "rm", *vids])


def cmd_uninstall(
    *,
    data: bool = False,
    yes: bool = False,
    force: bool = False,
    install_dir: Path = Path("/opt/agmind"),
) -> int:
    """Tear down the AGmind deployment.

    Always: ``docker compose down --remove-orphans`` + remove the install dir + the global
    ``/usr/local/bin/agmind`` shim. With ``data``: ALSO ``--volumes`` and wipe ``/var/lib/agmind``
    (all stack data) + ``/etc/agmind`` (config) — destructive, for a clean reinstall.

    Before anything else (P0.10 / D-02): refuse if ``install_dir`` resolves to a forbidden
    ancestor (``/``, ``/home``, ``/etc``, ``/var``, ``/opt``, ``$HOME``) or lacks the
    ``docker-compose.yml`` install sentinel — a routine uninstall must not be able to
    `rm -rf` an unintended target. Both checks are bypassable only via an explicit ``--force``.
    """
    install_dir = install_dir.resolve()
    if not force:
        reason = _forbidden_uninstall_target(install_dir)
        if reason is not None:
            typer.echo(f"ERROR: refusing to uninstall {install_dir}: {reason}", err=True)
            typer.echo(
                "Pass --force to bypass (only if --install-dir is certainly correct).", err=True
            )
            return 1
        if not (install_dir / "docker-compose.yml").exists():
            typer.echo(
                f"ERROR: refusing to uninstall {install_dir}: no docker-compose.yml found here "
                "(missing install sentinel — this does not look like an agmind install dir).",
                err=True,
            )
            typer.echo(
                "Pass --force to bypass (only if --install-dir is certainly correct).", err=True
            )
            return 1

    data_dir = Path("/var/lib/agmind")
    config_dir = Path("/etc/agmind")
    shim = Path("/usr/local/bin/agmind")
    # User-state holds the SAVED wizard selection (setup-state.json). A stale entry here — e.g. a
    # service later removed from the catalog — makes the next install abort with "Unknown services
    # requested", so a --data wipe MUST clear it for a truly clean reinstall. live 2026-06-07.
    user_state = [Path.home() / ".local/share/agmind", Path.home() / ".config/agmind"]
    compose_file = install_dir / "docker-compose.yml"
    removed = [install_dir, shim] + ([data_dir, config_dir, *user_state] if data else [])

    typer.echo("agmind uninstall will:")
    typer.echo(f"  • docker compose down --remove-orphans{' --volumes' if data else ''}")
    for p in removed:
        typer.echo(f"  • remove {p}")
    if data:
        typer.echo(
            "  ⚠️  --data PERMANENTLY DELETES all stack data (postgres/mysql/mongo/milvus/minio/"
            "qdrant/elasticsearch/grafana/phoenix/…). This cannot be undone."
        )
    else:
        typer.echo("  (data in /var/lib/agmind is KEPT — pass --data for a full wipe)")

    if not yes and not typer.confirm("Continue?", default=False):
        typer.echo("aborted.", err=True)
        return 1

    # compose down (sudo: the install dir + its .env are root-owned)
    if compose_file.exists():
        down = ["sudo", "docker", "compose", "-f", str(compose_file), "down", "--remove-orphans"]
        if data:
            down.append("--volumes")
        if _run_cmd(down) != 0:
            typer.echo("  ! compose down reported an error — continuing teardown anyway", err=True)
    else:
        typer.echo(f"  (no {compose_file} — skipping compose down)")

    # Force-remove any agmind-* containers / networks compose-down missed (orphans from a prior
    # project/state — e.g. a removed subsystem still running). This is why a bare reinstall could
    # otherwise leave wedged leftovers. live 2026-06-07.
    _sweep_agmind_runtime(volumes=data)

    # remove paths (best-effort, sudo for root-owned trees)
    for p in removed:
        _run_cmd(["sudo", "rm", "-rf", str(p)])

    typer.echo("✓ uninstall complete. Reinstall a clean stack with:  make setup")
    return 0


def register(app: typer.Typer) -> None:
    """Attach the ``setup``, ``install`` and ``uninstall`` commands to ``app``."""

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
            help="Public domain for Traefik TLS (skip prompt if set).",
        ),
        cf_token_file: Path | None = typer.Option(
            None,
            "--cf-token-file",
            help="File with a Cloudflare API token (skip prompt if set, chmod 600).",
        ),
        model_id: str = typer.Option(
            "",
            "--model-id",
            help="Curated model id (see `agmind install --list-models`) or 'custom'.",
        ),
        model_repo: str = typer.Option("", "--model-repo", help="HF repo for a custom LLM."),
        model_file: str = typer.Option("", "--model-file", help="GGUF filename for the LLM."),
        ctx_size: int = typer.Option(0, "--ctx-size", help="Context size override."),
        kv_cache: str = typer.Option("", "--kv-cache", help="KV cache quant override."),
        list_models: bool = typer.Option(False, "--list-models", help="Print model catalog."),
        lang: str = typer.Option("", "--lang", help="UI language (en|ru)."),
        legacy_wizard: bool = typer.Option(False, "--legacy-wizard", help="Use legacy wizard."),
        no_tui: bool = typer.Option(False, "--no-tui", help="Headless install for CI."),
        dry_run: bool = typer.Option(
            False,
            "--dry-run",
            help="Wizard/config only, no bootstrap/pull/deploy.",
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
            help="Public domain for Traefik TLS (skip prompt if set).",
        ),
        cf_token_file: Path | None = typer.Option(
            None,
            "--cf-token-file",
            help="File with a Cloudflare API token (skip prompt if set, chmod 600).",
        ),
        model_id: str = typer.Option(
            "",
            "--model-id",
            help="Curated model id (see `agmind install --list-models`) or 'custom'.",
        ),
        model_repo: str = typer.Option(
            "",
            "--model-repo",
            help="HF repo (for custom). Overrides the curated selection.",
        ),
        model_file: str = typer.Option(
            "",
            "--model-file",
            help="GGUF filename. Empty + non-custom id → resolved from the catalog.",
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
            help="Print the curated model catalog and exit.",
        ),
        lang: str = typer.Option(
            "",
            "--lang",
            help="UI language (en|ru). Default — auto-detect via LANG env.",
        ),
        legacy_wizard: bool = typer.Option(
            False,
            "--legacy-wizard",
            help="Force the legacy single-screen wizard (default is multi-step).",
        ),
        no_tui: bool = typer.Option(
            False,
            "--no-tui",
            help="CLI-only run without the Textual UI (for CI / headless).",
        ),
        dry_run: bool = typer.Option(
            False,
            "--dry-run",
            help="Preflight + wizard only, no bootstrap/pull/deploy.",
        ),
        from_state: Path | None = typer.Option(
            None,
            "--from-state",
            help="Load saved setup state JSON before opening wizard.",
        ),
    ) -> None:
        """End-to-end install (wizard → bootstrap → pull → deploy).

        In the TUI the sudo password is collected via a hidden input inside the wizard and is
        only needed for the bootstrap step. In `--no-tui` mode it is requested via a normal
        terminal prompt.
        """
        import getpass

        from agmind.cli.tui.setup_wizard import (
            STATE_PATH,
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
            typer.echo("\n★ = measured on Strix Halo")
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
        else:
            # Interactive re-run (no explicit --from-state): pre-select the
            # previously-deployed services/profiles so adding or replacing a component
            # does not silently drop the running stack via `compose up --remove-orphans`.
            # Secrets are not stored in state → re-prompted; CLI flags still win.
            from agmind.cli.install_state import load_prior_setup_state

            prior = load_prior_setup_state(STATE_PATH)
            if prior is not None:
                initial.services = prior.services
                initial.profiles = prior.profiles
                if not domain and prior.domain:
                    initial.domain = prior.domain
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
            if wizard_state is None and not dry_run:
                # A dry-run TUI run ends on the SummaryScreen whose quit returns None —
                # that is a normal completion, not a cancel. Only treat None as cancelled
                # for a real (install) run.
                typer.echo("aborted: wizard cancelled", err=True)
                raise typer.Exit(code=1)
            if dry_run:
                typer.echo("dry-run: wizard complete (no bootstrap/pull/deploy)")
                raise typer.Exit(code=0)
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
                validation_errors, wizard_state.domain = _headless_validation_errors(
                    wizard_state.services,
                    wizard_state.profiles,
                    wizard_state.domain,
                    wizard_state.cf_api_token,
                )
                if validation_errors:
                    for error in validation_errors:
                        typer.echo(f"ERROR: {error}", err=True)
                    raise typer.Exit(code=2)
                if _sudo_nopasswd_available():
                    wizard_state.sudo_password = ""
                else:
                    try:
                        sudo_pw = getpass.getpass("Sudo password (for apt/usermod/mkdir): ")
                    except (EOFError, KeyboardInterrupt):
                        typer.echo("\naborted: no sudo password entered", err=True)
                        raise typer.Exit(code=2)
                    if not sudo_pw:
                        typer.echo("aborted: empty sudo password", err=True)
                        raise typer.Exit(code=2)
                    wizard_state.sudo_password = sudo_pw

        # 3. Resolve final model repo/file (curated or custom) — для каждого role.
        # If no CLI LLM override is supplied, respect wizard/from-state "skip"
        # choices before building the install config.
        if not model_repo and not model_file:
            wizard_state.normalize_model_fields_and_services(drop_unselected_model_files=True)
        resolved_repo, resolved_file = wizard_state.resolve_model_repo_file()
        final_repo: str | None = resolved_repo
        final_file: str | None = resolved_file
        # CLI flags override wizard LLM values if provided (kept legacy semantics).
        if model_repo:
            final_repo = model_repo
        if model_file:
            final_file = model_file
        resolved_embed_repo, resolved_embed_file = wizard_state.resolve_embed_repo_file()
        embed_repo: str | None = resolved_embed_repo
        embed_file: str | None = resolved_embed_file
        resolved_rerank_repo, resolved_rerank_file = wizard_state.resolve_rerank_repo_file()
        rerank_repo: str | None = resolved_rerank_repo
        rerank_file: str | None = resolved_rerank_file
        selected_services = set(wizard_state.services)
        if "llama-llm" not in selected_services:
            final_repo = None
            final_file = None
        if "llama-embed" not in selected_services:
            embed_repo = None
            embed_file = None
        if "llama-rerank" not in selected_services:
            rerank_repo = None
            rerank_file = None

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
            typer.echo("dry-run: stopping after wizard")
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
            if result.success:
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

    @app.command("uninstall")
    def uninstall(
        data: bool = typer.Option(
            False,
            "--data",
            help="ALSO permanently delete all stack DATA (/var/lib/agmind), config "
            "(/etc/agmind) and named volumes. Destructive — use for a clean reinstall.",
        ),
        yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
        force: bool = typer.Option(
            False,
            "--force",
            help="Bypass the install-dir safety check (forbidden ancestors / missing "
            "sentinel). Use only when --install-dir is certainly correct.",
        ),
        install_dir: Path = typer.Option(
            Path("/opt/agmind"), "--install-dir", help="Deployment directory to tear down."
        ),
    ) -> None:
        """Tear down the AGmind stack (containers + networks + install dir + global shim).

        Keeps your data in /var/lib/agmind by default; pass --data for a full wipe before a
        clean reinstall (`make setup`). Needs sudo (the install dir is root-owned).
        """
        raise typer.Exit(
            code=cmd_uninstall(data=data, yes=yes, force=force, install_dir=install_dir)
        )
