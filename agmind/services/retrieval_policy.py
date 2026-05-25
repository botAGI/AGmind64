"""Operator-facing retrieval topology helpers for Dify and RAGFlow.

Dify uses a vector database capability. RAGFlow uses a search/doc-engine
capability. Some backends may look similar in the UI, but they are not
interchangeable: Milvus/Qdrant/Weaviate are Dify vector stores in the current
catalog, while RAGFlow requires Elasticsearch for DOC_ENGINE today.
"""

from __future__ import annotations

from collections.abc import Iterable

DIFY_SERVICES = frozenset(
    {
        "dify-api",
        "dify-worker",
        "dify-web",
        "dify-sandbox",
        "dify-plugin-daemon",
    }
)
DIFY_VECTOR_PROVIDERS = ("milvus", "weaviate", "qdrant")
RAGFLOW_SEARCH_PROVIDERS = ("elasticsearch",)
_PROVIDER_LABELS = {
    "qdrant": "Qdrant",
    "milvus": "Milvus",
    "weaviate": "Weaviate",
    "elasticsearch": "Elasticsearch",
}


def selected_dify_vector_provider(services: Iterable[str]) -> str | None:
    """Return the active Dify vector provider candidate, if Dify is active."""
    return _first_present(set(selected_dify_vector_providers(services)), DIFY_VECTOR_PROVIDERS)


def selected_dify_vector_providers(services: Iterable[str]) -> tuple[str, ...]:
    """Return all selected Dify vector providers, in resolution priority order."""
    selected = set(services)
    if not selected & DIFY_SERVICES:
        return ()
    return tuple(provider for provider in DIFY_VECTOR_PROVIDERS if provider in selected)


def selected_ragflow_search_provider(services: Iterable[str]) -> str | None:
    """Return the selected RAGFlow DOC_ENGINE provider, if RAGFlow is active."""
    selected = set(services)
    if "ragflow" not in selected:
        return None
    return _first_present(selected, RAGFLOW_SEARCH_PROVIDERS)


def summarize_retrieval_topology(services: Iterable[str]) -> list[str]:
    """Build short human-readable retrieval topology lines for setup summary."""
    selected = set(services)
    lines: list[str] = []
    dify_provider = selected_dify_vector_provider(selected)
    ragflow_provider = selected_ragflow_search_provider(selected)

    dify_providers = selected_dify_vector_providers(selected)
    if dify_provider is not None:
        if len(dify_providers) > 1:
            also_selected = ", ".join(
                provider for provider in dify_providers if provider != dify_provider
            )
            lines.append(
                f"DIFY VECTOR DB ..... {dify_provider} (ambiguous: {also_selected} also selected)"
            )
        else:
            lines.append(f"DIFY VECTOR DB ..... {dify_provider}")
    if ragflow_provider is not None:
        lines.append(f"RAGFLOW DOC ENGINE . {ragflow_provider}")

    if len(dify_providers) > 1:
        lines.append("NOTE ............... Choose one Dify VECTOR_STORE for deployment")

    if dify_provider and "ragflow" in selected and ragflow_provider:
        if dify_provider != ragflow_provider:
            label = _PROVIDER_LABELS.get(dify_provider, dify_provider)
            lines.append(
                f"NOTE ............... {label} applies to Dify only; "
                f"RAGFlow uses {ragflow_provider}"
            )
        else:
            lines.append("NOTE ............... Dify and RAGFlow share this retrieval backend")
    elif "ragflow" in selected and ragflow_provider is None:
        lines.append("RAGFLOW DOC ENGINE . missing search_index provider")

    return lines


def _first_present(selected: set[str], candidates: tuple[str, ...]) -> str | None:
    for candidate in candidates:
        if candidate in selected:
            return candidate
    return None


__all__ = [
    "DIFY_VECTOR_PROVIDERS",
    "RAGFLOW_SEARCH_PROVIDERS",
    "selected_dify_vector_provider",
    "selected_dify_vector_providers",
    "selected_ragflow_search_provider",
    "summarize_retrieval_topology",
]
