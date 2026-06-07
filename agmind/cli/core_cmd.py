"""Top-level `agmind` info commands: doctor / status / version / audit.

Registration only — heavy diagnostics/compute/TUI imports stay lazy inside the
command bodies so that building the app (and running unrelated commands) does
not import them.
"""

from __future__ import annotations

import json
from pathlib import Path

import typer

from agmind import __version__
from agmind.core.paths import data_root


def _deploy_summary(install_dir: Path) -> dict[str, object]:
    """Non-interactive deployment health — reuses the --tui dashboard's compose snapshot
    (query_compose_state never raises). live-audit 2026-06-07 UI-4."""
    from agmind.cli.tui.status_dashboard import query_compose_state

    snap = query_compose_state(install_dir)
    problems = sorted(
        s.service
        for s in snap.services
        if s.health == "unhealthy" or s.state in ("exited", "restarting")
    )
    out: dict[str, object] = {
        "error": snap.error,
        "total": snap.total,
        "running": snap.running,
        "healthy": snap.healthy,
        "unhealthy": snap.unhealthy,
        "problems": problems,
    }
    try:
        from agmind.cli.access_cmd import _llm_disabled_consumers

        disabled = _llm_disabled_consumers(install_dir)
    except Exception:  # never let the optional LLM hint break `status`
        disabled = []
    if disabled:
        out["llm_disabled_consumers"] = disabled
    return out


def _print_deploy_summary(summary: dict[str, object]) -> None:
    if summary.get("error"):
        typer.echo(f"Deploy:    {summary['error']}")
    else:
        typer.echo(
            f"Deploy:    {summary['running']}/{summary['total']} running, "
            f"{summary['healthy']} healthy, {summary['unhealthy']} unhealthy"
        )
        problems = summary.get("problems") or []
        if problems:
            typer.echo(f"  problems: {', '.join(problems)}")  # type: ignore[arg-type]
    disabled = summary.get("llm_disabled_consumers") or []
    if disabled:
        typer.echo(
            "  ⚠ no LLM deployed (model skipped) — chat/generation disabled for: "
            + ", ".join(disabled)  # type: ignore[arg-type]
        )


def register(app: typer.Typer) -> None:
    """Attach the core info commands to ``app``."""

    @app.command()
    def doctor(
        as_json: bool = typer.Option(False, "--json", help="JSON output"),
    ) -> None:
        """Run preflight diagnostics."""
        from agmind.diagnostics.doctor import format_doctor_report, run_preflight

        report = run_preflight()
        typer.echo(format_doctor_report(report, as_json=as_json))
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
        deploy: bool = typer.Option(
            False,
            "--deploy",
            help="Also print a non-interactive deployment health summary (services running/healthy).",
        ),
        install_dir: Path = typer.Option(
            Path("/opt/agmind"),
            "--install-dir",
            help="Deployment dir (for --tui / --deploy)",
        ),
        refresh: float = typer.Option(
            5.0, "--refresh", help="Refresh interval seconds (only for --tui)"
        ),
    ) -> None:
        """Show selected backend + device info, или live dashboard с --tui.

        ``--deploy`` adds a plain-text/--json deployment picture (services up/healthy + problems
        + an LLM-disabled warning) readable over SSH or pipe — the live health was previously
        reachable ONLY via the interactive --tui (live-audit 2026-06-07 UI-4).
        """
        if tui:
            from agmind.cli.tui.status_dashboard import run_dashboard

            run_dashboard(install_dir=install_dir, refresh_interval=refresh)
            return

        from agmind.compute import get_backend, list_available_backends

        backend = get_backend()
        info = backend.device_info()
        payload: dict[str, object] = {
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
        if deploy:
            payload["deploy"] = _deploy_summary(install_dir)
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
            if deploy:
                _print_deploy_summary(payload["deploy"])  # type: ignore[arg-type]

    @app.command()
    def version() -> None:
        """Print agmind version."""
        typer.echo(f"agmind {__version__}")

    @app.command()
    def audit() -> None:
        """Run audit_forbidden.py (forbid legacy patterns in main tree)."""  # audit: allow rule-self-reference
        import subprocess
        import sys

        script = data_root() / "scripts" / "checks" / "audit_forbidden.py"
        result = subprocess.run(
            [sys.executable, str(script), "--fail"],
            check=False,
        )
        raise typer.Exit(code=result.returncode)
