"""Operator-facing deploy scenario catalog (M8 Phase 10-02).

A *scenario* is a named, curated, closure-complete service selection an operator can render
and deploy as an isolated compose project via ``agmind render scenario <name>``. This is the
single source of truth for operator deploy presets.

Relationship to ``agmind/install/verify.py``: that module's ``InstallVerifyScenario`` fixtures
exist to PROVE deployability in CI (they intentionally include the full default set, e.g.
dozzle/netdata/homarr). The operator catalog here is a deliberately leaner, curated view —
it omits services kept in the catalog "for interest" but not deployed by default. Every
scenario here is validated to RENDER cleanly (closure-complete) by the test-suite.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Scenario:
    """A named operator deploy preset."""

    name: str
    description: str
    services: tuple[str, ...]


# Curated, closure-complete operator presets. Service lists are reused from the CI-verified
# InstallVerifyScenario sets where they overlap, minus the services the operator keeps out of
# the default selection. `tests/services/test_scenarios.py` renders every one of these.
SCENARIO_CATALOG: tuple[Scenario, ...] = (
    Scenario(
        name="inference",
        description="Local inference only: LLM + embed + rerank + Qdrant vector store. "
        "No edge proxy (no Cloudflare token / domain required) — the lean local path.",
        services=("llama-llm", "llama-embed", "llama-rerank", "qdrant"),
    ),
    # Every traefik (public edge) preset carries authelia + redis: chain-llm/chain-internal
    # routes forwardAuth to authelia, and the renderer fail-closes a traefik render without
    # it (P0.3 / 15-04). Local presets (no traefik) stay lean — no auth stack needed.
    Scenario(
        name="core-rag",
        description="Dify RAG: inference stack + Qdrant + Dify API behind the Traefik edge "
        "with Authelia SSO.",
        services=(
            "dify-api",
            "llama-llm",
            "llama-embed",
            "llama-rerank",
            "qdrant",
            "traefik",
            "authelia",
            "redis",
        ),
    ),
    Scenario(
        name="core-ragflow",
        description="RAGFlow RAG: inference stack + Qdrant + RAGFlow behind the Traefik edge "
        "with Authelia SSO.",
        services=(
            "ragflow",
            "llama-llm",
            "llama-embed",
            "llama-rerank",
            "qdrant",
            "traefik",
            "authelia",
            "redis",
        ),
    ),
    Scenario(
        name="core-rag-ragflow",
        description="Both RAG engines (Dify + RAGFlow) on the shared inference + Qdrant stack.",
        services=(
            "dify-api",
            "ragflow",
            "llama-llm",
            "llama-embed",
            "llama-rerank",
            "qdrant",
            "traefik",
            "authelia",
            "redis",
        ),
    ),
    Scenario(
        name="rag-milvus",
        description="Dify + RAGFlow with the distributed Milvus vector store (etcd + minio).",
        services=(
            "dify-api",
            "ragflow",
            "milvus",
            "llama-llm",
            "llama-embed",
            "llama-rerank",
            "traefik",
            "authelia",
            "redis",
        ),
    ),
)


def list_scenarios() -> tuple[Scenario, ...]:
    """All operator deploy scenarios, in catalog order."""
    return SCENARIO_CATALOG


def scenario_names() -> list[str]:
    """Sorted scenario names (for error messages / --list)."""
    return sorted(s.name for s in SCENARIO_CATALOG)


def get_scenario(name: str) -> Scenario | None:
    """Look up a scenario by name (exact match), or None."""
    for scenario in SCENARIO_CATALOG:
        if scenario.name == name:
            return scenario
    return None
