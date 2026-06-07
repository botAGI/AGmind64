"""llama-cpp-python Vulkan engine — primary engine для Vulkan backend.

Lazy import llama_cpp. Engine не делает env-setup — это Backend.make()
ответственность.

См. R3-llama-cpp-vulkan-hip.md.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from agmind.compute.backends._engines.llama_cpp_cpu import (
    _LlamaCppHandle,
    _safe_embed,
)
from agmind.core.logging import logger

log = logger(__name__)


class LlamaCppVulkanEngine:
    """LLM/embed/rerank через llama-cpp-python с GGML_VULKAN."""

    def load(self, model_path: str, **kwargs: Any) -> _LlamaCppHandle:
        from llama_cpp import Llama

        defaults: dict[str, Any] = {
            "n_ctx": kwargs.get("n_ctx", 8192),
            "n_gpu_layers": kwargs.get("n_gpu_layers", -1),  # все слои на GPU
            "use_mmap": kwargs.get("use_mmap", False),  # critical для STX-H
            "flash_attn": kwargs.get("flash_attn", True),  # Wave32 FA после PR #19625
            "n_batch": kwargs.get("n_batch", 512),
            "n_ubatch": kwargs.get("n_ubatch", 256),  # safe для DeviceLost
            "verbose": False,
        }
        # Только non-default user kwargs override
        for k, v in kwargs.items():
            if k not in defaults:
                defaults[k] = v
        log.info(
            "llama-cpp Vulkan load: %s (n_ctx=%d n_gpu_layers=%d)",
            model_path,
            defaults["n_ctx"],
            defaults["n_gpu_layers"],
        )
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
            "pooling_type": kwargs.pop("pooling_type", 1),  # 1 = LLAMA_POOLING_CLS (BGE-M3)
            "n_gpu_layers": kwargs.pop("n_gpu_layers", -1),
            "use_mmap": False,
            "n_ctx": kwargs.pop("n_ctx", 8192),
            "n_batch": kwargs.pop("n_batch", 512),
            "verbose": False,
        }
        defaults.update(kwargs)
        llama = Llama(**defaults)
        try:
            embeddings: list[list[float]] = []
            for text in texts:
                embeddings.append(_safe_embed(llama, text))
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
