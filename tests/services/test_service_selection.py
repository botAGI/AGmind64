from __future__ import annotations

import pytest

pytestmark = pytest.mark.backend_any


def test_service_selection_rejects_unknown_service_names() -> None:
    from agmind.services.renderer import load_descriptors
    from agmind.services.selection import resolve_service_selection

    descriptors = load_descriptors()
    with pytest.raises(ValueError, match="unknown services: missing-service"):
        resolve_service_selection(descriptors, services=["traefik", "missing-service"])


def test_selection_resolves_consumed_capability_provider() -> None:
    """Review MEDIUM selection-consumes-not-resolved: selecting openwebui (consumes
    llm_inference, pulled by no _stack) must carry its provider llama-llm — else render wires
    OPENAI_API_BASE_URL at an absent host."""
    from agmind.services.renderer import load_descriptors
    from agmind.services.selection import resolve_service_selection

    descriptors = load_descriptors()
    closure = resolve_service_selection(descriptors, services=["openwebui"])
    assert "llama-llm" in closure, "consumed llm_inference provider must be in the closure"


def test_selecting_prometheus_pulls_docker_socket_proxy() -> None:
    """live-audit 2026-06-05 (grafana-empty): prometheus docker_sd discovers targets over
    tcp://docker-socket-proxy:2375; the proxy is otherwise observability-profile-only, so an
    EXPLICIT-services selection (the operator's setup-state path) deployed prometheus WITHOUT
    it -> 0 targets -> empty Grafana. Selecting prometheus must now pull the proxy into the
    closure via the docker_api capability so it always co-deploys."""
    from agmind.services.renderer import load_descriptors
    from agmind.services.selection import resolve_service_selection

    descriptors = load_descriptors()
    closure = resolve_service_selection(descriptors, services=["prometheus"])
    assert "docker-socket-proxy" in closure, "prometheus must pull its docker-socket-proxy"


def test_docker_api_consume_is_derived_closure_pulled() -> None:
    """A raw per-service render of a docker_api consumer (prometheus/traefik/cadvisor/...) must NOT
    fail-closed — the sole provider (docker-socket-proxy) is closure-pulled. de-slop 2026-06-07
    SLOP-H3: this is now DERIVED via CLOSURE_PULLED_CAPABILITIES, not 6 hand-listed pairs."""
    from agmind.services.renderer import load_descriptors
    from agmind.services.topology_checks import (
        CLOSURE_PULLED_CAPABILITIES,
        KNOWN_CROSS_PROFILE_CONSUMES,
    )

    assert "docker_api" in CLOSURE_PULLED_CAPABILITIES
    # the derivation replaced the per-consumer pairs — none remain hand-listed
    assert not any(cap == "docker_api" for _, cap in KNOWN_CROSS_PROFILE_CONSUMES)
    # docker-socket-proxy is genuinely the sole provider (what makes the derivation sound)
    providers = [n for n, d in load_descriptors().items() if "docker_api" in d.provides]
    assert providers == ["docker-socket-proxy"]


def test_llm_endpoint_absent_when_llama_llm_not_in_render_set() -> None:
    """live-audit 2026-06-05 (HIGH dangling-llama-llm): openwebui/ragflow must NOT hardcode an
    http://llama-llm endpoint in their static env. When the LLM is skipped (no llama-llm in the
    rendered set) the capability binding omits the endpoint, so the service carries no dead DNS
    reference (chat/generation degrade cleanly instead of pointing at an unresolvable host)."""
    from agmind.services.renderer import descriptors_with_capability_env, load_descriptors

    ds = load_descriptors()
    subset = [ds[n] for n in ("openwebui", "ragflow") if n in ds]
    resolved = {d.name: d for d in descriptors_with_capability_env(subset)}
    assert "OPENAI_API_BASE_URL" not in resolved["openwebui"].env
    assert "VLM_ENDPOINT" not in resolved["ragflow"].env


def test_llm_endpoint_injected_when_llama_llm_present() -> None:
    """With llama-llm in the rendered set the binding injects the endpoint (with-model case is
    byte-identical to before — the hardcode used to duplicate exactly this value)."""
    from agmind.services.renderer import descriptors_with_capability_env, load_descriptors

    ds = load_descriptors()
    subset = [ds[n] for n in ("openwebui", "ragflow", "llama-llm") if n in ds]
    resolved = {d.name: d for d in descriptors_with_capability_env(subset)}
    assert resolved["openwebui"].env.get("OPENAI_API_BASE_URL") == "http://llama-llm:8080/v1"
    assert resolved["ragflow"].env.get("VLM_ENDPOINT") == "http://llama-llm:8080/v1"


