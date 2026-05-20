"""Phase N.G: curated model catalog для wizard выбора.

Каждая запись — verified GGUF model на Hugging Face: repo_id + filename +
размер + рекомендованный контекст + краткое описание. Catalog не претендует
на полноту — это short list known-good options для Strix Halo / x86 LLM
inference. Кастомный HF model id user может ввести через "Custom..." вариант
в wizard.

Все размеры в GiB approximate. Verify через HF API или WebFetch перед
финальной публикацией, не выдумывать (см. feedback_no_guessing).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ModelKind = Literal["llm", "embed", "rerank"]


@dataclass(frozen=True)
class ModelEntry:
    """One curated GGUF model recommendation."""

    id: str  # short stable id ("qwen36-a3b-q4km", "llama33-70b-q4km")
    name: str  # human display name
    repo: str  # HF repo id ("0xSero/Qwen3.6-35B-A3B-GGUF-Strix")
    file: str  # GGUF filename
    size_gib: float  # approximate disk size
    params_b: float  # total parameters in billions
    active_params_b: float | None  # for MoE — active params (else None)
    quant: str  # "Q4_K_M", "Q8_0", "BF16", etc.
    suggested_ctx: int  # recommended ctx-size for Strix Halo
    description: str  # one-liner shown в wizard
    kind: ModelKind = "llm"
    strix_tested: bool = False  # measured на нашем железе в R-recon
    measured_tg_t_s: float | None = None  # tg128 t/s если tested

    @property
    def display(self) -> str:
        suffix = ""
        if self.measured_tg_t_s is not None:
            suffix = f"  ·  {self.measured_tg_t_s:.0f} t/s"
        elif self.strix_tested:
            suffix = "  ·  tested"
        return f"{self.name}  [{self.size_gib:.1f} GB]{suffix}"


# Curated list — verified репозитории на HF (см. R3/R15/R16 recons).
# IMPORTANT: добавление сюда требует verify через WebFetch / curl HF.
CURATED_MODELS: tuple[ModelEntry, ...] = (
    # ---- MoE (recommended for Strix Halo) ----
    ModelEntry(
        id="qwen36-a3b-q4km",
        name="Qwen3.6-35B-A3B (MoE)",
        repo="0xSero/Qwen3.6-35B-A3B-GGUF-Strix",
        file="Qwen3.6-35B-A3B-Q4_K_M.gguf",
        size_gib=21.2,
        params_b=34.66,
        active_params_b=3.0,
        quant="Q4_K_M",
        suggested_ctx=16384,
        description="Chat/reasoning MoE — production sweet spot, Phase H measured.",
        strix_tested=True,
        measured_tg_t_s=73.5,
    ),
    ModelEntry(
        id="qwen36-a3b-q4_0",
        name="Qwen3.6-35B-A3B (MoE)",
        repo="0xSero/Qwen3.6-35B-A3B-GGUF-Strix",
        file="Qwen3.6-35B-A3B-Q4_0.gguf",
        size_gib=19.7,
        params_b=34.66,
        active_params_b=3.0,
        quant="Q4_0",
        suggested_ctx=16384,
        description="Same model — faster decode (76 t/s), slightly worse quality vs Q4_K_M.",
        strix_tested=True,
        measured_tg_t_s=76.5,
    ),
    ModelEntry(
        id="qwen36-a3b-dyn",
        name="Qwen3.6-35B-A3B (MoE, DYNAMIC mix)",
        repo="0xSero/Qwen3.6-35B-A3B-GGUF-Strix",
        file="Qwen3.6-35B-A3B-DYNAMIC.gguf",
        size_gib=19.0,
        params_b=34.66,
        active_params_b=3.0,
        quant="DYNAMIC",
        suggested_ctx=16384,
        description="Mixed precision — fastest prefill (1100 t/s), 64 t/s decode.",
        strix_tested=True,
        measured_tg_t_s=64.0,
    ),
    # ---- Dense (smaller) ----
    ModelEntry(
        id="llama2-7b-q4_0",
        name="Llama-2-7B",
        repo="TheBloke/Llama-2-7B-GGUF",
        file="llama-2-7b.Q4_0.gguf",
        size_gib=3.83,
        params_b=7.0,
        active_params_b=None,
        quant="Q4_0",
        suggested_ctx=4096,
        description="Light baseline для smoke / CI; community reference 52 t/s.",
        strix_tested=False,
    ),
    ModelEntry(
        id="llama2-7b-q4km",
        name="Llama-2-7B",
        repo="TheBloke/Llama-2-7B-GGUF",
        file="llama-2-7b.Q4_K_M.gguf",
        size_gib=4.08,
        params_b=7.0,
        active_params_b=None,
        quant="Q4_K_M",
        suggested_ctx=4096,
        description="Better quality 7B baseline (recommended by TheBloke).",
        strix_tested=False,
    ),
    # ---- Embeddings / Rerank (kind=embed/rerank) ----
    ModelEntry(
        id="bge-m3-q8",
        name="BGE-M3 (multilingual embed)",
        repo="lm-kit/bge-m3-gguf",
        file="bge-m3-Q8_0.gguf",
        size_gib=0.6,
        params_b=0.5,
        active_params_b=None,
        quant="Q8_0",
        suggested_ctx=8192,
        description="Dense embedding for RAG (1024-dim, multilingual).",
        kind="embed",
    ),
)


# Context-size presets shown в wizard. Real model max может быть выше
# (Qwen3.6 supports 260K) но 16K-65K — sweet spot для production memory budget.
CTX_SIZE_PRESETS: tuple[tuple[int, str], ...] = (
    (4096, "4K — minimal (fast, low VRAM)"),
    (8192, "8K — chat conversations"),
    (16384, "16K — default (recommended)"),
    (32768, "32K — long documents"),
    (65536, "64K — codebase / very long context"),
    (131072, "128K — extreme (требует много VRAM/GTT)"),
)


# KV cache quantization options. q8_0 — sweet spot для Strix Halo per
# R16 recon (saves ~50% memory без noticeable quality loss).
KV_CACHE_TYPES: tuple[tuple[str, str], ...] = (
    ("q8_0", "q8_0 — recommended (8-bit, ~50% memory saving)"),
    ("q4_0", "q4_0 — aggressive (4-bit, may degrade на длинных ctx)"),
    ("f16", "f16 — full precision (default llama.cpp)"),
)


def find_by_id(model_id: str) -> ModelEntry | None:
    """Lookup curated model by short id."""
    for m in CURATED_MODELS:
        if m.id == model_id:
            return m
    return None


def models_for_wizard() -> list[tuple[str, str]]:
    """Returns list of (display, id) для Textual Select widget.

    Sorted: strix_tested first (recommended), потом kind=llm size desc.
    "Custom" вариант добавляется wizard'ом отдельно (не здесь — id collision risk).
    """
    sortable = sorted(
        CURATED_MODELS,
        key=lambda m: (
            0 if m.strix_tested else 1,
            0 if m.kind == "llm" else 1,
            -m.size_gib,
        ),
    )
    return [(m.display, m.id) for m in sortable]


def default_model_id() -> str:
    """Recommended default — current Phase H verified model."""
    return "qwen36-a3b-q4km"


__all__ = [
    "CTX_SIZE_PRESETS",
    "CURATED_MODELS",
    "KV_CACHE_TYPES",
    "ModelEntry",
    "ModelKind",
    "default_model_id",
    "find_by_id",
    "models_for_wizard",
]
