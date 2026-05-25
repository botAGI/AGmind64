"""`agmind governance` commands for aggregate M7 consistency checks."""

from __future__ import annotations

import json

from agmind.governance import format_governance_report, run_governance_checks


def cmd_validate(as_json: bool = False) -> int:
    """Run all governance gates and print an aggregate report."""
    report = run_governance_checks(structured=True)
    if as_json:
        print(json.dumps(report.to_json(), indent=2, ensure_ascii=False))
    else:
        print(format_governance_report(report))
    return 0 if report.ok else 1