def test_selection_does_not_overpull_optional_capabilities() -> None:
    """The consumes walk must SKIP OPTIONAL_MISSING_CAPABILITIES (reranker / dify_external_kb)
    so a dify/ragflow closure does not silently drag in its optional providers."""
    from agmind.services.compatibility import OPTIONAL_MISSING_CAPABILITIES
    from agmind.services.renderer import load_descriptors
    from agmind.services.selection import resolve_service_selection

    descriptors = load_descriptors()
    closure = resolve_service_selection(descriptors, services=["ragflow"])
    # ragflow consumes the optional `reranker` capability — llama-rerank must NOT be force-pulled.
    assert "reranker" in OPTIONAL_MISSING_CAPABILITIES
    assert "llama-rerank" not in closure


def test_service_selection_expands_dify_stack_and_mandatory_runtime_dependencies() -> None:
    from agmind.components import load_component_contracts
    from agmind.services.compatibility import check_service_compatibility
    from agmind.services.renderer import check_missing_dependencies, load_descriptors
    from agmind.services.selection import resolve_service_selection

    descriptors = load_descriptors()
    selected = resolve_service_selection(
        descriptors,
        services=["dify-api"],
        component_contracts=load_component_contracts(),
    )

    assert {
        "dify-api",
        "dify-web",
        "dify-worker",
        "dify-plugin-daemon",
        "dify-sandbox",
        "postgres",
        "redis",
        "qdrant",
        "llama-llm",
        "llama-embed",
    } <= set(selected)
    assert "ragflow" not in selected
    assert check_missing_dependencies(selected, descriptors) == {}

    missing_capabilities = {
        issue.capability
        for issue in check_service_compatibility(selected).by_severity("warning")
        if issue.kind == "missing_capability"
    }
    assert missing_capabilities & {"llm_inference", "embedding_inference", "vector_db"} == set()
    assert missing_capabilities <= {"dify_external_kb"}


def test_service_selection_uses_explicit_milvus_for_dify_without_qdrant() -> None:
    from agmind.components import load_component_contracts
    from agmind.services.renderer import (
        check_missing_dependencies,
        load_descriptors,
        render_compose,
    )
    from agmind.services.selection import resolve_service_selection

    descriptors = load_descriptors()
    selected = resolve_service_selection(
        descriptors,
        services=["dify-api", "milvus"],
        component_contracts=load_component_contracts(),
    )

    assert "milvus" in selected
    assert "qdrant" not in selected
    assert check_missing_dependencies(selected, descriptors) == {}

    compose = render_compose(list(selected.values()), traefik_enabled=False)
    env = compose["services"]["dify-api"]["environment"]
    assert env["VECTOR_STORE"] == "milvus"
    assert env["MILVUS_URI"] == "http://milvus:19530"
    assert "QDRANT_URL" not in env


def test_service_selection_dify_ragflow_milvus_keeps_ragflow_on_search_index() -> None:
    from agmind.components import load_component_contracts
    from agmind.services.renderer import (
        check_missing_dependencies,
        load_descriptors,
        render_compose,
    )
    from agmind.services.selection import resolve_service_selection

    descriptors = load_descriptors()
    selected = resolve_service_selection(
        descriptors,
        services=["dify-api", "ragflow", "milvus"],
        component_contracts=load_component_contracts(),
    )

    assert "milvus" in selected
    assert "qdrant" not in selected
    assert "ragflow" in selected
    assert "elasticsearch" in selected
    assert check_missing_dependencies(selected, descriptors) == {}

    compose = render_compose(list(selected.values()), traefik_enabled=False)
    dify_env = compose["services"]["dify-api"]["environment"]
    ragflow_env = compose["services"]["ragflow"]["environment"]

    assert dify_env["VECTOR_STORE"] == "milvus"
    assert dify_env["MILVUS_URI"] == "http://milvus:19530"
    assert ragflow_env["DOC_ENGINE"] == "elasticsearch"
    assert ragflow_env["ES_HOST"] == "elasticsearch"
    assert "MILVUS_URI" not in ragflow_env


def test_service_selection_ragflow_renders_explicit_runtime_dependencies() -> None:
    from agmind.components import load_component_contracts
    from agmind.services.renderer import load_descriptors, render_compose
    from agmind.services.selection import resolve_service_selection

    descriptors = load_descriptors()
    selected = resolve_service_selection(
        descriptors,
        services=["ragflow"],
        component_contracts=load_component_contracts(),
    )

    compose = render_compose(list(selected.values()), traefik_enabled=False)
    ragflow_env = compose["services"]["ragflow"]["environment"]
    mysql_env = compose["services"]["mysql"]["environment"]

    assert mysql_env["MYSQL_DATABASE"] == "rag_flow"
    assert ragflow_env["MYSQL_HOST"] == "mysql"
    assert ragflow_env["MYSQL_PORT"] == "3306"
    assert ragflow_env["MYSQL_DBNAME"] == "rag_flow"
    assert (
        ragflow_env["MYSQL_PASSWORD"] == "${MYSQL_ROOT_PASSWORD:?MYSQL_ROOT_PASSWORD is required}"
    )
    assert ragflow_env["MINIO_HOST"] == "minio"
    assert ragflow_env["MINIO_PORT"] == "9000"
    assert ragflow_env["MINIO_USER"] == "${MINIO_ROOT_USER:?MINIO_ROOT_USER is required}"
    assert (
        ragflow_env["MINIO_PASSWORD"] == "${MINIO_ROOT_PASSWORD:?MINIO_ROOT_PASSWORD is required}"
    )
    assert ragflow_env["REDIS_HOST"] == "redis"
    assert ragflow_env["REDIS_PORT"] == "6379"
    assert ragflow_env["REDIS_PASSWORD"] == "${REDIS_PASSWORD:?REDIS_PASSWORD is required}"
    assert ragflow_env["DOC_ENGINE"] == "elasticsearch"
    assert ragflow_env["ES_HOST"] == "elasticsearch"


