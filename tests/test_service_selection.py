from __future__ import annotations


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
    assert ragflow_env["MYSQL_PASSWORD"] == "${MYSQL_ROOT_PASSWORD}"
    assert ragflow_env["MINIO_HOST"] == "minio"
    assert ragflow_env["MINIO_PORT"] == "9000"
    assert ragflow_env["MINIO_USER"] == "${MINIO_ROOT_USER}"
    assert ragflow_env["MINIO_PASSWORD"] == "${MINIO_ROOT_PASSWORD}"
    assert ragflow_env["REDIS_HOST"] == "redis"
    assert ragflow_env["REDIS_PORT"] == "6379"
    assert ragflow_env["REDIS_PASSWORD"] == "${REDIS_PASSWORD}"
    assert ragflow_env["DOC_ENGINE"] == "elasticsearch"
    assert ragflow_env["ES_HOST"] == "elasticsearch"
