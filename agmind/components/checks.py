"""Component and deploy-level consistency checks."""

from __future__ import annotations

from dataclasses import dataclass

from agmind.schemas import ServiceDescriptor


@dataclass(frozen=True)
class DeployIssue:
    """One deploy-level compatibility issue."""

    severity: str
    kind: str
    services: tuple[str, ...]
    detail: str
    message: str


@dataclass(frozen=True)
class DeployReport:
    """Deploy-level compatibility result."""

    issues: tuple[DeployIssue, ...]

    @property
    def has_errors(self) -> bool:
        return any(issue.severity == "error" for issue in self.issues)


def _host_port(port_spec: str) -> str | None:
    parts = port_spec.split(":")
    if len(parts) == 2:
        return parts[0]
    if len(parts) == 3:
        return parts[1]
    return None


def check_deploy_conflicts(
    selected: dict[str, ServiceDescriptor],
) -> DeployReport:
    """Check conflicts that only exist once services are deployed together."""
    port_owners: dict[str, list[str]] = {}
    for name, descriptor in selected.items():
        for port_spec in descriptor.ports:
            port = _host_port(port_spec)
            if port is None:
                continue
            port_owners.setdefault(port, []).append(name)

    issues: list[DeployIssue] = []
    for port, owners in sorted(port_owners.items()):
        unique = tuple(sorted(set(owners)))
        if len(unique) <= 1:
            continue
        issues.append(
            DeployIssue(
                severity="error",
                kind="host_port_conflict",
                services=unique,
                detail=port,
                message=f"Host port {port} is published by: {', '.join(unique)}",
            )
        )

    return DeployReport(issues=tuple(issues))


__all__ = [
    "DeployIssue",
    "DeployReport",
    "check_deploy_conflicts",
]
