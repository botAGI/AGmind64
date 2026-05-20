"""Phase O.A + O.B: tests for service compatibility + capability injection."""

from __future__ import annotations

import pytest

from agmind.schemas import ServiceDescriptor
from agmind.services.capability_bindings import BINDINGS, env_for_consumer
from agmind.services.compatibility import (
    check_service_compatibility,
    resolve_capability_provider,
)
from agmind.services.renderer import inject_capability_env, load_descriptors, render_compose

pytestmark = pytest.mark.backend_any


# ---------- helpers ----------


def _desc(name: str, provides: list[str] | None = None,
          conflicts: list[str] | None = None,
          consumes: list[str] | None = None) -> ServiceDescriptor:
    return ServiceDescriptor(
        name=name, image=f"{name}:1.0", tier="storage", purpose=f"{name}",
        profiles=["test"],
        provides=provides or [], conflicts_with=conflicts or [],
        consumes=consumes or [],
    )


# ---------- compatibility checker ----------


def test_no_issues_for_empty_set() -> None:
    report = check_service_compatibility({})
    assert report.issues == ()
    assert report.has_errors is False
    assert report.has_warnings is False


def test_detects_hard_conflict() -> None:
    selected = {
        "qdrant": _desc("qdrant", provides=["vector_db"], conflicts=["weaviate"]),
        "weaviate": _desc("weaviate", provides=["vector_db"], conflicts=["qdrant"]),
    }
    report = check_service_compatibility(selected)
    errors = report.by_severity("error")
    assert len(errors) == 1  # одна pair (sorted), не дубликат
    assert errors[0].kind == "conflict"
    assert set(errors[0].services) == {"qdrant", "weaviate"}


def test_no_conflict_if_one_side_absent() -> None:
    selected = {
        "qdrant": _desc("qdrant", provides=["vector_db"], conflicts=["weaviate"]),
    }
    report = check_service_compatibility(selected)
    assert report.by_severity("error") == ()


def test_redundant_provider_warning() -> None:
    selected = {
        "qdrant": _desc("qdrant", provides=["vector_db"]),
        "weaviate": _desc("weaviate", provides=["vector_db"]),
        "milvus": _desc("milvus", provides=["vector_db"]),
    }
    report = check_service_compatibility(selected)
    warns = report.by_severity("warning")
    assert any(i.kind == "redundant_provider" and i.capability == "vector_db" for i in warns)


def test_missing_capability_warning() -> None:
    selected = {
        "dify-api": _desc("dify-api", consumes=["vector_db"]),
    }
    report = check_service_compatibility(selected)
    warns = report.by_severity("warning")
    assert any(i.kind == "missing_capability" and i.capability == "vector_db" for i in warns)


def test_capability_providers_map() -> None:
    selected = {
        "qdrant": _desc("qdrant", provides=["vector_db"]),
        "llama-llm": _desc("llama-llm", provides=["llm_inference"]),
    }
    report = check_service_compatibility(selected)
    assert report.capability_providers["vector_db"] == ("qdrant",)
    assert report.capability_providers["llm_inference"] == ("llama-llm",)


def test_resolve_provider_single() -> None:
    selected = {"qdrant": _desc("qdrant", provides=["vector_db"])}
    assert resolve_capability_provider(selected, "vector_db") == "qdrant"


def test_resolve_provider_none() -> None:
    assert resolve_capability_provider({}, "vector_db") is None


def test_resolve_provider_multiple_picks_first_alphabet() -> None:
    selected = {
        "weaviate": _desc("weaviate", provides=["vector_db"]),
        "qdrant": _desc("qdrant", provides=["vector_db"]),
    }
    # sorted alphabetically: qdrant first
    assert resolve_capability_provider(selected, "vector_db") == "qdrant"


# ---------- capability bindings table ----------


def test_bindings_have_vector_db_entries() -> None:
    assert "vector_db" in BINDINGS
    assert "qdrant" in BINDINGS["vector_db"]
    assert "milvus" in BINDINGS["vector_db"]


def test_env_for_consumer_known_pair() -> None:
    env = env_for_consumer("vector_db", "milvus", "dify-api")
    assert env["VECTOR_STORE"] == "milvus"
    assert "MILVUS_URI" in env


def test_env_for_consumer_unknown_pair_returns_empty() -> None:
    assert env_for_consumer("nope", "nope", "nope") == {}
    assert env_for_consumer("vector_db", "qdrant", "unknown-consumer") == {}


def test_env_for_consumer_ragflow_milvus() -> None:
    env = env_for_consumer("vector_db", "milvus", "ragflow")
    assert env["DOC_ENGINE"] == "milvus"


