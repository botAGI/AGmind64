"""Validation helpers for AGmind service topology profile lanes."""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agmind.services.deployment_topology import (
    TopologyWarning,
    build_deployment_topology_report,
)
from agmind.services.renderer import DEFAULT_SERVICES_DIR, load_descriptors, select_services

DEFAULT_TOPOLOGY_PROFILE_SETS = (
    ("core",),
    ("core", "rag"),
    ("core", "observability"),
    ("core", "ragflow"),
    ("core", "rag", "ragflow"),
)


@dataclass(frozen=True)
class TopologyProfileReport:
    """Topology validation result for one profile set."""

    profiles: tuple[str, ...]
    service_count: int
    warnings: tuple[TopologyWarning, ...]
    infos: tuple[TopologyWarning, ...] = ()
    errors: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.errors and not self.warnings

    @property
    def warning_count(self) -> int:
        return len(self.warnings)

    @property
    def info_count(self) -> int:
        return len(self.infos)

    @property
    def error_count(self) -> int:
        return len(self.errors)

    def to_json(self) -> dict[str, Any]:
        return {
            "profiles": list(self.profiles),
            "service_count": self.service_count,
            "ok": self.ok,
            "warning_count": self.warning_count,
            "info_count": self.info_count,
            "error_count": self.error_count,
            "warnings": [warning.to_payload() for warning in self.warnings],
            "infos": [info.to_payload() for info in self.infos],
            "errors": list(self.errors),
        }


@dataclass(frozen=True)
class TopologyCheckReport:
    """Aggregate topology validation report."""

    profiles: tuple[TopologyProfileReport, ...]

    @property
    def ok(self) -> bool:
        return all(profile.ok for profile in self.profiles)

    @property
    def warning_count(self) -> int:
        return sum(profile.warning_count for profile in self.profiles)

    @property
    def info_count(self) -> int:
        return sum(profile.info_count for profile in self.profiles)

    @property
    def error_count(self) -> int:
        return sum(profile.error_count for profile in self.profiles)

    def to_json(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "profile_count": len(self.profiles),
            "warning_count": self.warning_count,
            "info_count": self.info_count,
            "error_count": self.error_count,
            "profiles": [profile.to_json() for profile in self.profiles],
        }


def validate_topology_profiles(
    profile_sets: tuple[tuple[str, ...], ...] = DEFAULT_TOPOLOGY_PROFILE_SETS,
    *,
    services_dir: Path = DEFAULT_SERVICES_DIR,
) -> TopologyCheckReport:
    """Validate standard profile lanes through the shared topology report."""
    descriptors = load_descriptors(services_dir)
    reports: list[TopologyProfileReport] = []

    for profiles in profile_sets:
        selected = select_services(descriptors, profiles=list(profiles))
        if not selected:
            reports.append(
                TopologyProfileReport(
                    profiles=profiles,
                    service_count=0,
                    warnings=(),
                    infos=(),
                    errors=(f"{_profile_key(profiles)}: no services selected",),
                )
            )
            continue

        topology = build_deployment_topology_report(
            selected,
            all_descriptors=descriptors,
        )
        reports.append(
            TopologyProfileReport(
                profiles=profiles,
                service_count=len(selected),
                warnings=topology.warnings,
                infos=topology.infos,
            )
        )

    return TopologyCheckReport(profiles=tuple(reports))


def format_topology_check_report(report: TopologyCheckReport) -> str:
    """Render a human-readable topology check report."""
    lines: list[str] = []
    for profile in report.profiles:
        status = "OK" if profile.ok else "FAILED"
        profile_key = _profile_key(profile.profiles)
        lines.append(
            f"{profile_key}: {status} "
            f"({profile.service_count} services, "
            f"warnings={profile.warning_count}, info={profile.info_count})"
        )
        for warning in profile.warnings:
            lines.append(f"  WARNING: {warning.message}")
        for error in profile.errors:
            lines.append(f"  ERROR: {error}")

    if report.ok:
        lines.append(f"topology OK: {len(report.profiles)} profile sets")
    else:
        failed = sum(1 for profile in report.profiles if not profile.ok)
        lines.append(f"topology FAILED: {failed}/{len(report.profiles)} profile sets failed")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    """CLI-compatible entry point for topology profile validation."""
    args = tuple(argv if argv is not None else sys.argv[1:])
    as_json = "--json" in args
    report = validate_topology_profiles()
    if as_json:
        print(json.dumps(report.to_json(), indent=2, ensure_ascii=False))
    else:
        print(format_topology_check_report(report))
    return 0 if report.ok else 1


def _profile_key(profiles: tuple[str, ...]) -> str:
    return ",".join(profiles) or "<none>"


__all__ = [
    "DEFAULT_TOPOLOGY_PROFILE_SETS",
    "TopologyCheckReport",
    "TopologyProfileReport",
    "format_topology_check_report",
    "main",
    "validate_topology_profiles",
]
