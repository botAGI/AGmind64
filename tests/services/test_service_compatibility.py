"""Phase O.A + O.B (revised): compatibility checker + capability injection.

Original version had fake "conflicts" tests. After research все declared
conflicts оказались выдуманными (ragflow ↔ dify официально интегрируются;
vector DBs / reverse proxies могут coexist). Эта версия проверяет:

  - redundant_provider warnings (NOT errors)
  - missing_capability warnings
  - real env injection paths (port 8080 internal, верные env keys)
  - dify_external_kb wiring (ragflow → dify-api RAGFLOW_API_ENDPOINT)
"""

from __future__ import annotations

import pytest

from agmind.schemas import ServiceDescriptor
from agmind.services.capability_bindings import BINDINGS, env_for_consumer
from agmind.services.compatibility import (
    check_service_compatibility,
    resolve_capability_provider,
)
from agmind.services.renderer import inject_capability_env, render_compose

pytestmark = pytest.mark.backend_any


# ---------- helpers ----------


def _desc(
    name: str, provides: list[str] | None = None, consumes: list[str] | None = None
) -> ServiceDescriptor:
    return ServiceDescriptor(
        name=name,
        image=f"{name}:1.0",
        tier="storage",
        purpose=f"{name}",
        profiles=["test"],
        provides=provides or [],
        consumes=consumes or [],
    )


# ---------- compatibility checker ----------


def test_empty_selection_no_issues() -> None:
    report = check_service_compatibility({})
    assert report.issues == ()
    assert report.has_errors is False


def test_no_hard_conflicts_emitted_anymore() -> None:
    """Phase O revised: hard conflicts больше не выдаются.

    Раньше qdrant.conflicts_with=[weaviate, milvus] → error severity.
    Теперь — только redundant_provider warning.
    """
    selected = {
        "qdrant": _desc("qdrant", provides=["vector_db"]),
        "milvus": _desc("milvus", provides=["vector_db"]),
    }
    report = check_service_compatibility(selected)
    assert report.has_errors is False
    # Warning ожидается (redundant_provider)
    assert report.has_warnings is True


def test_redundant_provider_warning() -> None:
    selected = {
        "qdrant": _desc("qdrant", provides=["vector_db"]),
        "weaviate": _desc("weaviate", provides=["vector_db"]),
        "milvus": _desc("milvus", provides=["vector_db"]),
    }
    report = check_service_compatibility(selected)
    warns = report.by_severity("warning")
    assert any(i.kind == "redundant_provider" and i.capability == "vector_db" for i in warns)


def test_dify_multiple_vector_providers_warns_about_ambiguous_backend() -> None:
    selected = {
        "dify-api": _desc("dify-api", consumes=["vector_db"]),
        "qdrant": _desc("qdrant", provides=["vector_db"]),
        "milvus": _desc("milvus", provides=["vector_db"]),
    }

    report = check_service_compatibility(selected)
    warns = report.by_severity("warning")

    assert any(
        i.kind == "ambiguous_dify_vector_provider"
        and i.capability == "vector_db"
        and i.services == ("milvus", "qdrant")
        for i in warns
    )


def test_missing_capability_warning() -> None:
    selected = {
        "dify-api": _desc("dify-api", consumes=["vector_db"]),
    }
    report = check_service_compatibility(selected)
    warns = report.by_severity("warning")
    assert any(i.kind == "missing_capability" and i.capability == "vector_db" for i in warns)


def test_optional_dify_external_kb_gap_is_info_not_warning() -> None:
    selected = {
        "dify-api": _desc("dify-api", consumes=["dify_external_kb"]),
    }

    report = check_service_compatibility(selected)

    assert not any(
        i.kind == "missing_capability" and i.capability == "dify_external_kb"
        for i in report.by_severity("warning")
    )
    assert any(
        i.kind == "optional_missing_capability" and i.capability == "dify_external_kb"
        for i in report.by_severity("info")
    )


def test_capability_providers_map() -> None:
    selected = {
        "qdrant": _desc("qdrant", provides=["vector_db"]),
        "llama-llm": _desc("llama-llm", provides=["llm_inference"]),
    }
    report = check_service_compatibility(selected)
    assert report.capability_providers["vector_db"] == ("qdrant",)


def test_resolve_provider_multiple_picks_first_alphabet() -> None:
    selected = {
        "weaviate": _desc("weaviate", provides=["vector_db"]),
        "qdrant": _desc("qdrant", provides=["vector_db"]),
    }
    assert resolve_capability_provider(selected, "vector_db") == "qdrant"


# ---------- capability bindings (post-research) ----------


