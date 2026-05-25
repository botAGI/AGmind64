"""XDNA 2 NPU stub — Ryzen AI SW под Linux не принимает Strix Halo.

См. https://github.com/amd/RyzenAI-SW/issues/366 и ADR-0002 для
backend-контракта.

Stub существует для symmetry — get_backend("npu") возвращает понятный
NotImplementedError, не таинственный AttributeError.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from agmind.compute.base import Backend, DeviceInfo, LLMHandle

_NOT_IMPLEMENTED_MSG = (
    "XDNA 2 NPU on Strix Halo is not supported by Ryzen AI SW on Linux "
    "(see amd/RyzenAI-SW#366). This stub will be replaced when AMD adds "
    "STX-H support."
)


class NPUStubBackend(Backend):
    """NPU placeholder — всегда raises NotImplementedError on actual use."""

    name = "npu"

    def __init__(self, engine: str = "stub") -> None:
        self._engine = engine

    @classmethod
    def available(cls) -> bool:
        """NPU stub НЕ available — `available()` это lightweight runtime check."""
        return False

    @classmethod
    def make(cls, engine: str = "auto") -> NPUStubBackend:
        # Note: даже если engine="auto", make() возвращает stub — но в
        # auto-select из get_backend() этот backend не выбирается
        # потому что `available() → False`.
        return cls(engine="stub")

    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            backend=self.name,
            engine=self._engine,
            device_id=0,
            name="XDNA 2 NPU (stub, not supported on Linux)",
            total_memory_bytes=0,
            capabilities={"reason": _NOT_IMPLEMENTED_MSG},
        )

    def load_llm(self, model_path: str, **kwargs: Any) -> LLMHandle:
        raise NotImplementedError(_NOT_IMPLEMENTED_MSG)

    def embed(self, texts: Sequence[str], model: str) -> list[list[float]]:
        raise NotImplementedError(_NOT_IMPLEMENTED_MSG)

    def rerank(self, query: str, documents: Sequence[str]) -> list[float]:
        raise NotImplementedError(_NOT_IMPLEMENTED_MSG)
