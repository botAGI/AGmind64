"""Shared HTTP fallback для всех backends (Vulkan/ROCm/CPU).

Если AGMIND_LLAMA_SERVER_URL set — backends используют этот helper
вместо in-process Llama() instance.

DRY: один источник истины для config read + client construction.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from agmind.compute.base import LLMHandle

if TYPE_CHECKING:
    from agmind.compute.clients import LlamaServerClient


def _server_url() -> str:
    """Return llama-server URL из config, или empty string."""
    from agmind.compute.config import read_config

    return read_config().llama_server_url


def _client() -> LlamaServerClient | None:
    """Return LlamaServerClient если URL set, else None."""
    url = _server_url()
    if not url:
        return None
    from agmind.compute.clients import LlamaServerClient

    return LlamaServerClient(url)


def try_http_handle(model_path: str) -> LLMHandle | None:
    """If AGMIND_LLAMA_SERVER_URL set → return LlamaServerHandle, else None."""
    client = _client()
    if client is None:
        return None
    from agmind.compute.backends._engines.llama_server_handle import (
        LlamaServerHandle,
    )

    return LlamaServerHandle(client, model=model_path)


def try_http_embed(
    texts: Sequence[str],
    model: str,
) -> list[list[float]] | None:
    """If AGMIND_LLAMA_SERVER_URL set → embed via HTTP, else None."""
    client = _client()
    if client is None:
        return None
    return client.embed(texts, model=model)


def try_http_rerank(
    query: str,
    documents: Sequence[str],
) -> list[float] | None:
    """If AGMIND_LLAMA_SERVER_URL set → rerank via HTTP, else None."""
    client = _client()
    if client is None:
        return None
    return client.rerank(query, documents)