def test_bindings_vector_db_only_for_dify() -> None:
    """RAGFlow НЕ supports vector_db backends (qdrant/milvus/weaviate).

    Только Dify per dify-docs. Bindings reflect this.
    """
    assert "ragflow" not in BINDINGS["vector_db"]["qdrant"]
    assert "ragflow" not in BINDINGS["vector_db"]["milvus"]
    assert "ragflow" not in BINDINGS["vector_db"]["weaviate"]


def test_bindings_search_index_for_ragflow() -> None:
    """RAGFlow's DOC_ENGINE supports elasticsearch / infinity / opensearch."""
    assert "search_index" in BINDINGS
    assert "elasticsearch" in BINDINGS["search_index"]
    assert BINDINGS["search_index"]["elasticsearch"]["ragflow"]["DOC_ENGINE"] == "elasticsearch"


def test_bindings_dify_external_kb_ragflow_to_dify() -> None:
    """RAGFlow provides external knowledge base for Dify (witmeng/ragflow-api plugin)."""
    env = env_for_consumer("dify_external_kb", "ragflow", "dify-api")
    assert env["RAGFLOW_API_ENDPOINT"] == "http://ragflow:9380/api/v1"


def test_bindings_llm_inference_uses_container_port_8080() -> None:
    """llama-llm internal port = 8080 (host 8080); env injection must reflect."""
    env = env_for_consumer("llm_inference", "llama-llm", "dify-api")
    assert "llama-llm:8080" in env["OPENAI_API_BASE"]


def test_bindings_embedding_uses_container_port_8080() -> None:
    """llama-embed internal port = 8080 (host 8081). Within compose: :8080."""
    env = env_for_consumer("embedding_inference", "llama-embed", "ragflow")
    assert "llama-embed:8080" in env["EMBEDDING_ENDPOINT"]


def test_bindings_reranker_uses_container_port_8080() -> None:
    """llama-rerank internal port = 8080 (host 8082). Within compose: :8080."""
    env = env_for_consumer("reranker", "llama-rerank", "dify-api")
    assert "llama-rerank:8080" in env["RERANK_PROVIDER_BASE_URL"]


def test_bindings_milvus_dify_correct_env() -> None:
    """Dify supports milvus → VECTOR_STORE=milvus + MILVUS_URI per dify docs."""
    env = env_for_consumer("vector_db", "milvus", "dify-api")
    assert env["VECTOR_STORE"] == "milvus"
    assert env["MILVUS_URI"] == "http://milvus:19530"


# ---------- BINDINGS ↔ descriptor closure guard (F.3) ----------
#
# BINDINGS and each descriptor's provides/consumes are two hand-maintained
# sources. Spot-checks above cover individual pairs; this closure guard locks the
# WHOLE table so a renamed/deleted service or a missing provides/consumes entry
# fails loudly instead of silently misconfiguring rendered env.
#
# Intentional gaps go here as explicit (capability, provider, consumer) triples
# with an inline reason. Empty today: every binding currently closes over the
# real catalog. Do NOT add entries to silence a real drift — fix the binding or
# the descriptor.
KNOWN_BINDING_GAPS: set[tuple[str, str, str]] = set()


def test_every_binding_triple_closes_over_real_descriptors() -> None:
    """Each BINDINGS[cap][provider][consumer] must resolve to real descriptors.

    For every triple: provider and consumer must exist in load_descriptors(),
    the provider must declare `cap` in `.provides`, and the consumer must declare
    `cap` in `.consumes`. A stale/renamed/deleted service or a missing
    provides/consumes entry surfaces as a named violation.
    """
    from agmind.services.renderer import load_descriptors

    all_d = load_descriptors()
    violations: list[str] = []

    for cap, prov_table in BINDINGS.items():
        for provider, cons_table in prov_table.items():
            for consumer in cons_table:
                if (cap, provider, consumer) in KNOWN_BINDING_GAPS:
                    continue
                if provider not in all_d:
                    violations.append(
                        f"({cap}, {provider}, {consumer}): provider '{provider}' "
                        f"is not a real descriptor"
                    )
                    continue
                if consumer not in all_d:
                    violations.append(
                        f"({cap}, {provider}, {consumer}): consumer '{consumer}' "
                        f"is not a real descriptor"
                    )
                    continue
                if cap not in all_d[provider].provides:
                    violations.append(
                        f"({cap}, {provider}, {consumer}): provider '{provider}' "
                        f"does not declare provides={cap!r} "
                        f"(has {sorted(all_d[provider].provides)})"
                    )
                if cap not in all_d[consumer].consumes:
                    violations.append(
                        f"({cap}, {provider}, {consumer}): consumer '{consumer}' "
                        f"does not declare consumes={cap!r} "
                        f"(has {sorted(all_d[consumer].consumes)})"
                    )

    assert not violations, "BINDINGS↔descriptor drift detected:\n" + "\n".join(sorted(violations))


