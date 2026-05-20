"""Phase O.A: service compatibility checker.

Использует ServiceDescriptor.provides + conflicts_with для detection:
1. Hard conflicts: два сервиса где A.conflicts_with[B] (e.g. ragflow + dify-api)
2. Redundant providers: несколько сервисов с одинаковой capability
   (e.g. qdrant + weaviate + milvus = 3 vector_db = redundant)
3. Missing capabilities: consumer объявляет consumes=['vector_db'] но никто
   не provides — warning

Используется renderer'ом + TUI wizard'ом для live warnings.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from agmind.schemas import ServiceDescriptor


@dataclass(frozen=True)
class CompatIssue:
    """One detected compatibility problem."""

    severity: str  # 'error' (hard conflict) | 'warning' (redundancy) | 'info' (missing)
    kind: str  # 'conflict' | 'redundant_provider' | 'missing_capability'
    services: tuple[str, ...]
    capability: str | None
    message: str


@dataclass(frozen=True)
class CompatReport:
    """Result of check_service_compatibility()."""

    issues: tuple[CompatIssue, ...]
    capability_providers: dict[str, tuple[str, ...]]
    """capability → tuple of service names providing it."""

    @property
    def has_errors(self) -> bool:
        return any(i.severity == "error" for i in self.issues)

    @property
    def has_warnings(self) -> bool:
        return any(i.severity == "warning" for i in self.issues)

    def by_severity(self, severity: str) -> tuple[CompatIssue, ...]:
        return tuple(i for i in self.issues if i.severity == severity)


def check_service_compatibility(
    selected: dict[str, ServiceDescriptor],
) -> CompatReport:
    """Audit selected services for conflicts / redundancy / missing capabilities.

    Args:
        selected: dict service_name → descriptor (только выбранные).

    Returns CompatReport с issues sorted by severity (errors first).
    """
    issues: list[CompatIssue] = []
    names = set(selected.keys())

    # ---- 1. Hard conflicts (A.conflicts_with[B] и B оба выбраны) ----
    seen_pairs: set[tuple[str, str]] = set()
    for name, d in selected.items():
        for conflict in d.conflicts_with:
            if conflict not in names:
                continue
            pair = tuple(sorted((name, conflict)))
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            issues.append(CompatIssue(
                severity="error",
                kind="conflict",
                services=pair,
                capability=None,
                message=(
                    f"'{pair[0]}' и '{pair[1]}' конфликтуют — оба нельзя"
                    f" одновременно. Оставь один."
                ),
            ))

    # ---- 2. Redundant providers (>1 service for same capability) ----
    providers: dict[str, list[str]] = defaultdict(list)
    for name, d in selected.items():
        for cap in d.provides:
            providers[cap].append(name)
    capability_providers: dict[str, tuple[str, ...]] = {
        cap: tuple(sorted(svcs)) for cap, svcs in providers.items()
    }
    for cap, svcs in providers.items():
        if len(svcs) > 1:
            issues.append(CompatIssue(
                severity="warning",
                kind="redundant_provider",
                services=tuple(sorted(svcs)),
                capability=cap,
                message=(
                    f"Capability '{cap}' предоставляется {len(svcs)} сервисами: "
                    f"{', '.join(sorted(svcs))}. Достаточно одного."
                ),
            ))

    # ---- 3. Missing capabilities (consumer без provider) ----
    consumed: dict[str, list[str]] = defaultdict(list)
    for name, d in selected.items():
        for cap in d.consumes:
            consumed[cap].append(name)
    for cap, consumers in consumed.items():
        if cap not in providers:
            issues.append(CompatIssue(
                severity="warning",
                kind="missing_capability",
                services=tuple(sorted(consumers)),
                capability=cap,
                message=(
                    f"Сервис(ы) {', '.join(sorted(consumers))} requires "
                    f"'{cap}', но ни один selected сервис не provides его."
                ),
            ))

    # Sort: errors → warnings → info
    severity_order = {"error": 0, "warning": 1, "info": 2}
    issues.sort(key=lambda i: (severity_order.get(i.severity, 99), i.kind, i.services))

    return CompatReport(
        issues=tuple(issues),
        capability_providers=capability_providers,
    )


def resolve_capability_provider(
    selected: dict[str, ServiceDescriptor],
    capability: str,
) -> str | None:
    """Return name of the (single) service providing capability в selected set.

    Если providers > 1 — возвращает первый по алфавиту (с т.з. determinism).
    Если 0 — None.
    """
    providers = sorted(
        name for name, d in selected.items() if capability in d.provides
    )
    return providers[0] if providers else None


__all__ = [
    "CompatIssue",
    "CompatReport",
    "check_service_compatibility",
    "resolve_capability_provider",
]
