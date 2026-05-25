#!/usr/bin/env python3
"""Validate component contracts, service ownership, and deploy conflicts."""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

PROFILE_SETS = (
    ("core",),
    ("core", "rag"),
    ("core", "observability"),
    ("core", "ragflow"),
)


def _issue(message: str) -> dict[str, str]:
    return {
        "severity": "error",
        "kind": "component_validation",
        "message": message,
    }


def _payload(
    *,
    ok: bool,
    contract_count: int,
    service_count: int,
    errors: list[str],
) -> dict[str, Any]:
    return {
        "ok": ok,
        "contract_count": contract_count,
        "service_count": service_count,
        "error_count": len(errors),
        "issues": [_issue(error) for error in errors],
    }


def main(argv: Sequence[str] | None = None) -> int:
    from agmind.components.checks import check_deploy_conflicts
    from agmind.components.registry import load_component_contracts
    from agmind.services.renderer import load_descriptors, select_services

    args = tuple(argv if argv is not None else sys.argv[1:])
    as_json = "--json" in args

    contracts = load_component_contracts()
    descriptors = load_descriptors()

    errors: list[str] = []
    owners: dict[str, list[str]] = {}

    for contract in contracts.values():
        for service_name in contract.runtime.service_descriptors:
            owners.setdefault(service_name, []).append(contract.id)
            if service_name not in descriptors:
                errors.append(f"{contract.id}: missing service descriptor {service_name}")

    missing_owners = sorted(set(descriptors) - set(owners))
    for service_name in missing_owners:
        errors.append(f"{service_name}: missing component owner")

    duplicate_owners = {
        service_name: component_ids
        for service_name, component_ids in sorted(owners.items())
        if len(component_ids) > 1
    }
    for service_name, component_ids in duplicate_owners.items():
        errors.append(f"{service_name}: multiple component owners: {', '.join(component_ids)}")

    for profiles in PROFILE_SETS:
        selected = select_services(descriptors, profiles=list(profiles))
        report = check_deploy_conflicts(selected)
        if report.has_errors:
            profile_key = ",".join(profiles)
            for issue in report.issues:
                if issue.severity == "error":
                    errors.append(f"{profile_key}: {issue.message}")

    payload = _payload(
        ok=not errors,
        contract_count=len(contracts),
        service_count=len(descriptors),
        errors=errors,
    )
    if as_json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0 if payload["ok"] else 1

    if errors:
        print("Component contract validation failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1

    print(f"component contracts OK: {len(contracts)} contracts, {len(descriptors)} services")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