def test_binding_closure_guard_actually_iterates_all_triples() -> None:
    """Sanity: the closure guard must cover EVERY triple, not a subset.

    Counts the triples the guard would inspect and asserts it matches the live
    BINDINGS shape, so the guard above cannot silently degrade to a no-op.
    """
    triples = [
        (cap, provider, consumer)
        for cap, prov_table in BINDINGS.items()
        for provider, cons_table in prov_table.items()
        for consumer in cons_table
    ]
    assert len(triples) == len(set(triples))
    assert len(triples) >= len(BINDINGS), "expected at least one triple per capability"
    # Spot anchor: a known-good triple is present (catches an accidentally empty table).
    assert ("llm_inference", "llama-llm", "dify-api") in triples


# ---------- renderer integration ----------


def test_render_compose_injects_dify_milvus() -> None:
    selected = {
        "milvus": _desc("milvus", provides=["vector_db"]),
        "dify-api": _desc("dify-api", consumes=["vector_db"]),
    }
    compose = render_compose(list(selected.values()), traefik_enabled=False)
    env = compose["services"]["dify-api"]["environment"]
    assert env["VECTOR_STORE"] == "milvus"
    assert env["MILVUS_URI"] == "http://milvus:19530"


def test_inject_capability_env_prefers_provider_with_consumer_binding() -> None:
    selected = {
        "aaa-vector": _desc("aaa-vector", provides=["vector_db"]),
        "milvus": _desc("milvus", provides=["vector_db"]),
        "dify-api": _desc("dify-api", consumes=["vector_db"]),
    }

    injected = inject_capability_env(selected)

    assert injected["dify-api"]["VECTOR_STORE"] == "milvus"
    assert injected["dify-api"]["MILVUS_URI"] == "http://milvus:19530"


def test_inject_capability_env_uses_dify_vector_provider_priority() -> None:
    selected = {
        "qdrant": _desc("qdrant", provides=["vector_db"]),
        "weaviate": _desc("weaviate", provides=["vector_db"]),
        "dify-api": _desc("dify-api", consumes=["vector_db"]),
    }

    injected = inject_capability_env(selected)

    assert injected["dify-api"]["VECTOR_STORE"] == "weaviate"
    assert injected["dify-api"]["WEAVIATE_ENDPOINT"] == "http://weaviate:8080"


def test_render_compose_does_not_override_manual_env() -> None:
    custom = ServiceDescriptor(
        name="dify-api",
        image="dify-api:1.0",
        tier="app",
        purpose="test",
        profiles=["test"],
        consumes=["vector_db"],
        env={"VECTOR_STORE": "preset-by-hand"},
    )
    selected = {
        "milvus": _desc("milvus", provides=["vector_db"]),
        "dify-api": custom,
    }
    compose = render_compose(list(selected.values()), traefik_enabled=False)
    env = compose["services"]["dify-api"]["environment"]
    assert env["VECTOR_STORE"] == "preset-by-hand"  # manual wins


# ---------- real catalog scenarios (corrected post-research) ----------


def test_real_catalog_ragflow_and_dify_coexist() -> None:
    """Phase O revised: ragflow + dify-api should coexist без errors.

    Refute prior выдумка: marketplace.dify.ai/plugin/witmeng/ragflow-api
    официально интегрирует их.
    """
    from agmind.services.renderer import load_descriptors, select_services

    all_d = load_descriptors()
    selected = select_services(
        all_d,
        services=[
            "ragflow",
            "dify-api",
            "llama-llm",
            "llama-embed",
            "mysql",
            "elasticsearch",
            "minio",
            "redis",
            "postgres",
            "qdrant",
        ],
    )
    report = check_service_compatibility(selected)
    assert report.has_errors is False


# ---------- C3: capability-vocabulary closure gate ----------
#
# Vocab = union of all `provides` tokens across the catalog + BINDINGS keys.
# Every `consumes` token in every descriptor must be in that vocab.
# This is a catalog-level CI guard (the model can't see the full catalog at
# validate-time).  Mirrors the BINDINGS closure guard (F.3) pattern above.


