#!/usr/bin/env python3
"""Validate that every AGmind compose profile renders without error.

Renders all 13 isolation lanes from ``ALL_PROFILE_SETS`` using the Python
renderer API (not a subprocess) and asserts each one produces a non-empty
compose service map.  Any render exception or empty selection fails the lane.

Exit codes:
  0 — all lanes rendered successfully
  1 — one or more lanes failed

Usage::

    python scripts/checks/compose_profile_check.py
    python scripts/checks/compose_profile_check.py --json
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from agmind.services.profile_sets import ALL_PROFILE_SETS  # noqa: E402,I001
from agmind.services.renderer import (  # noqa: E402
    DEFAULT_SERVICES_DIR,
    load_descriptors,
    render_compose,
    select_services,
)


@dataclass(frozen=True)
class ComposeProfileLaneReport:
    """Render result for a single isolation lane."""

    profiles: tuple[str, ...]
    ok: bool
    service_count: int
    error: str = ""

    def to_json(self) -> dict[str, Any]:
        return {
            "profiles": list(self.profiles),
            "ok": self.ok,
            "service_count": self.service_count,
            "error": self.error,
        }


@dataclass
class ComposeProfileCheckReport:
    """Aggregate render validation across all lanes."""

    lanes: list[ComposeProfileLaneReport] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(lane.ok for lane in self.lanes)

    @property
    def error_count(self) -> int:
        return sum(1 for lane in self.lanes if not lane.ok)

    def to_json(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "lane_count": len(self.lanes),
            "error_count": self.error_count,
            "lanes": [lane.to_json() for lane in self.lanes],
        }


def validate_compose_profiles(
    profile_sets: tuple[tuple[str, ...], ...] = ALL_PROFILE_SETS,
    *,
    services_dir: Path = DEFAULT_SERVICES_DIR,
) -> ComposeProfileCheckReport:
    """Render every profile set in ``profile_sets`` and report failures."""
    descriptors = load_descriptors(services_dir)
    report = ComposeProfileCheckReport()

    for profiles in profile_sets:
        profile_key = ",".join(profiles) or "<none>"
        try:
            selected = select_services(descriptors, profiles=list(profiles))
            if not selected:
                report.lanes.append(
                    ComposeProfileLaneReport(
                        profiles=profiles,
                        ok=False,
                        service_count=0,
                        error=f"{profile_key}: no services selected for this profile",
                    )
                )
                continue
            compose = render_compose(list(selected.values()), traefik_enabled=False)
            svc_count = len(compose.get("services") or {})
            report.lanes.append(
                ComposeProfileLaneReport(
                    profiles=profiles,
                    ok=True,
                    service_count=svc_count,
                )
            )
        except Exception as exc:  # noqa: BLE001
            report.lanes.append(
                ComposeProfileLaneReport(
                    profiles=profiles,
                    ok=False,
                    service_count=0,
                    error=f"{profile_key}: render raised {type(exc).__name__}: {exc}",
                )
            )

    return report


def format_report(report: ComposeProfileCheckReport) -> str:
    """Human-readable summary of the compose profile check."""
    lines: list[str] = []
    for lane in report.lanes:
        status = "OK" if lane.ok else "FAILED"
        profile_key = ",".join(lane.profiles) or "<none>"
        lines.append(f"{profile_key}: {status} ({lane.service_count} services)")
        if lane.error:
            lines.append(f"  ERROR: {lane.error}")
    if report.ok:
        lines.append(f"compose-profile-check OK: {len(report.lanes)} lanes")
    else:
        lines.append(
            f"compose-profile-check FAILED: {report.error_count}/{len(report.lanes)} lanes failed"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """Entry point: render all 12 profile lanes, return 0 on success."""
    args = list(argv if argv is not None else sys.argv[1:])
    as_json = "--json" in args

    report = validate_compose_profiles()

    if as_json:
        print(json.dumps(report.to_json(), indent=2, ensure_ascii=False))
    else:
        print(format_report(report))

    return 0 if report.ok else 1


__all__ = [
    "ComposeProfileCheckReport",
    "ComposeProfileLaneReport",
    "format_report",
    "main",
    "validate_compose_profiles",
]


if __name__ == "__main__":
    raise SystemExit(main())
