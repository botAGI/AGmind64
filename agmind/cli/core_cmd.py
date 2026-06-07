"""Top-level `agmind` info commands: doctor / status / version / audit.

Registration only — heavy diagnostics/compute/TUI imports stay lazy inside the
command bodies so that building the app (and running unrelated commands) does
not import them.
"""

from __future__ import annotations

import getpass
import json
from pathlib import Path
from typing import TYPE_CHECKING

import typer

from agmind import __version__
from agmind.core.paths import data_root

if TYPE_CHECKING:
    from agmind.config.validation import ConfigValidationReport
    from agmind.diagnostics.doctor import DoctorReport


def _run_preflight() -> DoctorReport:
    """Indirection seam (lazy import + monkeypatchable in tests)."""
    from agmind.diagnostics.doctor import run_preflight

    return run_preflight()


def _validate_config(install_dir: Path) -> ConfigValidationReport:
    """Indirection seam for the live ConfigValidationReport (monkeypatchable)."""
    from agmind.config.validation import validate_config

    return validate_config(install_dir, check_drift=True, strict=False)


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
        live: bool = typer.Option(
            False,
            "--live",
            help="Also run validate_config(install_dir) and merge its findings "
            "(env mode, secret-file perms, required vars, service drift).",
        ),
        fix: bool = typer.Option(
            False,
            "--fix",
            help="Implies --live. Auto-apply ONLY safe idempotent perm fixes "
            "(sudo chmod/chown); every other suggested fix is printed, never run.",
        ),
        bundle: Path = typer.Option(
            None,
            "--bundle",
            help="Write a sanitized support tar.gz (redacted .env, compose, docker "
            "ps/logs, validation + doctor reports). Output path must not exist.",
        ),
        install_dir: Path = typer.Option(
            Path("/opt/agmind"),
            "--install-dir",
            help="Deployed install dir (for --live / --fix / --bundle).",
        ),
        ask_sudo_password: bool = typer.Option(
            False,
            "--ask-sudo-password",
            help="Prompt for a sudo password (used by --fix perm operations).",
        ),
    ) -> None:
        """Run preflight diagnostics; optionally merge live config, auto-fix, bundle.

        Bare ``agmind doctor`` is unchanged (today's preflight). ``--live`` folds the
        live deployment's ConfigValidationReport in; ``--fix`` (implies ``--live``) runs
        only the permission-class idempotent fixes; ``--bundle PATH`` writes a redacted
        support archive.
        """
        from agmind.config.validation import ConfigFinding
        from agmind.diagnostics import live as live_mod
        from agmind.diagnostics.doctor import format_doctor_report

        if fix:
            live = True

        report = _run_preflight()
        live_findings: tuple[ConfigFinding, ...] = ()
        if live:
            live_report = _validate_config(install_dir)
            live_findings = tuple(live_report.findings)

        if fix:
            sudo_password = getpass.getpass("sudo password: ") if ask_sudo_password else None
            fix_result = live_mod.apply_safe_fixes(live_findings, sudo_password=sudo_password)
            if not as_json:
                for outcome in fix_result.fixed:
                    typer.echo(f"  ✓ fixed {outcome.finding.id}: {outcome.finding.fix_cmd}")
                for outcome in fix_result.failed:
                    typer.echo(
                        f"  ✗ FAILED to fix {outcome.finding.id}: {outcome.detail}", err=True
                    )
                if fix_result.unfixable:
                    typer.echo("Cannot auto-fix (manual action required):")
                    for finding in fix_result.unfixable:
                        typer.echo(f"  - {finding.id}: {finding.fix_cmd}")
            # Re-evaluate so the merged report reflects the post-fix state.
            live_report = _validate_config(install_dir)
            live_findings = tuple(live_report.findings)

        if live:
            report = live_mod.merge_live_findings(report, live_findings)

        if bundle is not None:
            try:
                result = live_mod.create_support_bundle(bundle, install_dir=install_dir)
            except Exception as exc:  # noqa: BLE001 — surface as exit 3, not a traceback
                typer.echo(f"ERROR: bundle creation failed: {exc}", err=True)
                raise typer.Exit(code=3) from exc
            typer.echo(
                f"support bundle written: {result.output_path} ({result.bytes_written} bytes)"
            )
            for issue in result.issues:
                typer.echo(f"  note: {issue}")

        typer.echo(format_doctor_report(report, as_json=as_json))
        if report.has_failures:
            raise typer.Exit(code=2 if not live else 1)
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
