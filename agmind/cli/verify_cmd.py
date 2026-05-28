"""`agmind verify` commands."""

from __future__ import annotations

import json
from pathlib import Path

import typer

from agmind.install.verify import (
    DEFAULT_SCENARIOS,
    InstallVerifyScenario,
    format_install_verify_report,
    scenario_names,
    verify_install,
)


def cmd_install(
    *,
    domain: str = "lab.example.com",
    scenarios: list[str] | None = None,
    as_json: bool = False,
    skip_ansible: bool = False,
    skip_compose: bool = False,
    skip_galaxy: bool = False,
    timeout_seconds: int = 240,
    work_dir: Path | None = None,
) -> int:
    """Run the non-destructive fresh-install verification gate."""
    selected_scenarios = _select_scenarios(scenarios or [])
    if selected_scenarios is None:
        known = ", ".join(scenario_names())
        print(f"ERROR: unknown scenario. Known scenarios: {known}")
        return 2

    report = verify_install(
        domain=domain,
        scenarios=selected_scenarios,
        include_ansible=not skip_ansible,
        include_compose=not skip_compose,
        install_collections=not skip_galaxy,
        timeout_seconds=timeout_seconds,
        work_dir=work_dir,
    )
    if as_json:
        print(json.dumps(report.to_json(), indent=2, ensure_ascii=False))
    else:
        print(format_install_verify_report(report))
    return 0 if report.ok else 1


def _select_scenarios(names: list[str]) -> tuple[InstallVerifyScenario, ...] | None:
    if not names:
        return DEFAULT_SCENARIOS

    by_name = {scenario.name: scenario for scenario in DEFAULT_SCENARIOS}
    missing = [name for name in names if name not in by_name]
    if missing:
        return None
    return tuple(by_name[name] for name in names)


def register(app: typer.Typer) -> None:
    """Attach the ``verify`` command group to ``app``."""

    # ---- verify subcommand group (fresh-install/product gates) ----
    verify_app = typer.Typer(
        name="verify",
        help="Run non-destructive product readiness gates.",
        no_args_is_help=True,
    )
    app.add_typer(verify_app)

    @verify_app.command("install")
    def verify_install_cmd(
        domain: str = typer.Option(
            "lab.example.com",
            "--domain",
            help="Domain used for render/config validation.",
        ),
        scenario: list[str] | None = typer.Option(
            None,
            "--scenario",
            "-s",
            help="Fresh-install scenario to run; can be repeated.",
        ),
        as_json: bool = typer.Option(False, "--json", help="JSON output"),
        skip_ansible: bool = typer.Option(
            False,
            "--skip-ansible",
            help="Skip ansible-galaxy and ansible-playbook syntax checks.",
        ),
        skip_compose: bool = typer.Option(
            False,
            "--skip-compose",
            help="Skip docker compose config validation.",
        ),
        skip_galaxy: bool = typer.Option(
            False,
            "--skip-galaxy",
            help="Skip ansible-galaxy collection install before syntax check.",
        ),
        timeout_seconds: int = typer.Option(
            240,
            "--timeout",
            min=1,
            help="Per-command timeout in seconds.",
        ),
        work_dir: Path | None = typer.Option(
            None,
            "--work-dir",
            help="Keep verification artifacts under this directory.",
        ),
    ) -> None:
        """Prove `agmind setup` inputs can render/deploy cleanly without applying."""
        raise typer.Exit(
            code=cmd_install(
                domain=domain,
                scenarios=scenario,
                as_json=as_json,
                skip_ansible=skip_ansible,
                skip_compose=skip_compose,
                skip_galaxy=skip_galaxy,
                timeout_seconds=timeout_seconds,
                work_dir=work_dir,
            )
        )
