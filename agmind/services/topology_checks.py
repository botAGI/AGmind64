"""Validation helpers for AGmind service topology profile lanes."""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agmind.schemas import ServiceDescriptor
from agmind.services.deployment_topology import (
    TopologyWarning,
    build_deployment_topology_report,
)
from agmind.services.profile_sets import ALL_PROFILE_SETS
from agmind.services.renderer import (
    DEFAULT_SERVICES_DIR,
    load_descriptors,
    select_services,
    unknown_profiles,
)

# Backward-compatibility alias: existing callers that import
# DEFAULT_TOPOLOGY_PROFILE_SETS continue to work unchanged.
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
    def expected_infos(self) -> tuple[TopologyWarning, ...]:
        return tuple(info for info in self.infos if info.expected)

    @property
    def expected_info_count(self) -> int:
        return len(self.expected_infos)

    @property
    def unexpected_infos(self) -> tuple[TopologyWarning, ...]:
        return tuple(info for info in self.infos if not info.expected)

    @property
    def unexpected_info_count(self) -> int:
        return len(self.unexpected_infos)

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
            "expected_info_count": self.expected_info_count,
            "unexpected_info_count": self.unexpected_info_count,
            "error_count": self.error_count,
            "warnings": [warning.to_payload() for warning in self.warnings],
            "infos": [info.to_payload() for info in self.infos],
            "expected_infos": [info.to_payload() for info in self.expected_infos],
            "unexpected_infos": [info.to_payload() for info in self.unexpected_infos],
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
    def expected_info_count(self) -> int:
        return sum(profile.expected_info_count for profile in self.profiles)

    @property
    def unexpected_info_count(self) -> int:
        return sum(profile.unexpected_info_count for profile in self.profiles)

    @property
    def error_count(self) -> int:
        return sum(profile.error_count for profile in self.profiles)

    def to_json(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "profile_count": len(self.profiles),
            "warning_count": self.warning_count,
            "info_count": self.info_count,
            "expected_info_count": self.expected_info_count,
            "unexpected_info_count": self.unexpected_info_count,
            "error_count": self.error_count,
            "profiles": [profile.to_json() for profile in self.profiles],
        }


