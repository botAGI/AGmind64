"""Phase O.A: service compatibility checker (REVISED 2026-05-20).

Original version had `conflicts_with` field + hard 'error' severity. После
research user'a (RAGFlow ↔ Dify плагин существует, vector DBs можно поднимать
несколько одновременно для разных проектов, reverse proxies могут coexist
если хотя бы один из них не публикуется на 80/443) — выяснилось что почти
все declared conflicts были выдуманы.

Текущая модель — warnings для неоднозначностей и hard errors для выбора,
который renderer всё равно не сможет собрать:
1. `redundant_provider` (warning) — 2+ сервисов с одинаковой capability
   (e.g. qdrant + milvus в одном compose). Не блокирующая — user может
   использовать для разных проектов внутри одного стека.
2. `ambiguous_dify_vector_provider` (warning) — Dify stack active while 2+
   Dify vector DB providers are selected. Не блокирующая, but operator should
   choose one active `VECTOR_STORE` for Dify API/worker.
3. `missing_capability` (error) — consumer объявляет consumes=['vector_db']
   но никто не provides. Renderer fail-closed на таком выборе, поэтому wizard
   тоже блокирует Apply до deploy.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from agmind.schemas import ServiceDescriptor
from agmind.services.retrieval_policy import DIFY_SERVICES, DIFY_VECTOR_PROVIDERS

OPTIONAL_MISSING_CAPABILITIES = frozenset({"dify_external_kb", "reranker"})


@dataclass(frozen=True)
class CompatIssue:
    """One detected compatibility problem."""

    severity: str  # 'error' (hard blocker) | 'warning' (redundancy) | 'info' (optional)
    kind: str  # conflict | redundant_provider | ambiguous_* | missing_capability
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
    # (ragflow ⟂ dify, qdrant ⟂ milvus). After research все эти
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
            issues.append(
                CompatIssue(
                    severity="warning",
                    kind="redundant_provider",
                    services=tuple(sorted(svcs)),
                    capability=cap,
                    message=(
                        f"Capability '{cap}' предоставляется {len(svcs)} сервисами: "
                        f"{', '.join(sorted(svcs))}. Достаточно одного."
                    ),
                )
            )

    # ---- 2b. Role-specific provider ambiguity ----
    dify_active = bool(set(selected) & DIFY_SERVICES)
    dify_vector_providers = tuple(
        provider
        for provider in sorted(DIFY_VECTOR_PROVIDERS)
        if provider in selected and "vector_db" in selected[provider].provides
    )
    if dify_active and len(dify_vector_providers) > 1:
        issues.append(
            CompatIssue(
                severity="warning",
                kind="ambiguous_dify_vector_provider",
                services=dify_vector_providers,
                capability="vector_db",
                message=(
                    "Dify has multiple vector_db providers selected: "
                    f"{', '.join(dify_vector_providers)}. "
                    "Choose one active VECTOR_STORE for the Dify stack; "
                    "RAGFlow uses search_index separately."
                ),
            )
        )

    # ---- 3. Missing capabilities (consumer без provider) ----
    # Cross-profile / closure-pulled consumes (e.g. prometheus→docker_api co-pulls docker-socket-
    # proxy; openwebui→llm_inference co-pulls llama-llm) are satisfied at deploy by
    # resolve_service_selection. This validation runs on the raw `select_services` set, which does
    # NOT expand that closure, so such consumes are NOT genuinely missing — don't hard-error them
    # (matches renderer._check_unresolved_consumes). live-audit 2026-06-05.
    from agmind.services.topology_checks import (
        CLOSURE_PULLED_CAPABILITIES,
        KNOWN_CROSS_PROFILE_CONSUMES,
    )

    consumed: dict[str, list[str]] = defaultdict(list)
    for name, d in selected.items():
        for cap in d.consumes:
            consumed[cap].append(name)
    for cap, consumers in consumed.items():
        if cap not in providers:
            optional = cap in OPTIONAL_MISSING_CAPABILITIES
            if optional:
                flagged = sorted(consumers)  # optional: surface as info for every consumer
            elif cap in CLOSURE_PULLED_CAPABILITIES:
                flagged = []  # sole provider is closure-pulled (docker_api → proxy) — derived
            else:
                # hard error only for consumers whose consume is NOT a known closure-pulled link
                flagged = sorted(
                    c for c in consumers if (c, cap) not in KNOWN_CROSS_PROFILE_CONSUMES
                )
            if not flagged:
                continue
            issues.append(
                CompatIssue(
                    severity="info" if optional else "error",
                    kind="optional_missing_capability" if optional else "missing_capability",
                    services=tuple(flagged),
                    capability=cap,
                    message=(
                        f"Сервис(ы) {', '.join(flagged)} requires "
                        f"'{cap}', но ни один selected сервис не provides его."
                    ),
                )
            )

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
    providers = sorted(name for name, d in selected.items() if capability in d.provides)
    return providers[0] if providers else None


def resolve_capability_provider_for_consumer(
    selected: dict[str, ServiceDescriptor],
    capability: str,
    consumer: str,
) -> str | None:
    """Return a provider that can configure `consumer` for `capability`.

    Capability ownership alone is not always enough: future providers may
    expose a capability for another tool family before AGmind has env bindings
    for every consumer. Prefer providers with an explicit binding for the
    consumer; fall back to deterministic capability ownership when no binding
    exists.
    """
    from agmind.services.capability_bindings import env_for_consumer

    providers = sorted(
        (name for name, d in selected.items() if capability in d.provides),
        key=lambda name: _provider_sort_key(capability, consumer, name),
    )
    for provider in providers:
        if env_for_consumer(capability, provider, consumer):
            return provider
    return providers[0] if providers else None


def _provider_sort_key(capability: str, consumer: str, provider: str) -> tuple[int, str]:
    if capability == "vector_db" and consumer in DIFY_SERVICES:
        rank = {name: index for index, name in enumerate(DIFY_VECTOR_PROVIDERS)}
        return (rank.get(provider, len(rank)), provider)
    return (0, provider)


__all__ = [
    "CompatIssue",
    "CompatReport",
    "check_service_compatibility",
    "resolve_capability_provider",
    "resolve_capability_provider_for_consumer",
]