def test_env_for_consumer_llm_inference() -> None:
    env = env_for_consumer("llm_inference", "llama-llm", "openwebui")
    assert "OPENAI_API_BASE_URL" in env
    assert "llama-llm" in env["OPENAI_API_BASE_URL"]


# ---------- renderer integration ----------


def test_inject_capability_env_no_consumers() -> None:
    selected = {
        "qdrant": _desc("qdrant", provides=["vector_db"]),
    }
    assert inject_capability_env(selected) == {}


def test_inject_capability_env_routes_provider_to_consumer() -> None:
    selected = {
        "milvus": _desc("milvus", provides=["vector_db"]),
        "ragflow": _desc("ragflow", consumes=["vector_db"]),
    }
    out = inject_capability_env(selected)
    assert "ragflow" in out
    assert out["ragflow"]["DOC_ENGINE"] == "milvus"
    assert out["ragflow"]["MILVUS_URI"] == "http://milvus:19530"


def test_inject_skips_missing_provider() -> None:
    selected = {
        "ragflow": _desc("ragflow", consumes=["vector_db"]),
    }
    out = inject_capability_env(selected)
    # No provider — no env injected.
    assert out.get("ragflow", {}) == {}


def test_render_compose_merges_capability_env() -> None:
    """End-to-end: compose YAML содержит injected env под consumer."""
    selected = {
        "milvus": _desc("milvus", provides=["vector_db"]),
        "dify-api": _desc("dify-api", consumes=["vector_db"]),
    }
    compose = render_compose(list(selected.values()), traefik_enabled=False)
    dify_env = compose["services"]["dify-api"].get("environment", {})
    assert isinstance(dify_env, dict)
    assert dify_env.get("VECTOR_STORE") == "milvus"
    assert "MILVUS_URI" in dify_env


def test_render_compose_does_not_override_manual_env() -> None:
    """Если у consumer есть свой env value — capability injection не должен перетереть."""
    custom = ServiceDescriptor(
        name="dify-api", image="dify-api:1.0", tier="app", purpose="test",
        profiles=["test"], consumes=["vector_db"],
        env={"VECTOR_STORE": "preset-by-hand"},
    )
    selected = {
        "milvus": _desc("milvus", provides=["vector_db"]),
        "dify-api": custom,
    }
    compose = render_compose(list(selected.values()), traefik_enabled=False)
    env = compose["services"]["dify-api"]["environment"]
    assert env["VECTOR_STORE"] == "preset-by-hand"  # manual wins


# ---------- real catalog smoke ----------


def test_real_catalog_qdrant_weaviate_milvus_conflict() -> None:
    """Sanity: реальные descriptors detect collision если выбрать все 3 vector DB."""
    from agmind.services.renderer import load_descriptors, select_services
    all_d = load_descriptors()
    selected = select_services(all_d, services=["qdrant", "weaviate", "milvus"])
    report = check_service_compatibility(selected)
    assert report.has_errors  # qdrant.conflicts_with(weaviate, milvus) и так далее


def test_real_catalog_ragflow_vs_dify_conflict() -> None:
    """ragflow.conflicts_with(dify-*) — выбрать оба = error."""
    from agmind.services.renderer import load_descriptors, select_services
    all_d = load_descriptors()
    selected = select_services(all_d, services=["ragflow", "dify-api"])
    report = check_service_compatibility(selected)
    assert report.has_errors


def test_real_catalog_milvus_in_ragflow() -> None:
    """User scenario: ragflow + milvus → должно работать без conflicts (только missing checks)."""
    from agmind.services.renderer import load_descriptors, select_services
    all_d = load_descriptors()
    # Реальный production minimum: llm + embed + milvus + ragflow
    selected = select_services(
        all_d, services=["llama-llm", "llama-embed", "milvus", "ragflow"],
    )
    report = check_service_compatibility(selected)
    # Не должно быть conflicts (only warnings из-за ragflow.consumes[reranker] e.g.)
    assert not report.has_errors


def test_real_catalog_milvus_injects_into_ragflow() -> None:
    """User-asked feature: выбрал ragflow+milvus → ragflow видит milvus."""
    from agmind.services.renderer import load_descriptors, select_services
    all_d = load_descriptors()
    selected = select_services(
        all_d, services=["milvus", "ragflow", "llama-llm", "llama-embed", "mysql",
                         "elasticsearch", "minio", "redis"],
    )
    out = inject_capability_env(selected)
    assert "ragflow" in out
    assert out["ragflow"].get("DOC_ENGINE") == "milvus"


def test_real_catalog_reverse_proxy_conflict() -> None:
    """traefik + caddy = conflict."""
    from agmind.services.renderer import load_descriptors, select_services
    all_d = load_descriptors()
    selected = select_services(all_d, services=["traefik", "caddy"])
    report = check_service_compatibility(selected)
    assert report.has_errors
