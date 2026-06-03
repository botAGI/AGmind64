"""`agmind security audit` — read-only posture scan of the deployed artifacts.

Registration only; the scanner import stays lazy in the command body.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import typer


def register(app: typer.Typer) -> None:
    """Attach the ``security`` command group to ``app``."""

    security_app = typer.Typer(
        name="security",
        help="Security posture tooling.",
        no_args_is_help=True,
    )
    app.add_typer(security_app)

    @security_app.command("audit")
    def audit(
        install_dir: Path = typer.Option(
            Path("/opt/agmind"), "--install-dir", help="Deployed install dir to audit."
        ),
        as_json: bool = typer.Option(False, "--json", help="JSON output."),
        block: str = typer.Option(
            "high", "--block", help="Exit non-zero at this severity or above."
        ),
        live: bool = typer.Option(
            False, "--live", help="Also inspect running containers (needs a Docker daemon)."
        ),
    ) -> None:
        """Scan the deployed compose/.env/secret-file perms for posture issues.

        Exit 0 = clean below --block, 1 = a finding at/above --block, 2 = not
        installed or bad --block. Secret values are never printed.
        """
        from agmind.security.audit import (
            SEVERITY_LEVELS,
            audit_install,
            gate_exit,
            max_severity,
        )

        if block not in SEVERITY_LEVELS:
            print(
                f"agmind security audit: invalid --block '{block}' "
                f"(expected one of {', '.join(SEVERITY_LEVELS)})",
                file=sys.stderr,
            )
            raise typer.Exit(code=2)

        findings, installed = audit_install(install_dir, live=live)
        if not installed:
            print(
                f"agmind security audit: no deployment found at {install_dir} "
                "(no docker-compose.yml)",
                file=sys.stderr,
            )
            raise typer.Exit(code=2)

        exit_code = gate_exit(findings, block=block)
        if as_json:
            counts: dict[str, int] = {}
            for f in findings:
                counts[f.severity] = counts.get(f.severity, 0) + 1
            payload = {
                "install_dir": str(install_dir),
                "block": block,
                "findings": [f.to_dict() for f in findings],
                "summary": {
                    "counts_by_severity": counts,
                    "max_severity": max_severity(findings),
                    "exit": exit_code,
                },
            }
            typer.echo(json.dumps(payload, indent=2, ensure_ascii=False))
        else:
            if not findings:
                typer.echo(f"security audit: {install_dir} — no findings.")
            else:
                typer.echo(f"security audit: {install_dir} — {len(findings)} finding(s):")
                for f in findings:
                    typer.echo(f"  [{f.severity:<8}] {f.check:<14} {f.target} — {f.detail}")
                    if f.fix:
                        typer.echo(f"             fix: {f.fix}")
            typer.echo(f"max severity: {max_severity(findings)}  (block >= {block})")

        raise typer.Exit(code=exit_code)