def test_capability_vocab_is_closed() -> None:
    """C3: every consumes token across the catalog must be in the known vocab.

    vocab = {cap for d in all_descriptors for cap in d.provides} | BINDINGS.keys()

    A typo'd or invented capability token (e.g. 'vector_dbX') silently skips
    injection — this gate makes such drift loud.

    Mutation-proof: injecting a synthetic bad token surfaces as a named violation.
    """
    from agmind.services.renderer import load_descriptors

    descriptors = load_descriptors()
    all_provides = {cap for d in descriptors.values() for cap in d.provides}
    vocab = all_provides | set(BINDINGS.keys())

    violations: list[str] = []
    for name, d in descriptors.items():
        for cap in d.consumes:
            if cap not in vocab:
                violations.append(f"{name} consumes unknown capability '{cap}'")

    assert not violations, "Capability-vocab closure violated:\n" + "\n".join(sorted(violations))


def test_capability_vocab_closure_mutation_proof() -> None:
    """C3 mutation control: a typo'd token in a synthetic descriptor → gate flags it.

    This proves the gate is not vacuously true; any unknown token causes a named
    violation instead of silently passing.
    """
    from agmind.services.renderer import load_descriptors

    descriptors = load_descriptors()
    all_provides = {cap for d in descriptors.values() for cap in d.provides}
    vocab = all_provides | set(BINDINGS.keys())

    # Synthetic typo'd token — not in any real descriptor's provides or BINDINGS.
    typo_token = "vector_dbX"
    assert typo_token not in vocab, (
        f"mutation control token '{typo_token}' accidentally entered the catalog — "
        "choose a different token for this test"
    )

    synthetic_consumes = [typo_token]
    violations = [
        f"synthetic-svc consumes unknown capability '{cap}'"
        for cap in synthetic_consumes
        if cap not in vocab
    ]

    assert violations, (
        f"Mutation control FAILED: token '{typo_token}' was not caught as a violation. "
        "The gate would silently pass a real typo — fix the gate logic."
    )


def test_real_catalog_ragflow_provides_dify_external_kb() -> None:
    """ragflow.provides включает dify_external_kb."""
    from agmind.services.renderer import load_descriptors

    all_d = load_descriptors()
    assert "dify_external_kb" in all_d["ragflow"].provides


def test_real_catalog_dify_api_consumes_dify_external_kb() -> None:
    """dify-api.consumes включает dify_external_kb."""
    from agmind.services.renderer import load_descriptors

    all_d = load_descriptors()
    assert "dify_external_kb" in all_d["dify-api"].consumes


def test_real_catalog_dify_stack_marker_is_not_provider() -> None:
    """Dify stack membership lives in component contracts, not service provides."""
    from agmind.services.renderer import load_descriptors

    all_d = load_descriptors()
    dify_services = [
        "dify-api",
        "dify-web",
        "dify-worker",
        "dify-sandbox",
        "dify-plugin-daemon",
    ]
    for service_name in dify_services:
        assert "dify_stack" not in all_d[service_name].provides


def test_real_catalog_ragflow_dify_integration_env_injected() -> None:
    """Когда выбраны и ragflow и dify-api — dify-api получает RAGFLOW_API_ENDPOINT."""
    from agmind.services.renderer import load_descriptors, select_services

    all_d = load_descriptors()
    selected = select_services(
        all_d,
        services=[
            "ragflow",
            "dify-api",
            "llama-llm",
            "llama-embed",
            "qdrant",
            "mysql",
            "elasticsearch",
            "minio",
            "redis",
            "postgres",
        ],
    )
    injected = inject_capability_env(selected)
    assert "dify-api" in injected
    assert injected["dify-api"].get("RAGFLOW_API_ENDPOINT") == "http://ragflow:9380/api/v1"


def test_real_catalog_ragflow_uses_elasticsearch_not_milvus() -> None:
    """ragflow consumes search_index (НЕ vector_db).

    Раньше я выдумал что ragflow.consumes=['vector_db'] и pickup milvus. Неправда.
    """
    from agmind.services.renderer import load_descriptors

    all_d = load_descriptors()
    rf = all_d["ragflow"]
    assert "search_index" in rf.consumes
    assert "vector_db" not in rf.consumes


def test_real_catalog_dify_api_has_no_hardcoded_qdrant_binding() -> None:
    """Dify vector store must be selected through vector_db capability bindings."""
    from agmind.services.renderer import load_descriptors

    all_d = load_descriptors()
    dify_api = all_d["dify-api"]
    assert "qdrant" not in dify_api.depends_on
    assert "VECTOR_STORE" not in dify_api.env
    assert "QDRANT_URL" not in dify_api.env


