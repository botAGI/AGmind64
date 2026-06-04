"""ABC для compute backend.

См. ADR-0002 § «Контракт». Минимальный API — расширяется по фактической
нужде (Karpathy "simplicity first": не закладывать спекулятивные методы).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class DeviceInfo:
    """Метаданные compute-устройства.

    Поля специально keep-minimal — то, что детектируется на любой машине без
    зависимости от vendor-specific CLI утилит. Engine-specific метаданные
    (cooperative_matrix, BF16, etc) идут в `capabilities`.
    """

    backend: str
    """Имя backend'а: "cpu" | "vulkan" | "rocm" | "npu"."""

    engine: str
    """Имя engine внутри backend: "llama_cpp" | "vllm" | "infinity" | "stub"."""

    device_id: int
    """Index устройства (0 для primary, для multi-GPU 0..N-1)."""

    name: str
    """Human-readable имя устройства, например "AMD Radeon 8060S (gfx1151)"
    или "AMD Ryzen AI Max+ 395"."""

    total_memory_bytes: int
    """Доступная память для compute в байтах. На UMA-системах (Strix Halo)
    это `mem_info_gtt_total`, не `mem_info_vram_total`."""

    capabilities: dict[str, Any] = field(default_factory=dict)
    """Engine-specific дополнения: cooperative_matrix, bf16, fp8,
    flash_attention_v2, max_seq_len, vulkan_api_version и т.п."""

    def __post_init__(self) -> None:
        if not self.backend:
            raise ValueError("DeviceInfo.backend must not be empty")
        if not self.engine:
            raise ValueError("DeviceInfo.engine must not be empty")


class LLMHandle(ABC):
    """Загруженная LLM. Время жизни — пока handle жив; close() освобождает GPU.

    Расширенный API (2026-05, после R-llm-models):
    - generate(prompt, ...) — legacy completion (без chat template)
    - generate_stream(prompt, ...) — streaming для real-time UI
    - chat(messages, ...) — OpenAI-compatible chat с chat template
    - chat_stream(messages, ...) — streaming chat
    - close() — release resources
    """

    @abstractmethod
    def generate(
        self,
        prompt: str,
        *,
        max_tokens: int = 512,
        temperature: float = 0.7,
        stop: Sequence[str] | None = None,
        **kwargs: Any,
    ) -> str:
        """Synchronous completion. Returns full output string.

        kwargs — дополнительные sampling params (top_p, top_k, repeat_penalty,
        seed, etc). См. SamplingParams в agmind.compute.clients.llama_server.
        """

    def generate_stream(
        self,
        prompt: str,
        *,
        max_tokens: int = 512,
        temperature: float = 0.7,
        stop: Sequence[str] | None = None,
        **kwargs: Any,
    ) -> Iterator[str]:
        """Streaming completion — yields text chunks.

        Default — fallback к одиночному `generate()` (для backends где
        streaming не имеет смысла, e.g. NPU stub).
        """
        yield self.generate(
            prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            stop=stop,
            **kwargs,
        )

    def chat(
        self,
        messages: Sequence[dict[str, str]],
        *,
        max_tokens: int = 512,
        temperature: float = 0.7,
        stop: Sequence[str] | None = None,
        **kwargs: Any,
    ) -> str:
        """OpenAI-compatible chat. Default — joins messages в prompt.

        Backends могут override для использования chat template из GGUF.
        """
        prompt = self._messages_to_prompt(messages)
        return self.generate(
            prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            stop=stop,
            **kwargs,
        )

    def chat_stream(
        self,
        messages: Sequence[dict[str, str]],
        *,
        max_tokens: int = 512,
        temperature: float = 0.7,
        stop: Sequence[str] | None = None,
        **kwargs: Any,
    ) -> Iterator[str]:
        """Streaming chat. Default — fallback to generate_stream."""
        prompt = self._messages_to_prompt(messages)
        return self.generate_stream(
            prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            stop=stop,
            **kwargs,
        )

    @staticmethod
    def _messages_to_prompt(messages: Sequence[dict[str, str]]) -> str:
        """Naive fallback: join messages с role labels.

        Этот формат подходит для smoke tests. Production использует
        chat template из tokenizer.json модели через llama-server.
        """
        parts: list[str] = []
        for m in messages:
            role = str(m.get("role", "user")).strip()
            content = str(m.get("content", "")).strip()
            if role == "system":
                parts.append(f"System: {content}")
            elif role == "assistant":
                parts.append(f"Assistant: {content}")
            else:
                parts.append(f"User: {content}")
        parts.append("Assistant:")
        return "\n".join(parts)

    @abstractmethod
    def close(self) -> None:
        """Release GPU memory and stop server processes."""

    def __enter__(self) -> LLMHandle:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


class Backend(ABC):
    """Compute backend ABC.

    Subclass'ы регистрируются в `agmind.compute._registry` через
    `_registry.register(Backend)`. Имя backend'а — class-level `name`.
    """

    name: str = ""
    """Backend identifier: "cpu" | "vulkan" | "rocm" | "npu"."""

    @classmethod
    @abstractmethod
    def available(cls) -> bool:
        """Можно ли использовать этот backend на текущей машине.

        Это lightweight check (без heavy imports): проверка наличия
        бинарников, sysfs paths, env vars. Тяжёлые проверки —
        отложить до `device_info()`.
        """

    @classmethod
    @abstractmethod
    def make(cls, engine: str = "auto") -> Backend:
        """Factory: создать инстанс backend с конкретным engine.

        Args:
            engine: "llama_cpp" / "vllm" / "infinity" / "stub" / "auto"

        Returns:
            Backend instance.

        Raises:
            ValueError: engine не поддерживается на этом backend.
            RuntimeError: engine не доступен (отсутствуют зависимости).
        """

    @abstractmethod
    def device_info(self) -> DeviceInfo:
        """Probe устройства и engine, вернуть metadata."""

    @abstractmethod
    def load_llm(self, model_path: str, **kwargs: Any) -> LLMHandle:
        """Load LLM from disk (GGUF / HF folder / ONNX), return handle."""

    @abstractmethod
    def embed(self, texts: Sequence[str], model: str) -> list[list[float]]:
        """Embed sequence of texts. Returns float vectors as list of lists.

        Зачем list-of-lists а не numpy.ndarray: чтобы не зависеть от numpy
        в публичном API. Backend'ы могут возвращать np.array.tolist().
        """

    @abstractmethod
    def rerank(
        self,
        query: str,
        documents: Sequence[str],
    ) -> list[float]:
        """Score relevance documents для query. Higher = more relevant.

        Возвращает список scores той же длины что documents.
        """
