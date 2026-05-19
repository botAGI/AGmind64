"""llama-cpp-python CPU engine — для CPU backend.

Lazy import llama_cpp — модуль может отсутствовать на dev-машине без
GPU. Реальная инициализация Llama instance — на `load()`.
"""

from __future__ import annotations

from typing import Any, Sequence

from agmind.compute.base import LLMHandle
from agmind.log import logger

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
    ) -> str:
        out = self._llama(  # type: ignore[operator]
            prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            stop=list(stop) if stop else None,
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
        from llama_cpp import Llama  # noqa: PLC0415 — lazy import by design

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
        from llama_cpp import Llama  # noqa: PLC0415

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
        # Для reranking llama.cpp используется в pooling=rank mode. Это
        # требует reranker GGUF модели — caller должен передать model
        # path через kwargs.
        from llama_cpp import Llama  # noqa: PLC0415

        model_path = kwargs.pop("model", None)
        if model_path is None:
            raise ValueError(
                "rerank requires model='<path-to-bge-reranker-v2-m3.gguf>'"
            )

        defaults: dict[str, Any] = {
            "model_path": model_path,
            "embedding": True,  # rerank piggybacks on embedding API
            "verbose": False,
        }
        defaults.update(kwargs)
        llama = Llama(**defaults)
        try:
            # Для rerank-задачи правильнее использовать BgeReranker / custom
            # pooling=rank. Здесь fallback: вычисляем cosine sim между
            # query и каждым doc через mean-pooling embeddings.
            q_emb = _safe_embed(llama, query)
            scores: list[float] = []
            for doc in documents:
                d_emb = _safe_embed(llama, doc)
                scores.append(_cosine(q_emb, d_emb))
            return scores
        finally:
            del llama


def _safe_embed(llama: Any, text: str) -> list[float]:
    emb = llama.create_embedding(text)
    if not isinstance(emb, dict):
        return []
    data = emb.get("data") or []
    if not data or not isinstance(data[0], dict):
        return []
    return list(data[0].get("embedding") or [])


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(x * x for x in b) ** 0.5
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)
