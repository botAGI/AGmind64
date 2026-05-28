"""`agmind ci` commands for GitHub Actions runner visibility."""

from __future__ import annotations

import json
import sys

import typer

from agmind.ci.monitor import DEFAULT_RUN_LIMIT, CIMonitorReport, collect_ci_status


def cmd_status(
    *,
    repository: str | None = None,
    run_limit: int = DEFAULT_RUN_LIMIT,
    as_json: bool = False,
) -> int:
    """Show GitHub Actions queue and self-hosted runner state."""
    report = collect_ci_status(repository=repository, run_limit=run_limit)

    if as_json:
        print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
        return 0 if report.repository else 1

    if report.warnings and not report.runs and not report.runners:
        for warning in report.warnings:
            print(warning, file=sys.stderr)
        return 1

    print(_format_report(report))
    return 0


def _format_report(report: CIMonitorReport) -> str:
    lines: list[str] = [f"Repository: {report.repository or '-'}"]
    if report.warnings:
        lines.append("Warnings:")
        for warning in report.warnings:
            lines.append(f"  - {warning}")

    lines.append(f"Runs: {_summary(report.run_summary)}")
    for run in report.runs:
        conclusion = f"/{run.conclusion}" if run.conclusion else ""
        lines.append(
            f"  - #{run.database_id} {run.workflow:<24} {run.status}{conclusion:<14} "
            f"{run.branch or '-'} {run.title}"
        )

    lines.append(f"Runners: {_summary(report.runner_summary)}")
    for runner in report.runners:
        state = "busy" if runner.busy else "idle"
        labels = ",".join(runner.labels) or "-"
        lines.append(f"  - {runner.name:<24} {runner.status:<8} {state:<4} {labels}")
    return "\n".join(lines)


def _summary(summary: dict[str, int]) -> str:
    if not summary:
        return "none"
    return ", ".join(f"{key}={value}" for key, value in summary.items())


def register(app: typer.Typer) -> None:
    """Attach the ``ci`` command group to ``app``."""

    # ---- ci subcommand group (GitHub Actions/self-hosted runner visibility) ----
    ci_app = typer.Typer(
        name="ci",
        help="Inspect GitHub Actions runs and self-hosted runners",
        no_args_is_help=True,
    )
    app.add_typer(ci_app)

    @ci_app.command("status")
    def ci_status(
        repository: str | None = typer.Option(
            None,
            "--repo",
            help="GitHub repository slug owner/name (default: git remote or AGMIND_GITHUB_REPO)",
        ),
        run_limit: int = typer.Option(10, "--limit", "-n", min=1, max=100),
        as_json: bool = typer.Option(False, "--json", help="JSON output"),
    ) -> None:
        """Show GitHub Actions queue and self-hosted runner state."""
        raise typer.Exit(
            code=cmd_status(repository=repository, run_limit=run_limit, as_json=as_json)
        )


__all__ = ["cmd_status", "register"]
