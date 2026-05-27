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


_WILDCARD_BINDS = frozenset({"*", "0.0.0.0", "::"})


def _published_endpoint(port_spec: str) -> tuple[str, str] | None:
    """Return (bind, host_port) for a compose port spec, or None if unmapped.

    `host:container` binds every interface, modelled as the wildcard `"*"`.
    `ip:host:container` keeps the explicit bind IP.
    """
    parts = port_spec.split(":")
    if len(parts) == 2:
        return ("*", parts[0])
    if len(parts) == 3:
        return (parts[0], parts[1])
    return None


def _binds_conflict(bind_a: str, bind_b: str) -> bool:
    return bind_a == bind_b or bind_a in _WILDCARD_BINDS or bind_b in _WILDCARD_BINDS


def check_deploy_conflicts(
    selected: dict[str, ServiceDescriptor],
) -> DeployReport:
    """Check conflicts that only exist once services are deployed together."""
    port_endpoints: dict[str, list[tuple[str, str]]] = {}
    for name, descriptor in selected.items():
        for port_spec in descriptor.ports:
            endpoint = _published_endpoint(port_spec)
            if endpoint is None:
                continue
            bind, port = endpoint
            port_endpoints.setdefault(port, []).append((name, bind))

    issues: list[DeployIssue] = []
    for port, endpoints in sorted(port_endpoints.items()):
        conflicting: set[str] = set()
        for i in range(len(endpoints)):
            name_a, bind_a = endpoints[i]
            for j in range(i + 1, len(endpoints)):
                name_b, bind_b = endpoints[j]
                if name_a == name_b:
                    continue
                if _binds_conflict(bind_a, bind_b):
                    conflicting.update((name_a, name_b))

        unique = tuple(sorted(conflicting))
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
