"""Shared operator topology report for selected AGmind services."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agmind.schemas import ServiceDescriptor
from agmind.services.compatibility import CompatIssue, check_service_compatibility
from agmind.services.renderer import (
    DEFAULT_SERVICES_DIR,
    check_missing_dependencies,
    load_descriptors,
    select_services,
)
from agmind.services.retrieval_policy import (
    selected_dify_vector_provider,
    selected_dify_vector_providers,
    selected_ragflow_search_provider,
    summarize_retrieval_topology,
)


@dataclass(frozen=True)
class TopologyWarning:
    """Structured warning emitted by deployment topology checks."""

    source: str
    severity: str
    kind: str
    services: tuple[str, ...]
    capability: str | None
    message: str
    expected: bool = False

    def to_payload(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "severity": self.severity,
            "kind": self.kind,
            "services": list(self.services),
            "capability": self.capability,
            "message": self.message,
            "expected": self.expected,
        }


@dataclass(frozen=True)
class DeploymentTopologyReport:
    """Operator-facing topology summary for a selected service set."""

    services: tuple[str, ...]
    retrieval_lines: tuple[str, ...]
    warnings: tuple[TopologyWarning, ...]
    infos: tuple[TopologyWarning, ...] = ()

    @property
    def has_warnings(self) -> bool:
        return bool(self.warnings)

    @property
    def warning_count(self) -> int:
        return len(self.warnings)

    @property
    def has_infos(self) -> bool:
        return bool(self.infos)

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
    def dependency_warnings(self) -> tuple[str, ...]:
        return tuple(warning.message for warning in self.warnings if warning.source == "dependency")

    @property
    def compatibility_warnings(self) -> tuple[str, ...]:
        return tuple(
            warning.message for warning in self.warnings if warning.source == "compatibility"
        )

    @property
    def compatibility_infos(self) -> tuple[str, ...]:
        return tuple(info.message for info in self.infos if info.source == "compatibility")

    def block_lines(self, *, warning_limit: int | None = None) -> tuple[str, ...]:
        """Return compact lines suitable for TUI/CLI status summaries."""
        lines: list[str] = []
        if self.retrieval_lines:
            lines.append("RAG STORAGE PLAN ..")
            lines.extend(f"    {line}" for line in self.retrieval_lines)

        warnings = [warning.message for warning in self.warnings]
        if warning_limit is not None:
            warnings = warnings[:warning_limit]
        if warnings:
            lines.append("TOPOLOGY WARNINGS .")
            lines.extend(f"    {warning}" for warning in warnings)

        return tuple(lines)

    def to_payload(self) -> dict[str, Any]:
        """Return a stable JSON-serializable payload for CLI/CI consumers."""
        dependency_count = sum(1 for warning in self.warnings if warning.source == "dependency")
        compatibility_count = sum(
            1 for warning in self.warnings if warning.source == "compatibility"
        )
        compatibility_info_count = sum(1 for info in self.infos if info.source == "compatibility")
        return {
            "services": list(self.services),
            "retrieval": {
                "dify_vector_provider": selected_dify_vector_provider(self.services),
                "dify_vector_providers": list(selected_dify_vector_providers(self.services)),
                "ragflow_search_provider": selected_ragflow_search_provider(self.services),
                "lines": list(self.retrieval_lines),
            },
            "retrieval_lines": list(self.retrieval_lines),
            "warnings": [warning.to_payload() for warning in self.warnings],
            "infos": [info.to_payload() for info in self.infos],
            "expected_infos": [info.to_payload() for info in self.expected_infos],
            "unexpected_infos": [info.to_payload() for info in self.unexpected_infos],
            "dependency_warnings": list(self.dependency_warnings),
            "compatibility_warnings": list(self.compatibility_warnings),
            "compatibility_infos": list(self.compatibility_infos),
            "dependency_warning_count": dependency_count,
            "compatibility_warning_count": compatibility_count,
            "compatibility_info_count": compatibility_info_count,
            "warning_count": self.warning_count,
            "info_count": self.info_count,
            "expected_info_count": self.expected_info_count,
            "unexpected_info_count": self.unexpected_info_count,
            "has_warnings": self.has_warnings,
            "has_infos": self.has_infos,
        }


def build_deployment_topology_report(
    selected: Mapping[str, ServiceDescriptor],
    *,
    all_descriptors: Mapping[str, ServiceDescriptor] | None = None,
) -> DeploymentTopologyReport:
    """Build a shared topology report for setup, compose, and future renderers."""
    services = tuple(sorted(selected))
    warnings = list(_dependency_warnings(selected, all_descriptors))
    compatibility = check_service_compatibility(dict(selected))
    for severity in ("error", "warning"):
        warnings.extend(
            TopologyWarning(
                source="compatibility",
                severity=issue.severity,
                kind=issue.kind,
                services=issue.services,
                capability=issue.capability,
                message=issue.message,
            )
            for issue in compatibility.by_severity(severity)
        )
    infos = tuple(
        TopologyWarning(
            source="compatibility",
            severity=issue.severity,
            kind=issue.kind,
            services=issue.services,
            capability=issue.capability,
            message=issue.message,
            expected=_topology_issue_is_expected(issue),
        )
        for issue in compatibility.by_severity("info")
    )

    return DeploymentTopologyReport(
        services=services,
        retrieval_lines=tuple(summarize_retrieval_topology(services)),
        warnings=tuple(warnings),
        infos=infos,
    )


def _topology_issue_is_expected(issue: CompatIssue) -> bool:
    return issue.severity == "info" and issue.kind == "optional_missing_capability"


def build_deployment_topology_report_for_services(
    services: list[str] | tuple[str, ...],
    *,
    services_dir: Path = DEFAULT_SERVICES_DIR,
) -> DeploymentTopologyReport:
    """Load descriptors and build a topology report for explicit service names."""
    all_descriptors = load_descriptors(services_dir)
    missing = sorted(set(services).difference(all_descriptors))
    if missing:
        raise ValueError(f"Unknown services requested: {', '.join(missing)}")
    selected = select_services(all_descriptors, services=list(services))
    return build_deployment_topology_report(
        selected,
        all_descriptors=all_descriptors,
    )


def _dependency_warnings(
    selected: Mapping[str, ServiceDescriptor],
    all_descriptors: Mapping[str, ServiceDescriptor] | None,
) -> tuple[TopologyWarning, ...]:
    if all_descriptors is None:
        return ()
    missing = check_missing_dependencies(dict(selected), dict(all_descriptors))
    return tuple(
        TopologyWarning(
            source="dependency",
            severity="warning",
            kind="missing_dependency",
            services=(service,),
            capability=None,
            message=f"{service} needs {dependency}",
        )
        for service, dependencies in sorted(missing.items())
        for dependency in dependencies
    )


__all__ = [
    "DeploymentTopologyReport",
    "TopologyWarning",
    "build_deployment_topology_report",
    "build_deployment_topology_report_for_services",
]
