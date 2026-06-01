#!/usr/bin/env python3
"""Validate selected-service topology for standard profile lanes.

Runs two passes:

Pass 1 — 13-lane isolation sweep (ALL_PROFILE_SETS, isolation_mode=True):
    Exercises every single-profile isolation lane.  Dependency/compatibility
    warnings are reclassified as "expected info" for single-profile lanes
    (e.g. "rag" without "core" naturally lacks LLM inference — that gap is
    intentional in isolation).  Catches render failures and unknown profiles.

Pass 2 — strict multi-profile validation (DEFAULT_TOPOLOGY_PROFILE_SETS, isolation_mode=False):
    Validates the known combined stacks (core, core+rag, core+observability,
    core+ragflow, core+rag+ragflow) without the isolation-mode reclassification.
    This pass catches genuine cross-profile topology gaps that would be silently
    promoted to "expected info" in single-profile lanes (WR-02 fix).

Exit codes:
  0 — all passes clean
  1 — one or more lanes failed in either pass
"""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from agmind.services.topology_checks import (  # noqa: E402,I001
    DEFAULT_TOPOLOGY_PROFILE_SETS,
    format_topology_check_report,
    validate_topology_profiles,
)
from agmind.services.profile_sets import ALL_PROFILE_SETS  # noqa: E402


def main(argv: Sequence[str] | None = None) -> int:
    """CLI-compatible entry point: run both topology passes."""
    args = list(argv if argv is not None else sys.argv[1:])
    as_json = "--json" in args

    # Pass 1: all-13-lanes isolation sweep (existing behaviour).
    report_isolation = validate_topology_profiles(ALL_PROFILE_SETS, isolation_mode=True)

    # Pass 2: strict multi-profile validation (WR-02 fix).
    report_strict = validate_topology_profiles(DEFAULT_TOPOLOGY_PROFILE_SETS, isolation_mode=False)

    if as_json:
        payload = {
            "ok": report_isolation.ok and report_strict.ok,
            "isolation_pass": report_isolation.to_json(),
            "strict_pass": report_strict.to_json(),
            # Top-level counters (governance reads these):
            "profile_count": len(report_isolation.profiles) + len(report_strict.profiles),
            "warning_count": report_isolation.warning_count + report_strict.warning_count,
            "info_count": report_isolation.info_count + report_strict.info_count,
            "expected_info_count": (
                report_isolation.expected_info_count + report_strict.expected_info_count
            ),
            "unexpected_info_count": (
                report_isolation.unexpected_info_count + report_strict.unexpected_info_count
            ),
            "error_count": report_isolation.error_count + report_strict.error_count,
        }
        print(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        print("=== Pass 1: isolation sweep (all 11 profiles) ===")
        print(format_topology_check_report(report_isolation))
        print("=== Pass 2: strict multi-profile validation ===")
        print(format_topology_check_report(report_strict))

    return 0 if (report_isolation.ok and report_strict.ok) else 1


if __name__ == "__main__":
    raise SystemExit(main())
