"""LLMHandle wrapper for remote llama-server (HTTP).

Используется когда AGmind connects к running llama-server (docker
container из services.yaml). В отличие от _LlamaCppHandle (in-process
Llama() instance), HTTP handle:
- Не загружает модель в собственный process
- Поддерживает streaming через SSE
- Использует server-side chat template (из GGUF metadata)
- Может быть shared между несколькими agmind clients
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from typing import Any

from agmind.compute.base import LLMHandle
from agmind.compute.clients import LlamaServerClient, SamplingParams
from agmind.log import logger

log = logger(__name__)


class LlamaServerHandle(LLMHandle):
    """LLMHandle, реализованный поверх HTTP клиента llama-server."""

    def __init__(self, client: LlamaServerClient, model: str = "") -> None:
        self._client = client
        self._model = model
        self._closed = False

    # ---- LLMHandle interface ----

    def generate(
        self,
        prompt: str,
        *,
        max_tokens: int = 512,
        temperature: float = 0.7,
        stop: Sequence[str] | None = None,
        **kwargs: Any,
    ) -> str:
        self._check_open()
        sampling = self._sampling_from_kwargs(temperature, kwargs)
        return self._client.complete(
            prompt,
            max_tokens=max_tokens,
            stop=stop,
            sampling=sampling,
        )

    def generate_stream(
        self,
        prompt: str,
        *,
        max_tokens: int = 512,
        temperature: float = 0.7,
        stop: Sequence[str] | None = None,
        **kwargs: Any,
    ) -> Iterator[str]:
        self._check_open()
        sampling = self._sampling_from_kwargs(temperature, kwargs)
        yield from self._client.complete_stream(
            prompt,
            max_tokens=max_tokens,
            stop=stop,
            sampling=sampling,
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
        """Server-side chat template (из GGUF metadata) — лучше чем base."""
        self._check_open()
        sampling = self._sampling_from_kwargs(temperature, kwargs)
        tools = kwargs.pop("tools", None)
        return self._client.chat(
            messages,
            max_tokens=max_tokens,
            stop=stop,
            sampling=sampling,
            tools=tools,
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
        self._check_open()
        sampling = self._sampling_from_kwargs(temperature, kwargs)
        tools = kwargs.pop("tools", None)
        yield from self._client.chat_stream(
            messages,
            max_tokens=max_tokens,
            stop=stop,
            sampling=sampling,
            tools=tools,
        )

    def close(self) -> None:
        """HTTP handle не владеет процессом llama-server — close = no-op flag."""
        self._closed = True

    # ---- HTTP-specific extensions ----

    def health(self) -> dict[str, Any]:
        """Поверка llama-server alive."""
        return self._client.health()

    def model_info(self) -> dict[str, Any]:
        """GET /props — server metadata, model name, n_ctx, etc."""
        return self._client.props()

    # ---- helpers ----

    def _check_open(self) -> None:
        if self._closed:
            raise RuntimeError("LlamaServerHandle is closed — re-create via load_llm()")

    @staticmethod
    def _sampling_from_kwargs(
        temperature: float,
        kwargs: dict[str, Any],
    ) -> SamplingParams:
        """Build SamplingParams from kwargs + temperature default."""
        return SamplingParams(
            temperature=temperature,
            top_p=float(kwargs.pop("top_p", 0.9)),
            top_k=int(kwargs.pop("top_k", 40)),
            min_p=float(kwargs.pop("min_p", 0.0)),
            repeat_penalty=float(kwargs.pop("repeat_penalty", 1.1)),
            frequency_penalty=float(kwargs.pop("frequency_penalty", 0.0)),
            presence_penalty=float(kwargs.pop("presence_penalty", 0.0)),
            seed=int(kwargs.pop("seed", -1)),
        )
