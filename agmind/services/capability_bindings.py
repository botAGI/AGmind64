"""Phase O.B: capability injection table.

Когда consumer ('dify-api', 'ragflow', 'openwebui') consumes capability
('vector_db', 'llm_inference', ...), renderer должен подсунуть ему
правильные env vars указывающие на выбранного provider'а.

Convention:
    BINDINGS[capability][provider][consumer] = {ENV_VAR: value_template}

Value template поддерживает Python format() placeholders:
    {provider} — имя сервиса provider'а (e.g. 'milvus')
    {provider_host} — то же что provider (compose service hostname == name)

Пример:
    consumer = dify-api
    capability = vector_db
    provider = milvus
    → inject: VECTOR_STORE=milvus, MILVUS_URI=http://milvus:19530

Note: правда мы не претендуем что покрываем все случаи env vars каждого
сервиса. Это short-list для **vector_db** + **llm_inference** + **embedding**
которые user явно меняет между qdrant/weaviate/milvus и ragflow/dify.
Расширять при добавлении новых stack'ов.
"""

from __future__ import annotations

CapabilityBindings = dict[str, dict[str, dict[str, dict[str, str]]]]
"""capability → provider → consumer → {env_key: value_template}."""


BINDINGS: CapabilityBindings = {
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
            "ragflow": {
                "DOC_ENGINE": "qdrant",
                "QDRANT_HOST": "qdrant",
                "QDRANT_PORT": "6333",
            },
        },
        "weaviate": {
            "dify-api": {
                "VECTOR_STORE": "weaviate",
                "WEAVIATE_ENDPOINT": "http://weaviate:8080",
            },
            "dify-worker": {
                "VECTOR_STORE": "weaviate",
                "WEAVIATE_ENDPOINT": "http://weaviate:8080",
            },
            "ragflow": {
                "DOC_ENGINE": "weaviate",
                "WEAVIATE_HOST": "weaviate",
                "WEAVIATE_PORT": "8080",
            },
        },
        "milvus": {
            "dify-api": {
                "VECTOR_STORE": "milvus",
                "MILVUS_URI": "http://milvus:19530",
                "MILVUS_TOKEN": "",
            },
            "dify-worker": {
                "VECTOR_STORE": "milvus",
                "MILVUS_URI": "http://milvus:19530",
            },
            "ragflow": {
                "DOC_ENGINE": "milvus",
                "MILVUS_URI": "http://milvus:19530",
            },
        },
        "elasticsearch": {
            # ragflow's default — vector via ES dense_vector
            "ragflow": {
                "DOC_ENGINE": "elasticsearch",
                "ES_HOST": "elasticsearch",
                "ES_PORT": "9200",
            },
        },
    },
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
    "embedding_inference": {
        "llama-embed": {
            "dify-api": {
                "EMBEDDING_PROVIDER": "openai_compat",
                "EMBEDDING_API_BASE": "http://llama-embed:8081/v1",
            },
            "ragflow": {
                "EMBEDDING_ENDPOINT": "http://llama-embed:8081/v1",
            },
        },
    },
    "reranker": {
        "llama-rerank": {
            "dify-api": {
                "RERANK_API_BASE": "http://llama-rerank:8082/v1",
            },
            "ragflow": {
                "RERANK_ENDPOINT": "http://llama-rerank:8082/v1",
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
    не нуждаются в env injection (или uses sensible defaults from image).
    """
    cap_table = BINDINGS.get(capability, {})
    prov_table = cap_table.get(provider, {})
    return dict(prov_table.get(consumer, {}))


__all__ = ["BINDINGS", "env_for_consumer"]
