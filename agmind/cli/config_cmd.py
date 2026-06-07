"""`agmind config` commands — runtime validation of a LIVE deployment."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import typer

from agmind.config.validation import ConfigValidationReport, validate_config

_DEFAULT_INSTALL_DIR = Path("/opt/agmind")


# Findings sharing an id + fix_cmd are collapsed into ONE summarized line listing the affected
# service names, so a real multi-service condition is not a wall of N near-identical rows
# (live-audit 2026-06-08 UX-4 / M2).
_COLLAPSIBLE_IDS = frozenset({"drift-not-running", "drift-orphan", "drift-digest-undeterminable"})


def _format_report(report: ConfigValidationReport) -> str:
    """Render a human-readable findings table (never echoes a secret value)."""
    lines: list[str] = []
    n_err = len(report.by_severity("error"))
    n_warn = len(report.by_severity("warning"))
    counts = f"{n_err} error(s), {n_warn} warning(s), {len(report.by_severity('info'))} info"
    if not report.findings:
        return f"config OK: {counts}"

    # Don't print an unqualified "OK" header when warnings (or errors) exist — it contradicts
    # the warning wall below it (live-audit 2026-06-08 UX-4).
    if n_err:
        header = "config validation FAILED"
    elif n_warn:
        header = "config: warnings present"
    else:
        header = "config OK"
    lines.append(f"{header}: {counts}")
    width = max((len(f.severity) for f in report.findings), default=7)

    collapsed: dict[tuple[str, str, str], list[str]] = {}
    for finding in report.findings:
        if finding.id in _COLLAPSIBLE_IDS:
            key = (finding.severity, finding.id, finding.fix_cmd)
            collapsed.setdefault(key, []).append(finding.evidence or "?")
            continue
        lines.append(f"  [{finding.severity:<{width}}] {finding.id}: {finding.message}")
        if finding.evidence:
            lines.append(f"      evidence: {finding.evidence}")
        if finding.fixable and finding.fix_cmd:
            lines.append(f"      fix: {finding.fix_cmd}")

    for (severity, fid, fix_cmd), names in collapsed.items():
        plural = "s" if len(names) != 1 else ""
        lines.append(f"  [{severity:<{width}}] {fid}: {len(names)} service{plural} affected")
        lines.append(f"      services: {', '.join(sorted(names))}")
        if fix_cmd:
            lines.append(f"      fix: {fix_cmd}")
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