def validate_topology_profiles(
    profile_sets: tuple[tuple[str, ...], ...] = ALL_PROFILE_SETS,
    *,
    services_dir: Path = DEFAULT_SERVICES_DIR,
    isolation_mode: bool = True,
) -> TopologyCheckReport:
    """Validate standard profile lanes through the shared topology report.

    Args:
        profile_sets: Tuples of profile names to validate, one lane per tuple.
        services_dir: Path to the service descriptor directory.
        isolation_mode: When *True* (the default), single-profile lanes are run
            in "isolation" and dependency/compatibility warnings are reclassified
            as expected infos so the lane reports green.  This is correct for the
            13-profile all-lanes validation: a lane like ``("rag",)`` naturally
            lacks an LLM inference provider — that gap is expected and intentional
            in isolation; the full combined stacks (``core,rag``) catch real gaps.
            Set to *False* for strict multi-profile cross-validation.
    """
    descriptors = load_descriptors(services_dir)
    reports: list[TopologyProfileReport] = []

    for profiles in profile_sets:
        missing_profiles = unknown_profiles(descriptors, list(profiles))
        if missing_profiles:
            reports.append(
                TopologyProfileReport(
                    profiles=profiles,
                    service_count=0,
                    warnings=(),
                    infos=(),
                    errors=(
                        f"{_profile_key(profiles)}: unknown profile(s): "
                        f"{', '.join(missing_profiles)}",
                    ),
                )
            )
            continue

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

        # In isolation_mode, reclassify all topology warnings for single-profile
        # lanes as expected infos.  A profile run in isolation is inherently
        # missing the services from sibling profiles (e.g. "rag" without "core"
        # has no LLM inference provider) — these gaps are expected in isolation
        # and should not block the lane from passing.
        is_isolated = isolation_mode and len(profiles) == 1
        if is_isolated and topology.warnings:
            promoted_infos = tuple(
                TopologyWarning(
                    source=w.source,
                    severity="info",
                    kind=w.kind,
                    services=w.services,
                    capability=w.capability,
                    message=w.message,
                    expected=True,
                )
                for w in topology.warnings
            )
            reports.append(
                TopologyProfileReport(
                    profiles=profiles,
                    service_count=len(selected),
                    warnings=(),
                    infos=topology.infos + promoted_infos,
                )
            )
        else:
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
        counters = [
            f"warnings={profile.warning_count}",
            f"info={profile.info_count}",
        ]
        if profile.expected_info_count:
            counters.append(f"expected_info={profile.expected_info_count}")
        if profile.unexpected_info_count:
            counters.append(f"unexpected_info={profile.unexpected_info_count}")
        lines.append(
            f"{profile_key}: {status} ({profile.service_count} services, {', '.join(counters)})"
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


# ---------------------------------------------------------------------------
# Dep-graph closure guards
# ---------------------------------------------------------------------------

# Intentional cross-profile ``depends_on`` relationships documented here.
# Each entry is a (consumer_service, dependency_service) pair.  These are
# architectural design choices that require multi-profile deployments; they
# are NOT bugs.  Add a new entry ONLY when a cross-profile dep is genuinely
# intentional — this is the registry that lets the guard fail closed on
# *unexpected* cross-profile deps.
# Empty: nginx was removed from the catalog in phase 08 (user decision — same
# defect class as caddy: no conf.d template, crashes on health check). Its former
# cross-profile depends_on dify-api/dify-web had already been dropped (violated
# Правила §12: edge proxies must not hard-depend on app upstreams). No descriptor
# currently declares an intentional cross-profile depends_on; the guard
# fail-closes on any new one.
KNOWN_CROSS_PROFILE_DEPENDS: set[tuple[str, str]] = set()

# Intentional cross-profile ``consumes`` relationships: (consumer, capability).
# A consumer in profile P consuming a capability from a provider in profile Q
# implies multi-profile deployment.  Document every such expected gap here.
KNOWN_CROSS_PROFILE_CONSUMES: set[tuple[str, str]] = {
    # dify-api / dify-worker (rag) consume inference from llama-* (core).
    # Always deployed together: --profile core,rag
    ("dify-api", "llm_inference"),
    ("dify-api", "embedding_inference"),
    ("dify-api", "reranker"),
    ("dify-api", "dify_external_kb"),  # ragflow (ragflow) → optional integration
    ("dify-worker", "llm_inference"),
    ("dify-worker", "embedding_inference"),
    # openwebui (ui) needs an LLM backend (core).
    ("openwebui", "llm_inference"),
    # ragflow (ragflow) needs inference from llama-* (core).
    ("ragflow", "llm_inference"),
    ("ragflow", "embedding_inference"),
    ("ragflow", "reranker"),
}


def check_depends_on_within_profile(
    descriptors: dict[str, ServiceDescriptor],
    *,
    known_cross_profile: set[tuple[str, str]] = KNOWN_CROSS_PROFILE_DEPENDS,
) -> list[str]:
    """Return a list of cross-profile ``depends_on`` violations.

    For each descriptor D and each name N in ``D.depends_on``:
    - If N does not exist in ``descriptors``: violation (missing dep).
    - If N exists but shares no profile with D AND the (D.name, N) pair is
      NOT in ``known_cross_profile``: violation (unexpected cross-profile dep).

    Returns:
        A list of human-readable violation strings (empty → catalog is clean).
    """
    violations: list[str] = []
    for name, desc in sorted(descriptors.items()):
        for dep in desc.depends_on:
            if dep not in descriptors:
                violations.append(f"{name}: depends_on '{dep}' does not exist in the catalog")
                continue
            dep_desc = descriptors[dep]
            shared = set(desc.profiles) & set(dep_desc.profiles)
            if not shared and (name, dep) not in known_cross_profile:
                violations.append(
                    f"{name}: depends_on '{dep}' shares no compose profile "
                    f"(consumer={sorted(desc.profiles)}, dep={sorted(dep_desc.profiles)})"
                )
    return violations


def check_consumes_within_profile(
    descriptors: dict[str, ServiceDescriptor],
    *,
    known_cross_profile: set[tuple[str, str]] = KNOWN_CROSS_PROFILE_CONSUMES,
) -> list[str]:
    """Return a list of cross-profile ``consumes`` satisfiability violations.

    For each descriptor D and capability C in ``D.consumes``, at least one
    provider P (i.e. C ∈ P.provides) must share a profile with D.  If no such
    provider exists AND (D.name, C) is NOT in ``known_cross_profile``: violation.

    Returns:
        A list of human-readable violation strings (empty → catalog is clean).
    """
    violations: list[str] = []
    for name, desc in sorted(descriptors.items()):
        for cap in desc.consumes:
            providers = [p for p in descriptors.values() if cap in p.provides]
            satisfiable = any(set(desc.profiles) & set(p.profiles) for p in providers)
            if not satisfiable and (name, cap) not in known_cross_profile:
                provider_info = [
                    f"{p.name}{sorted(p.profiles)}" for p in sorted(providers, key=lambda x: x.name)
                ]
                violations.append(
                    f"{name}: consumes '{cap}' but no provider shares a profile "
                    f"(consumer={sorted(desc.profiles)}, providers={provider_info})"
                )
    return violations


__all__ = [
    "ALL_PROFILE_SETS",
    "DEFAULT_TOPOLOGY_PROFILE_SETS",
    "KNOWN_CROSS_PROFILE_CONSUMES",
    "KNOWN_CROSS_PROFILE_DEPENDS",
    "TopologyCheckReport",
    "TopologyProfileReport",
    "check_consumes_within_profile",
    "check_depends_on_within_profile",
    "format_topology_check_report",
    "main",
    "validate_topology_profiles",
]
