"""`agmind tools` commands for optional homelab/enterprise tool candidates."""

from __future__ import annotations

import json
import sys
from collections import Counter
from typing import Any

from agmind.addons import ToolCandidate, load_tool_candidates
from agmind.addons.checks import validate_tool_candidates
from agmind.components import load_component_contracts
from agmind.deploy import load_deploy_targets
from agmind.services.renderer import load_descriptors


def _candidate_payload(candidate: ToolCandidate) -> dict[str, Any]:
    return {
        "id": candidate.id,
        "name": candidate.name,
        "status": candidate.status,
        "category": candidate.category,
        "summary": candidate.summary,
        "recommended_version": candidate.recommended_version,
        "version_source": candidate.version_source,
        "admission": candidate.admission.model_dump(mode="json"),
        "deploy_targets": list(candidate.dependencies.deploy_targets),
        "profiles": list(candidate.dependencies.profiles),
        "storage_profiles": list(candidate.dependencies.storage_profiles),
        "secrets_profiles": list(candidate.dependencies.secrets_profiles),
        "ports": list(candidate.dependencies.ports),
        "requires_gpu": candidate.dependencies.requires_gpu,
        "risks": list(candidate.risks),
        "next_step": candidate.next_step,
        "verification": candidate.verification.model_dump(mode="json"),
    }


def _load_admission_errors(candidates: dict[str, ToolCandidate]) -> list[str]:
    return validate_tool_candidates(
        candidates,
        load_deploy_targets(),
        load_descriptors(),
        load_component_contracts(),
    )


def _candidate_errors(candidate_id: str, errors: list[str]) -> list[str]:
    prefix = f"{candidate_id}:"
    return [error for error in errors if error.startswith(prefix)]


def cmd_list(as_json: bool = False) -> int:
    """List optional tool candidates and accepted integrations."""
    candidates = load_tool_candidates()
    status_counts = Counter(candidate.status for candidate in candidates.values())

    if as_json:
        payload = {
            "summary": dict(sorted(status_counts.items())),
            "tools": [_candidate_payload(candidate) for candidate in candidates.values()],
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    if not candidates:
        print("No tool candidates found.", file=sys.stderr)
        return 1

    width = max(len(candidate_id) for candidate_id in candidates) + 2
    print(f"{'ID':<{width}} {'STATUS':<10} {'CATEGORY':<16} {'PROFILES':<24} PORTS")
    print("-" * (width + 62))
    for candidate in candidates.values():
        profiles = ",".join(candidate.dependencies.profiles) or "-"
        ports = ",".join(candidate.dependencies.ports) or "-"
        print(
            f"{candidate.id:<{width}} {candidate.status:<10} "
            f"{candidate.category:<16} {profiles:<24} {ports}"
        )
    print()
    print(
        "Summary: "
        + ", ".join(f"{status}={count}" for status, count in sorted(status_counts.items()))
    )
    return 0


def cmd_status(name: str, as_json: bool = False) -> int:
    """Show one optional tool candidate with admission status."""
    candidates = load_tool_candidates()
    candidate = candidates.get(name)
    if candidate is None:
        print(f"Tool candidate '{name}' not found. Run `agmind tools list`.", file=sys.stderr)
        return 1

    errors = _candidate_errors(candidate.id, _load_admission_errors(candidates))
    admission_status = "OK" if candidate.status == "accepted" and not errors else "pending"
    if errors:
        admission_status = "FAILED"

    payload = _candidate_payload(candidate)
    payload["admission_status"] = admission_status.lower()
    payload["admission_errors"] = errors
    if as_json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    print(f"ID: {candidate.id}")
    print(f"Name: {candidate.name}")
    print(f"Status: {candidate.status}")
    print(f"Category: {candidate.category}")
    if candidate.recommended_version:
        print(f"Recommended version: {candidate.recommended_version}")
    if candidate.version_source:
        print(f"Version source: {candidate.version_source}")
    print(f"Admission: {admission_status}")
    print(f"Scope: {candidate.admission.scope} / {candidate.admission.runtime}")
    print(f"Deploy targets: {', '.join(candidate.dependencies.deploy_targets) or '-'}")
    print(f"Profiles: {', '.join(candidate.dependencies.profiles) or '-'}")
    print(f"Ports: {', '.join(candidate.dependencies.ports) or '-'}")
    print(f"GPU: {'yes' if candidate.dependencies.requires_gpu else 'no'}")
    print(f"Summary: {candidate.summary}")
    if candidate.risks:
        print("Risks:")
        for risk in candidate.risks:
            print(f"  - {risk}")
    if candidate.verification.commands:
        print("Verification:")
        for command in candidate.verification.commands:
            print(f"  - {command}")
    if errors:
        print("Admission errors:")
        for error in errors:
            print(f"  - {error}")
    print(f"Next step: {candidate.next_step}")
    return 0


def cmd_validate() -> int:
    """Validate optional tool candidate catalog and accepted runtime admission."""
    candidates = load_tool_candidates()
    deploy_targets = load_deploy_targets()
    errors = validate_tool_candidates(
        candidates,
        deploy_targets,
        load_descriptors(),
        load_component_contracts(),
    )

    if errors:
        print("Tool candidate validation failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1

    print(f"tool candidates OK: {len(candidates)} candidates, {len(deploy_targets)} targets")
    return 0
