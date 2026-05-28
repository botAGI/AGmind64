"""`agmind governance` commands for aggregate M7 consistency checks."""

from __future__ import annotations

import json

import typer

from agmind.governance import format_governance_report, run_governance_checks


def cmd_validate(as_json: bool = False) -> int:
    """Run all governance gates and print an aggregate report."""
    report = run_governance_checks(structured=True)
    if as_json:
        print(json.dumps(report.to_json(), indent=2, ensure_ascii=False))
    else:
        print(format_governance_report(report))
    return 0 if report.ok else 1


def register(app: typer.Typer) -> None:
    """Attach the ``governance`` command group to ``app``."""

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
        raise typer.Exit(code=cmd_validate(as_json=as_json))
