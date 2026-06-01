"""Phase O.B: capability injection table (revised after 2026-05-20 research).

ВНИМАНИЕ: первая версия этого файла содержала выдумки. После research
обновлено по verified источникам:

  - Dify env vars: docs.dify.ai/getting-started/install-self-hosted/environments
    VECTOR_STORE ∈ {qdrant, milvus, weaviate, pgvector, chroma,
                    opensearch, oracle, ...} (25+ backends).
  - RAGFlow env vars: github.com/infiniflow/ragflow/blob/main/docker/.env
    DOC_ENGINE ∈ {elasticsearch, infinity, oceanbase, opensearch, seekdb}.
    RAGFlow НЕ поддерживает milvus/qdrant/weaviate как DOC_ENGINE — это
    Dify-only options.
  - Dify ↔ RAGFlow integration: marketplace.dify.ai/plugin/witmeng/ragflow-api.
    Plugin require: RAGFLOW_API_ENDPOINT (http://ragflow:9380) + RAGFLOW_API_KEY.

Внутри docker compose network все llama-server container ports = 8080
(host-side ports 8080/8081/8082 — это публикация на 127.0.0.1, не
internal hostnames). Hostname == service name.

Convention: BINDINGS[capability][provider][consumer] = {ENV_KEY: value_template}.
"""

from __future__ import annotations

CapabilityBindings = dict[str, dict[str, dict[str, dict[str, str]]]]


BINDINGS: CapabilityBindings = {
    # ---- vector_db: SUPPORTED only by Dify (per Dify env docs) ----
    # RAGFlow uses search_index capability (ES / opensearch / infinity), не vector_db.
    "vector_db": {
        "qdrant": {
            "dify-api": {
                "VECTOR_STORE": "qdrant",
                "QDRANT_URL": "http://qdrant:6333",
                "QDRANT_API_KEY": "",
            },
            "dify-worker": {
                "VECTOR_STORE": "qdrant",
                "QDRANT_URL": "http://qdrant:6333",
            },
        },
        "weaviate": {
            "dify-api": {
                "VECTOR_STORE": "weaviate",
                "WEAVIATE_ENDPOINT": "http://weaviate:8080",
                "WEAVIATE_API_KEY": "",
            },
            "dify-worker": {
                "VECTOR_STORE": "weaviate",
                "WEAVIATE_ENDPOINT": "http://weaviate:8080",
            },
        },
        "milvus": {
            "dify-api": {
                "VECTOR_STORE": "milvus",
                "MILVUS_URI": "http://milvus:19530",
                "MILVUS_TOKEN": "",
                "MILVUS_DATABASE": "default",
            },
            "dify-worker": {
                "VECTOR_STORE": "milvus",
                "MILVUS_URI": "http://milvus:19530",
            },
        },
    },
    # ---- search_index: для RAGFlow (DOC_ENGINE) ----
    # Не для Dify — у Dify свой vector_store.
    "search_index": {
        "elasticsearch": {
            "ragflow": {
                "DOC_ENGINE": "elasticsearch",
                "ES_HOST": "elasticsearch",
                "ES_PORT": "9200",
            },
        },
        # Future: opensearch / infinity / oceanbase / seekdb — add when descriptors exist.
    },
    # ---- llm_inference: llama-server OpenAI-compatible API ----
    # Internal container port = 8080 for всех llama-* (host ports разные —
    # см. templates/services/llama-{llm,embed,rerank}.yaml).
    "llm_inference": {
        "llama-llm": {
            "dify-api": {
                "OPENAI_API_BASE": "http://llama-llm:8080/v1",
                "OPENAI_API_KEY": "sk-no-key-needed",
            },
            "dify-worker": {
                "OPENAI_API_BASE": "http://llama-llm:8080/v1",
            },
            "ragflow": {
                "VLM_ENDPOINT": "http://llama-llm:8080/v1",
            },
            "openwebui": {
                "OPENAI_API_BASE_URL": "http://llama-llm:8080/v1",
                "OPENAI_API_KEY": "sk-no-key-needed",
            },
        },
    },
    # ---- embedding_inference: llama-server embed model ----
    "embedding_inference": {
        "llama-embed": {
            "dify-api": {
                # Dify use 'openai_api_compatible' provider type.
                "EMBEDDING_PROVIDER_BASE_URL": "http://llama-embed:8080/v1",
                "EMBEDDING_PROVIDER_API_KEY": "sk-no-key-needed",
            },
            "dify-worker": {
                "EMBEDDING_PROVIDER_BASE_URL": "http://llama-embed:8080/v1",
                "EMBEDDING_PROVIDER_API_KEY": "sk-no-key-needed",
            },
            "ragflow": {
                # RAGFlow: configured per-tenant в UI, env shortcut:
                "EMBEDDING_ENDPOINT": "http://llama-embed:8080/v1",
            },
        },
    },
    # ---- reranker: llama-server rerank model ----
    "reranker": {
        "llama-rerank": {
            "dify-api": {
                "RERANK_PROVIDER_BASE_URL": "http://llama-rerank:8080/v1",
            },
            "ragflow": {
                "RERANK_ENDPOINT": "http://llama-rerank:8080/v1",
            },
        },
    },
    # ---- dify_external_kb: RAGFlow → Dify (via marketplace plugin) ----
    # User configures plugin credentials в Dify UI; env hint для discovery.
    "dify_external_kb": {
        "ragflow": {
            "dify-api": {
                "RAGFLOW_API_ENDPOINT": "http://ragflow:9380/api/v1",
                # API key user заполняет вручную через Dify plugin settings.
                # Placeholder env passes hint в plugin discovery.
                "RAGFLOW_API_KEY_HINT": "set-in-dify-plugin-settings",
            },
        },
    },
}


def env_for_consumer(
    capability: str,
    provider: str,
    consumer: str,
) -> dict[str, str]:
    """Return env vars to inject в `consumer` для `provider` of `capability`.

    Empty dict если пара не известна — это OK для consumers которые
    не нуждаются в env injection (или используют sensible defaults from image).
    """
    cap_table = BINDINGS.get(capability, {})
    prov_table = cap_table.get(provider, {})
    return dict(prov_table.get(consumer, {}))


__all__ = ["BINDINGS", "env_for_consumer"]
