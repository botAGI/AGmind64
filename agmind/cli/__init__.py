"""AGmind CLI — entry point typer app.

Команды:
    agmind doctor      — preflight diagnostics
    agmind status      — backend / engine info
    agmind version     — pkg version + git rev
    agmind audit       — wrapper над scripts/checks/audit_forbidden.py

Установка typer/click — soft dependency. Если typer не установлен,
`app()` падает с понятной инструкцией.

Каждая группа команд регистрируется своим модулем `agmind.cli.<group>_cmd`
через `register(app)`; `_make_app` только собирает app и делегирует.
"""

from __future__ import annotations

import sys

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

    @app.callback()
    def _global_options(
        verbose: bool = typer.Option(False, "--verbose", "-v", help="Debug logging"),
    ) -> None:
        setup_logging("DEBUG" if verbose else "INFO")

    # Per-group registration. Imported here (not at module top) so that
    # `import agmind.cli` stays cheap and does not require typer. Call order
    # determines the order commands/groups appear in `agmind --help`.
    from agmind.cli import (
        access_cmd,
        ci_cmd,
        cluster_cmd,
        core_cmd,
        deploy_cmd,
        docling_cmd,
        estimate_cmd,
        governance_cmd,
        install_cmd,
        migrate_cmd,
        models_cmd,
        ops_cmd,
        render_cmd,
        service_cmd,
        targets_cmd,
        tools_cmd,
        upgrade_cmd,
        verify_cmd,
    )

    core_cmd.register(app)
    deploy_cmd.register(app)
    verify_cmd.register(app)
    install_cmd.register(app)
    service_cmd.register(app)
    tools_cmd.register(app)
    ci_cmd.register(app)
    targets_cmd.register(app)
    governance_cmd.register(app)
    render_cmd.register(app)
    cluster_cmd.register(app)
    estimate_cmd.register(app)
    docling_cmd.register(app)
    upgrade_cmd.register(app)
    models_cmd.register(app)
    ops_cmd.register(app)
    migrate_cmd.register(app)
    access_cmd.register(app)
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
