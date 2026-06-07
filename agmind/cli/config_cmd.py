"""`agmind config` commands — runtime validation of a LIVE deployment."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import typer

from agmind.config.validation import ConfigValidationReport, validate_config

_DEFAULT_INSTALL_DIR = Path("/opt/agmind")


def _format_report(report: ConfigValidationReport) -> str:
    """Render a human-readable findings table (never echoes a secret value)."""
    lines: list[str] = []
    counts = (
        f"{len(report.by_severity('error'))} error(s), "
        f"{len(report.by_severity('warning'))} warning(s), "
        f"{len(report.by_severity('info'))} info"
    )
    if report.ok and not report.findings:
        return f"config OK: {counts}"

    header = "config OK" if report.ok else "config validation FAILED"
    lines.append(f"{header}: {counts}")
    width = max((len(f.severity) for f in report.findings), default=7)
    for finding in report.findings:
        lines.append(f"  [{finding.severity:<{width}}] {finding.id}: {finding.message}")
        if finding.evidence:
            lines.append(f"      evidence: {finding.evidence}")
        if finding.fixable and finding.fix_cmd:
            lines.append(f"      fix: {finding.fix_cmd}")
    return "\n".join(lines)


def cmd_validate(
    install_dir: Path = _DEFAULT_INSTALL_DIR,
    *,
    as_json: bool = False,
    strict: bool = False,
    check_drift: bool = True,
) -> int:
    """Validate the live deployment under ``install_dir``. Returns 0/1 (never 2)."""
    report = validate_config(install_dir, check_drift=check_drift, strict=strict)

    if as_json:
        print(json.dumps(report.to_payload(), indent=2, ensure_ascii=False))
        return 0 if report.ok else 1

    rendered = _format_report(report)
    if report.ok:
        print(rendered)
        return 0
    print(rendered, file=sys.stderr)
    return 1


def register(app: typer.Typer) -> None:
    """Attach the ``config`` command group to ``app``."""

    config_app = typer.Typer(
        name="config",
        help="Validate the live deployment configuration",
        no_args_is_help=True,
    )
    app.add_typer(config_app)

    @config_app.command("validate")
    def config_validate(
        as_json: bool = typer.Option(False, "--json", help="JSON output"),
        install_dir: Path = typer.Option(
            _DEFAULT_INSTALL_DIR,
            "--install-dir",
            help="Deployed install dir holding .env + docker-compose.yml",
        ),
        strict: bool = typer.Option(False, "--strict", help="Treat warnings as failures (exit 1)"),
        check_drift: bool = typer.Option(
            True,
            "--drift/--no-drift",
            help="Compare pinned vs running image digests (needs docker)",
        ),
    ) -> None:
        """Validate the live deployment configuration."""
        raise typer.Exit(
            code=cmd_validate(
                install_dir=install_dir,
                as_json=as_json,
                strict=strict,
                check_drift=check_drift,
            )
        )
