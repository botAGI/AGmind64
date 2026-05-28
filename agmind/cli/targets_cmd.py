"""`agmind targets` commands for deployment target contracts."""

from __future__ import annotations

import json
import sys
from collections import Counter
from typing import Any

import typer

from agmind.deploy import DeploymentTarget, load_deploy_targets
from agmind.deploy.target_checks import (
    format_deployment_check_report,
    validate_deploy_target_report,
    validate_deploy_targets,
)


def _target_payload(target: DeploymentTarget) -> dict[str, Any]:
    return {
        "id": target.id,
        "name": target.name,
        "status": target.status,
        "summary": target.summary,
        "runtime": target.runtime.model_dump(mode="json"),
        "provisioner": target.provisioner.model_dump(mode="json"),
        "configurator": target.configurator.model_dump(mode="json"),
        "storage_profile": target.storage_profile,
        "secrets_profile": target.secrets_profile,
        "verification": target.verification.model_dump(mode="json"),
    }


def _target_errors(target_id: str, errors: list[str]) -> list[str]:
    prefix = f"{target_id}:"
    return [error for error in errors if error.startswith(prefix)]


def cmd_list(as_json: bool = False) -> int:
    """List deployment targets."""
    targets = load_deploy_targets()
    status_counts = Counter(target.status for target in targets.values())

    if as_json:
        payload = {
            "summary": dict(sorted(status_counts.items())),
            "targets": [_target_payload(target) for target in targets.values()],
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    if not targets:
        print("No deployment targets found.", file=sys.stderr)
        return 1

    width = max(len(target_id) for target_id in targets) + 2
    print(f"{'ID':<{width}} {'STATUS':<13} {'RUNTIME':<12} {'PROVISIONER':<18} STORAGE")
    print("-" * (width + 66))
    for target in targets.values():
        print(
            f"{target.id:<{width}} {target.status:<13} {target.runtime.kind:<12} "
            f"{target.provisioner.kind:<18} {target.storage_profile}"
        )
    print()
    print(
        "Summary: "
        + ", ".join(f"{status}={count}" for status, count in sorted(status_counts.items()))
    )
    return 0


def cmd_status(name: str, as_json: bool = False) -> int:
    """Show one deployment target."""
    targets = load_deploy_targets()
    target = targets.get(name)
    if target is None:
        print(f"Deployment target '{name}' not found. Run `agmind targets list`.", file=sys.stderr)
        return 1

    errors = _target_errors(target.id, validate_deploy_targets(targets))
    payload = _target_payload(target)
    payload["validation_errors"] = errors
    if as_json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    print(f"ID: {target.id}")
    print(f"Name: {target.name}")
    print(f"Status: {target.status}")
    print(f"Runtime: {target.runtime.kind} ({target.runtime.renderer})")
    print(f"Profiles: {', '.join(target.runtime.profiles) or '-'}")
    print(f"Provisioner: {target.provisioner.kind}")
    if target.provisioner.module:
        print(f"Module: {target.provisioner.module}")
    print(f"Configurator: {target.configurator.kind}")
    print(f"Inventory source: {target.configurator.inventory_source or '-'}")
    print(f"Storage: {target.storage_profile}")
    print(f"Secrets: {target.secrets_profile}")
    print(f"Summary: {target.summary}")
    if target.verification.commands:
        print("Verification:")
        for command in target.verification.commands:
            print(f"  - {command}")
    if errors:
        print("Validation errors:")
        for error in errors:
            print(f"  - {error}")
    return 0


def cmd_validate(as_json: bool = False) -> int:
    """Validate deployment target catalog and local repository references."""
    targets = load_deploy_targets()
    report = validate_deploy_target_report(targets)
    if as_json:
        print(json.dumps(report.to_json(), indent=2, ensure_ascii=False))
        return 0 if report.ok else 1

    if not report.ok:
        print(
            format_deployment_check_report(report, ok_label="Deployment target"),
            file=sys.stderr,
        )
        return 1

    print(format_deployment_check_report(report, ok_label="deployment targets"))
    return 0


def register(app: typer.Typer) -> None:
    """Attach the ``targets`` command group to ``app``."""

    # ---- targets subcommand group (universal deployment lanes) ----
    targets_app = typer.Typer(
        name="targets",
        help="Inspect deployment target contracts",
        no_args_is_help=True,
    )
    app.add_typer(targets_app)

    @targets_app.command("list")
    def targets_list(
        as_json: bool = typer.Option(False, "--json", help="JSON output"),
    ) -> None:
        """List deployment targets."""
        raise typer.Exit(code=cmd_list(as_json=as_json))

    @targets_app.command("status")
    def targets_status(
        name: str = typer.Argument(..., help="Deployment target id"),
        as_json: bool = typer.Option(False, "--json", help="JSON output"),
    ) -> None:
        """Show one deployment target."""
        raise typer.Exit(code=cmd_status(name=name, as_json=as_json))

    @targets_app.command("validate")
    def targets_validate(
        as_json: bool = typer.Option(False, "--json", help="JSON output"),
    ) -> None:
        """Validate deployment target contracts."""
        raise typer.Exit(code=cmd_validate(as_json=as_json))
