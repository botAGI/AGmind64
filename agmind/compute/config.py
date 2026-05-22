"""Чтение конфигурации compute layer из env vars.

Поддерживаемые переменные:

| Var                       | Allowed                                       | Default |
|---------------------------|-----------------------------------------------|---------|
| AGMIND_BACKEND            | auto / vulkan / rocm / cpu / npu              | auto    |
| AGMIND_ENGINE             | auto / llama_cpp / vllm / infinity            | auto    |
| AGMIND_DEVICE_ID          | int (≥0)                                      | 0       |
| AGMIND_BACKEND_PROFILE    | tg / pp / mixed / embed_single / embed_batch  | mixed   |

См. AGMIND_MIGRATION_SPEC.md §1.2.6 (selection rules).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from typing import Final

_ALLOWED_BACKENDS: Final = frozenset({"auto", "vulkan", "rocm", "cpu", "npu"})
_ALLOWED_ENGINES: Final = frozenset({"auto", "llama_cpp", "vllm", "infinity", "stub"})


class Profile(str, Enum):
    """Workload profile — влияет на auto-select backend/engine."""

    TG = "tg"
    """Token generation: single-user chat, short context."""

    PP = "pp"
    """Prompt processing: long-context prefill, RAG with big prompts."""

    MIXED = "mixed"
    """Default: tg + occasional pp, без strong bias."""

    EMBED_SINGLE = "embed_single"
    """Embedding с batch ≤4."""

    EMBED_BATCH = "embed_batch"
    """High-throughput embedding ≥4 batch — Infinity territory."""


@dataclass(frozen=True)
class ComputeConfig:
    """Resolved runtime config (после env read)."""

    backend: str = "auto"
    engine: str = "auto"
    device_id: int = 0
    profile: Profile = Profile.MIXED
    llama_server_url: str = ""
    """Optional HTTP URL для running llama-server. Если задан — backends
    используют HTTP клиент (LlamaServerHandle) вместо in-process Llama().
    Set via AGMIND_LLAMA_SERVER_URL=http://llama-llm:8080."""


def _read_str(key: str, allowed: frozenset[str], default: str) -> str:
    val = os.environ.get(key, default).strip().lower()
    if val not in allowed:
        raise ValueError(f"{key}={val!r} not in {sorted(allowed)}")
    return val


def _read_profile() -> Profile:
    raw = os.environ.get("AGMIND_BACKEND_PROFILE", "mixed").strip().lower()
    try:
        return Profile(raw)
    except ValueError as exc:
        allowed = [p.value for p in Profile]
        raise ValueError(f"AGMIND_BACKEND_PROFILE={raw!r} not in {allowed}") from exc


def _read_device_id() -> int:
    raw = os.environ.get("AGMIND_DEVICE_ID", "0").strip()
    try:
        val = int(raw)
    except ValueError as exc:
        raise ValueError(f"AGMIND_DEVICE_ID={raw!r} is not an int") from exc
    if val < 0:
        raise ValueError(f"AGMIND_DEVICE_ID={val} must be ≥ 0")
    return val


def read_config() -> ComputeConfig:
    """Read all AGMIND_* env vars, validate, return resolved config."""
    return ComputeConfig(
        backend=_read_str("AGMIND_BACKEND", _ALLOWED_BACKENDS, "auto"),
        engine=_read_str("AGMIND_ENGINE", _ALLOWED_ENGINES, "auto"),
        device_id=_read_device_id(),
        profile=_read_profile(),
        llama_server_url=os.environ.get("AGMIND_LLAMA_SERVER_URL", "").strip(),
    )
