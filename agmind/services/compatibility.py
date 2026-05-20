"""Phase O.A: service compatibility checker (REVISED 2026-05-20).

Original version had `conflicts_with` field + hard 'error' severity. После
research user'a (RAGFlow ↔ Dify плагин существует, vector DBs можно поднимать
несколько одновременно для разных проектов, reverse proxies могут coexist
если хотя бы один из них не публикуется на 80/443) — выяснилось что почти
все declared conflicts были выдуманы.

Текущая модель — **только soft warnings**:
1. `redundant_provider` (warning) — 2+ сервисов с одинаковой capability
   (e.g. qdrant + milvus в одном compose). Не блокирующая — user может
   использовать для разных проектов внутри одного стека.
2. `missing_capability` (warning) — consumer объявляет consumes=['vector_db']
   но никто не provides — env injection не сработает, но docker compose
   ещё может стартовать (сервис со стандартными defaults).

Hard `error` severity больше **не выдаётся** — мы перестали выдумывать
конфликты, оставляя decision за user'ом. Wizard НЕ блокирует Apply на
основе compat report.
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

    # ---- 1. Hard conflicts ----
    # Removed: original "conflicts_with" model was based on выдуманных предположений
    # (ragflow ⟂ dify, qdrant ⟂ milvus, traefik ⟂ caddy). After research все эти
    # сервисы могут coexist. Field оставлен в schema для backward compat / future
    # реальных кейсов где docker compose буквально не может стартовать — но default
    # checker этим не пользуется.

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