def test_service_selection_renders_reranker_env_when_reranker_is_selected() -> None:
    from agmind.components import load_component_contracts
    from agmind.services.renderer import load_descriptors, render_compose
    from agmind.services.selection import resolve_service_selection

    descriptors = load_descriptors()
    selected = resolve_service_selection(
        descriptors,
        services=["dify-api", "ragflow", "llama-rerank"],
        component_contracts=load_component_contracts(),
    )

    compose = render_compose(list(selected.values()), traefik_enabled=False)
    dify_env = compose["services"]["dify-api"]["environment"]
    ragflow_env = compose["services"]["ragflow"]["environment"]

    assert dify_env["RERANK_PROVIDER_BASE_URL"] == "http://llama-rerank:8080/v1"
    assert ragflow_env["RERANK_ENDPOINT"] == "http://llama-rerank:8080/v1"


def test_service_selection_n8n_is_isolated_automation_runtime() -> None:
    from agmind.components import load_component_contracts
    from agmind.services.renderer import (
        check_missing_dependencies,
        load_descriptors,
        render_to_string,
    )
    from agmind.services.selection import resolve_service_selection

    descriptors = load_descriptors()
    selected = resolve_service_selection(
        descriptors,
        services=["n8n"],
        component_contracts=load_component_contracts(),
    )

    assert set(selected) == {"n8n"}
    assert check_missing_dependencies(selected, descriptors) == {}

    # The Host(...) assertion below tests the public edge posture: traefik in the render
    # selection derives traefik_enabled=True, and authelia+redis satisfy the P0.3
    # topology gate for n8n's chain-internal route (15-04).
    rendered = render_to_string(
        services=sorted([*selected, "traefik", "authelia", "redis"]),
        domain="lab.example.com",
    )
    assert "image: n8nio/n8n:2.22.3@sha256:" in rendered
    assert "N8N_ENCRYPTION_KEY: ${N8N_ENCRYPTION_KEY:?N8N_ENCRYPTION_KEY is required}" in rendered
    assert "N8N_DIAGNOSTICS_ENABLED: 'false'" in rendered
    assert "N8N_RUNNERS_ENABLED: 'true'" in rendered
    assert "N8N_HOST: n8n.lab.example.com" in rendered
    assert "WEBHOOK_URL: https://n8n.lab.example.com/" in rendered
    assert "Host(`n8n.lab.example.com`)" in rendered


def test_service_selection_includes_operator_console_runtime_services() -> None:
    from agmind.components import load_component_contracts
    from agmind.services.renderer import (
        check_missing_dependencies,
        load_descriptors,
        render_to_string,
    )
    from agmind.services.selection import resolve_service_selection

    services = ["uptime-kuma", "homarr", "watchtower", "dozzle", "netdata"]
    descriptors = load_descriptors()
    selected = resolve_service_selection(
        descriptors,
        services=services,
        component_contracts=load_component_contracts(),
    )

    assert set(services) <= set(selected)
    assert check_missing_dependencies(selected, descriptors) == {}

    # Edge trio added for the render: the Host(...) assertions test public routing, and
    # the P0.3 gate requires authelia for these chain-internal routes (15-04).
    rendered = render_to_string(
        services=sorted([*services, "traefik", "authelia", "redis"]),
        domain="lab.example.com",
    )
    assert "image: louislam/uptime-kuma:2.3.2@sha256:" in rendered
    assert "image: ghcr.io/homarr-labs/homarr:v1.62.0@sha256:" in rendered
    assert "image: containrrr/watchtower:1.7.1@sha256:" in rendered
    assert "image: amir20/dozzle:v10.6.1@sha256:" in rendered
    assert "image: netdata/netdata:v2.10.3@sha256:" in rendered
    assert "image: louislam/uptime-kuma:latest" not in rendered
    assert "image: ghcr.io/homarr-labs/homarr:latest" not in rendered
    assert "WATCHTOWER_MONITOR_ONLY: 'true'" in rendered
    assert "Host(`uptime.lab.example.com`)" in rendered
    assert "Host(`homarr.lab.example.com`)" in rendered
    assert "Host(`dozzle.lab.example.com`)" in rendered
    assert "Host(`netdata.lab.example.com`)" in rendered
