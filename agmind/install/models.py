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

from agmind.models import (
    CuratedModelEntry as ModelEntry,
)
from agmind.models import (
    ModelKind,
    load_curated_model_entries,
    load_model_catalog_defaults,
)

CURATED_MODELS: tuple[ModelEntry, ...] = load_curated_model_entries()


# Context-size presets shown в wizard. Real model max может быть выше
# (Qwen3.6 supports 260K) но 16K-65K — sweet spot для production memory budget.
CTX_SIZE_PRESETS: tuple[tuple[int, str], ...] = (
    (1024, "1K — minimal (rerank cross-encoder)"),
    (2048, "2K — rerank default"),
    (4096, "4K — minimal LLM (fast, low VRAM)"),
    (8192, "8K — chat / embed default"),
    (16384, "16K — LLM default (recommended)"),
    (32768, "32K — long documents"),
    (65536, "64K — codebase / long ctx"),
    (131072, "128K — long-form (~21 GB KV q8_0)"),
    (262144, "256K — Qwen3.6 native max (~43 GB KV q8_0 — fits Strix Halo)"),
    (524288, "512K — beyond-native (нужен RoPE+YaRN, q4_0 KV; quality risk)"),
)


# KV cache quantization options. q8_0 — sweet spot для Strix Halo per
# R16 recon (saves ~50% memory без noticeable quality loss).
KV_CACHE_TYPES: tuple[tuple[str, str], ...] = (
    ("q8_0", "q8_0 — recommended (8-bit, ~50% memory saving)"),
    ("q4_0", "q4_0 — aggressive (4-bit, may degrade на длинных ctx)"),
    ("f16", "f16 — full precision (default llama.cpp)"),
)


# CPU threads — relevant для MoE / small models. Strix Halo = 16C/32T,
# llama-server default = всё доступное. -1 = auto.
THREADS_PRESETS: tuple[tuple[int, str], ...] = (
    (-1, "auto (use all available cores)"),
    (8, "8 threads — minimal"),
    (16, "16 threads — Strix Halo balanced"),
    (32, "32 threads — Strix Halo max (HT)"),
)


# Parallel slots — для concurrent serving. >1 enables continuous batching
# (llama-server multiplexes N requests). Trade-off: VRAM × N.
PARALLEL_PRESETS: tuple[tuple[int, str], ...] = (
    (1, "1 — serial (default, безопасно)"),
    (2, "2 — light concurrency"),
    (4, "4 — moderate (нужно ~2× ctx VRAM)"),
    (8, "8 — heavy multi-tenant (нужно ~4× ctx VRAM)"),
)


def find_by_id(model_id: str) -> ModelEntry | None:
    """Lookup curated model by short id."""
    for m in CURATED_MODELS:
        if m.id == model_id:
            return m
    return None


def models_for_wizard(kind: ModelKind | None = None) -> list[tuple[str, str]]:
    """Returns list of (display, id) для Textual Select widget.

    Args:
        kind: filter — "llm" / "embed" / "rerank". None = all (legacy bulk list).

    Sorted: strix_tested first (recommended), потом size desc.
    "Custom" вариант добавляется wizard'ом отдельно (не здесь — id collision risk).
    """
    pool = CURATED_MODELS if kind is None else tuple(m for m in CURATED_MODELS if m.kind == kind)
    sortable = sorted(
        pool,
        key=lambda m: (
            0 if m.strix_tested else 1,
            0 if m.kind == "llm" else (1 if m.kind == "embed" else 2),
            -m.size_gib,
        ),
    )
    return [(m.display, m.id) for m in sortable]


# Per-kind defaults come from templates/models.yaml::wizard_catalog.
_KIND_DEFAULTS: dict[str, str] = load_model_catalog_defaults()


def default_model_id(kind: ModelKind | None = None) -> str:
    """Recommended default model id для wizard initial value.

    kind=None / "llm" → Phase H verified LLM baseline.
    """
    if kind is None:
        return _KIND_DEFAULTS["llm"]
    return _KIND_DEFAULTS.get(kind, "custom")


__all__ = [
    "CTX_SIZE_PRESETS",
    "CURATED_MODELS",
    "KV_CACHE_TYPES",
    "PARALLEL_PRESETS",
    "THREADS_PRESETS",
    "ModelEntry",
    "ModelKind",
    "default_model_id",
    "find_by_id",
    "models_for_wizard",
]
