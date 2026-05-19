"""CPU backend — fallback для x86_64 без AMD GPU.

Engine: только llama_cpp (CPU build).

См. R3-llama-cpp-vulkan-hip.md (build flags), AGMIND_MIGRATION_SPEC.md
§1.2.3 (CPU section).
"""

from __future__ import annotations

import shutil
from typing import Any, Sequence

from agmind.compute.base import Backend, DeviceInfo, LLMHandle
from agmind.compute.detect import detect_host
from agmind.log import logger

log = logger(__name__)

_SUPPORTED_ENGINES = ("llama_cpp",)


class CPUBackend(Backend):
    """CPU backend через llama-cpp-python (CPU build) + ONNXRuntime CPU."""

    name = "cpu"

    def __init__(self, engine: str) -> None:
        if engine not in _SUPPORTED_ENGINES:
            raise ValueError(
                f"CPU backend engine={engine!r} not supported. "
                f"Allowed: {_SUPPORTED_ENGINES}"
            )
        self._engine = engine
        self._llm: LLMHandle | None = None

    @classmethod
    def available(cls) -> bool:
        """CPU всегда есть — критический fallback."""
        return True

    @classmethod
    def make(cls, engine: str = "auto") -> "CPUBackend":
        if engine == "auto":
            engine = "llama_cpp"
        return cls(engine=engine)

    def device_info(self) -> DeviceInfo:
        host = detect_host()
        caps: dict[str, Any] = {
            "cpu_model": host.cpu_model,
            "cpu_cores": host.cpu_cores,
            "kernel": host.kernel_version,
            "llama_cpp_installed": _llama_cpp_installed(),
        }
        return DeviceInfo(
            backend=self.name,
            engine=self._engine,
            device_id=0,
            name=host.cpu_model or "x86_64 CPU",
            total_memory_bytes=host.system_ram_bytes,
            capabilities=caps,
        )

    def load_llm(self, model_path: str, **kwargs: Any) -> LLMHandle:
        # HTTP mode: AGMIND_LLAMA_SERVER_URL set → connect к running server
        from agmind.compute.config import read_config

        cfg = read_config()
        if cfg.llama_server_url:
            from agmind.compute.backends._engines.llama_server_handle import (
                LlamaServerHandle,
            )
            from agmind.compute.clients import LlamaServerClient

            log.info("CPU backend: using HTTP llama-server at %s", cfg.llama_server_url)
            client = LlamaServerClient(cfg.llama_server_url)
            self._llm = LlamaServerHandle(client, model=model_path)
            return self._llm

        # In-process mode (default for dev): load model via llama-cpp-python
        if not _llama_cpp_installed():
            raise RuntimeError(
                "llama-cpp-python is not installed AND AGMIND_LLAMA_SERVER_URL "
                "is not set. Either: \n"
                "  1. pip install 'llama-cpp-python>=0.3.23'  (in-process)\n"
                "  2. export AGMIND_LLAMA_SERVER_URL=http://llama-llm:8080  (HTTP)"
            )
        from agmind.compute.backends._engines.llama_cpp_cpu import (
            LlamaCppCPUEngine,
        )
        engine = LlamaCppCPUEngine()
        self._llm = engine.load(model_path, **kwargs)
        return self._llm

    def embed(self, texts: Sequence[str], model: str) -> list[list[float]]:
        from agmind.compute.config import read_config

        cfg = read_config()
        if cfg.llama_server_url:
            from agmind.compute.clients import LlamaServerClient

            client = LlamaServerClient(cfg.llama_server_url)
            return client.embed(texts, model=model)

        if not _llama_cpp_installed():
            raise RuntimeError("llama-cpp-python is not installed")
        from agmind.compute.backends._engines.llama_cpp_cpu import (
            LlamaCppCPUEngine,
        )
        return LlamaCppCPUEngine().embed(texts, model)

    def rerank(self, query: str, documents: Sequence[str]) -> list[float]:
        from agmind.compute.config import read_config

        cfg = read_config()
        if cfg.llama_server_url:
            from agmind.compute.clients import LlamaServerClient

            client = LlamaServerClient(cfg.llama_server_url)
            return client.rerank(query, documents)

        if not _llama_cpp_installed():
            raise RuntimeError("llama-cpp-python is not installed")
        from agmind.compute.backends._engines.llama_cpp_cpu import (
            LlamaCppCPUEngine,
        )
        return LlamaCppCPUEngine().rerank(query, documents)


def _llama_cpp_installed() -> bool:
    """Lightweight check: is llama-cpp-python importable.

    Не делает реального импорта (heavy), только проверяет наличие через
    `importlib.util.find_spec`. Идемпотентно при многократных вызовах.
    """
    import importlib.util

    return importlib.util.find_spec("llama_cpp") is not None
