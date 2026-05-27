#!/usr/bin/env python3
"""Validate the manual Kubernetes proof workflow against deploy target contracts."""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))


def main(argv: Sequence[str] | None = None) -> int:
    from agmind.deploy import load_deploy_targets
    from agmind.deploy.target_checks import (
        format_deployment_check_report,
        validate_kubernetes_proof_workflow_report,
    )

    args = tuple(argv if argv is not None else sys.argv[1:])
    as_json = "--json" in args
    targets = load_deploy_targets()
    report = validate_kubernetes_proof_workflow_report(targets)

    if as_json:
        print(json.dumps(report.to_json(), indent=2, ensure_ascii=False))
    elif report.ok:
        print(format_deployment_check_report(report, ok_label="kubernetes proof workflow"))
    else:
        print(
            format_deployment_check_report(report, ok_label="Kubernetes proof workflow"),
            file=sys.stderr,
        )

    if not report.ok:
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
