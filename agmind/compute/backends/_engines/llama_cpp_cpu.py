"""llama-cpp-python CPU engine — для CPU backend.

Lazy import llama_cpp — модуль может отсутствовать на dev-машине без
GPU. Реальная инициализация Llama instance — на `load()`.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from agmind.compute.base import LLMHandle
from agmind.core.logging import logger

log = logger(__name__)


class _LlamaCppHandle(LLMHandle):
    """Wrapper над llama_cpp.Llama (CPU)."""

    def __init__(self, llama_instance: object) -> None:
        # llama_instance is llama_cpp.Llama, but we don't import the type
        # at module level to keep llama_cpp lazy.
        self._llama = llama_instance

    def generate(
        self,
        prompt: str,
        *,
        max_tokens: int = 512,
        temperature: float = 0.7,
        stop: Sequence[str] | None = None,
        **kwargs: Any,
    ) -> str:
        out = self._llama(  # type: ignore[operator]
            prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            stop=list(stop) if stop else None,
            **kwargs,
        )
        # llama_cpp returns dict with choices[0]["text"]
        if isinstance(out, dict):
            choices = out.get("choices") or []
            if choices and isinstance(choices[0], dict):
                return str(choices[0].get("text", ""))
        return str(out)

    def close(self) -> None:
        self._llama = None  # GC pickle up llama instance


class LlamaCppCPUEngine:
    """Factory для CPU LLM/embed/rerank через llama-cpp-python."""

    def load(self, model_path: str, **kwargs: Any) -> _LlamaCppHandle:
        from llama_cpp import Llama

        defaults: dict[str, Any] = {
            "n_ctx": 8192,
            "n_threads": None,  # llama-cpp auto-detect
            "verbose": False,
        }
        defaults.update(kwargs)
        llama = Llama(model_path=model_path, **defaults)
        return _LlamaCppHandle(llama)

    def embed(
        self,
        texts: Sequence[str],
        model: str,
        **kwargs: Any,
    ) -> list[list[float]]:
        from llama_cpp import Llama

        defaults: dict[str, Any] = {
            "model_path": model,
            "embedding": True,
            "verbose": False,
        }
        defaults.update(kwargs)
        llama = Llama(**defaults)
        try:
            embeddings: list[list[float]] = []
            for text in texts:
                emb = llama.create_embedding(text)
                # llama_cpp returns {"data": [{"embedding": [...]}]}
                data = emb.get("data") if isinstance(emb, dict) else None
                if not data:
                    embeddings.append([])
                    continue
                vec = data[0].get("embedding") if isinstance(data[0], dict) else None
                embeddings.append(list(vec) if vec else [])
            return embeddings
        finally:
            del llama

    def rerank(
        self,
        query: str,
        documents: Sequence[str],
        **kwargs: Any,
    ) -> list[float]:
        # In-process rerank via cosine-of-embeddings is NOT a real cross-encoder rerank — it
        # ignores the rank head and silently returned wrong scores. The correct path is the
        # deployed llama-server /v1/rerank endpoint (review LOW inprocess-rerank-cosine).
        raise NotImplementedError(
            "in-process rerank is not implemented; use the deployed reranker via "
            "AGMIND_LLAMA_SERVER_URL (HTTP /v1/rerank)"
        )


def _safe_embed(llama: Any, text: str) -> list[float]:
    emb = llama.create_embedding(text)
    if not isinstance(emb, dict):
        return []
    data = emb.get("data") or []
    if not data or not isinstance(data[0], dict):
        return []
    return list(data[0].get("embedding") or [])