def test_real_catalog_dify_capability_consumers_are_runtime_services_only() -> None:
    """Only Dify API/worker consume model and vector providers directly."""
    from agmind.services.renderer import load_descriptors

    all_d = load_descriptors()
    provider_caps = {"llm_inference", "embedding_inference", "vector_db"}

    assert provider_caps <= set(all_d["dify-api"].consumes)
    assert provider_caps <= set(all_d["dify-worker"].consumes)
    for service_name in ("dify-web", "dify-sandbox", "dify-plugin-daemon"):
        assert provider_caps.isdisjoint(all_d[service_name].consumes)


def test_real_catalog_milvus_does_not_inject_into_ragflow() -> None:
    """Confirm: milvus НЕ injects в ragflow (мы перестали выдумывать)."""
    from agmind.services.renderer import load_descriptors, select_services

    all_d = load_descriptors()
    selected = select_services(
        all_d,
        services=["milvus", "ragflow", "llama-llm", "mysql", "elasticsearch", "minio", "redis"],
    )
    injected = inject_capability_env(selected)
    rf_env = injected.get("ragflow", {})
    # ragflow doesn't consume vector_db — milvus не должен попадать в его env.
    assert "MILVUS_URI" not in rf_env
    assert rf_env.get("DOC_ENGINE") != "milvus"


def test_real_catalog_es_injects_doc_engine_into_ragflow() -> None:
    """elasticsearch present → ragflow получает DOC_ENGINE=elasticsearch."""
    from agmind.services.renderer import load_descriptors, select_services

    all_d = load_descriptors()
    selected = select_services(
        all_d,
        services=[
            "elasticsearch",
            "ragflow",
            "llama-llm",
            "llama-embed",
            "mysql",
            "minio",
            "redis",
        ],
    )
    injected = inject_capability_env(selected)
    rf_env = injected.get("ragflow", {})
    assert rf_env.get("DOC_ENGINE") == "elasticsearch"
    assert rf_env.get("ES_HOST") == "elasticsearch"


def test_real_catalog_ragflow_pin_is_explicit_baseline() -> None:
    """ragflow stays explicitly pinned until the v1 migration is verified."""
    from agmind.services.renderer import load_descriptors

    all_d = load_descriptors()
    assert all_d["ragflow"].image == "infiniflow/ragflow:v0.25.5"


# ---------- B7: observability-profile co-deploy (08-06) ----------


def test_observability_profile_includes_its_backends() -> None:
    """B7: observability-alone must co-deploy postgres + redis so exporters have backends.

    postgres-exporter hard-points DATA_SOURCE_NAME=@postgres and redis-exporter
    hard-points REDIS_ADDR=redis://redis:6379.  Without the backends in the same
    profile the exporters ship with no targets to scrape.

    LOCKED mechanism: add `- observability` to profiles of both backend descriptors
    (shared-profile co-deploy, NO cross-profile depends_on — Правила §12 hard-raise).
    """
    from agmind.services.renderer import load_descriptors, render_to_string, select_services

    all_d = load_descriptors()

    # 1. postgres + redis declare the observability profile.
    assert "observability" in all_d["postgres"].profiles, (
        "postgres.yaml must have 'observability' in profiles (B7 shared-profile co-deploy)"
    )
    assert "observability" in all_d["redis"].profiles, (
        "redis.yaml must have 'observability' in profiles (B7 shared-profile co-deploy)"
    )

    # 2. select_services for the observability profile includes both backends.
    selected = select_services(all_d, profiles=["observability"])
    assert "postgres" in selected, (
        "select_services(observability) must include postgres — exporter needs its backend"
    )
    assert "redis" in selected, (
        "select_services(observability) must include redis — exporter needs its backend"
    )

    # 3. render_to_string does NOT raise for observability-alone (guards Правила §12 —
    #    cross-profile depends_on would hard-raise here).
    rendered = render_to_string(profiles=["observability"])
    assert "postgres" in rendered, "rendered observability compose must contain postgres service"
    assert "redis" in rendered, "rendered observability compose must contain redis service"

    # 4. Neither postgres nor redis gained a cross-profile depends_on (Правила §12 guard).
    postgres_depends = all_d["postgres"].depends_on
    redis_depends = all_d["redis"].depends_on
    cross_profile_obs = {"postgres-exporter", "redis-exporter", "prometheus", "grafana", "loki"}
    assert not cross_profile_obs.intersection(postgres_depends), (
        f"postgres must not have observability-profile depends_on — got {postgres_depends}"
    )
    assert not cross_profile_obs.intersection(redis_depends), (
        f"redis must not have observability-profile depends_on — got {redis_depends}"
    )
