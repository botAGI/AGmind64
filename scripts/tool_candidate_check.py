#!/usr/bin/env python3
"""Validate optional tool candidate catalog and deploy target references."""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from agmind.addons.checks import validate_tool_candidates


def _issue(message: str) -> dict[str, str]:
    return {
        "severity": "error",
        "kind": "tool_candidate_validation",
        "message": message,
    }


def _payload(
    *,
    candidate_count: int,
    target_count: int,
    errors: list[str],
) -> dict[str, Any]:
    return {
        "ok": not errors,
        "candidate_count": candidate_count,
        "target_count": target_count,
        "error_count": len(errors),
        "issues": [_issue(error) for error in errors],
    }


def main(argv: Sequence[str] | None = None) -> int:
    from agmind.addons import load_tool_candidates
    from agmind.components import load_component_contracts
    from agmind.deploy import load_deploy_targets
    from agmind.services.renderer import load_descriptors

    args = tuple(argv if argv is not None else sys.argv[1:])
    as_json = "--json" in args

    candidates = load_tool_candidates()
    deploy_targets = load_deploy_targets()
    descriptors = load_descriptors()
    component_contracts = load_component_contracts()

    errors = validate_tool_candidates(candidates, deploy_targets, descriptors, component_contracts)
    payload = _payload(
        candidate_count=len(candidates),
        target_count=len(deploy_targets),
        errors=errors,
    )
    if as_json:
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0 if payload["ok"] else 1

    if errors:
        print("Tool candidate validation failed:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1

    print(f"tool candidates OK: {len(candidates)} candidates, {len(deploy_targets)} targets")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
