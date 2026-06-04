"""llama-cpp-python HIP engine — primary engine для ROCm backend.

Lazy import llama_cpp. Env vars устанавливаются Backend.make().

См. R3-llama-cpp-vulkan-hip.md (build flags GGML_HIP_NO_VMM=ON,
GGML_HIP_ROCWMMA_FATTN=ON, etc).
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


class LlamaCppHIPEngine:
    """LLM/embed/rerank через llama-cpp-python с GGML_HIP."""

    def load(self, model_path: str, **kwargs: Any) -> _LlamaCppHandle:
        from llama_cpp import Llama  # noqa: PLC0415

        defaults: dict[str, Any] = {
            "n_ctx": kwargs.get("n_ctx", 8192),
            "n_gpu_layers": kwargs.get("n_gpu_layers", -1),  # 999 = all
            # Для HIP: -dio (direct IO) обязателен для моделей >6 GB
            "use_mmap": kwargs.get("use_mmap", False),
            "flash_attn": kwargs.get("flash_attn", True),  # rocWMMA FA
            "n_batch": kwargs.get("n_batch", 2048),  # из discussion #20856
            "n_ubatch": kwargs.get("n_ubatch", 512),
            "verbose": False,
        }
        for k, v in kwargs.items():
            if k not in defaults:
                defaults[k] = v
        log.info(
            "llama-cpp HIP load: %s (n_ctx=%d n_gpu_layers=%d)",
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
        from llama_cpp import Llama  # noqa: PLC0415

        defaults: dict[str, Any] = {
            "model_path": model,
            "embedding": True,
            "pooling_type": kwargs.pop("pooling_type", 1),  # CLS для BGE-M3
            "n_gpu_layers": kwargs.pop("n_gpu_layers", -1),
            "use_mmap": False,
            "n_ctx": kwargs.pop("n_ctx", 8192),
            "n_batch": kwargs.pop("n_batch", 2048),
            "verbose": False,
        }
        defaults.update(kwargs)
        llama = Llama(**defaults)
        try:
            return [_safe_embed(llama, text) for text in texts]
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
