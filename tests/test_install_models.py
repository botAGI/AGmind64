"""Phase N.G: tests for agmind.install.models catalog + wizard helpers."""

from __future__ import annotations

import pytest

from agmind.install.models import (
    CTX_SIZE_PRESETS,
    CURATED_MODELS,
    KV_CACHE_TYPES,
    ModelEntry,
    default_model_id,
    find_by_id,
    models_for_wizard,
)

pytestmark = pytest.mark.backend_any


# ---------- catalog hygiene ----------


def test_catalog_not_empty() -> None:
    assert len(CURATED_MODELS) >= 3


def test_unique_ids() -> None:
    ids = [m.id for m in CURATED_MODELS]
    assert len(ids) == len(set(ids)), f"duplicate model ids: {ids}"


def test_all_have_required_fields() -> None:
    for m in CURATED_MODELS:
        assert m.id and m.repo and m.file and m.quant
        assert m.size_gib > 0
        assert m.params_b > 0
        assert m.suggested_ctx >= 1024


def test_strix_tested_have_measured_tps() -> None:
    """Если флаг strix_tested стоит — measured_tg_t_s обязан быть set."""
    for m in CURATED_MODELS:
        if m.strix_tested:
            assert m.measured_tg_t_s is not None, f"{m.id} tested но без measured_tg_t_s"
            assert m.measured_tg_t_s > 0


def test_moe_models_declare_active_params() -> None:
    """Для MoE моделей active_params_b должен быть < total params."""
    for m in CURATED_MODELS:
        if m.active_params_b is not None:
            assert m.active_params_b < m.params_b


# ---------- find_by_id ----------


def test_find_by_id_known() -> None:
    m = find_by_id("qwen36-a3b-q4km")
    assert m is not None
    assert m.repo == "0xSero/Qwen3.6-35B-A3B-GGUF-Strix"
    assert m.file == "Qwen3.6-35B-A3B-Q4_K_M.gguf"


def test_find_by_id_unknown() -> None:
    assert find_by_id("does-not-exist") is None


def test_find_by_id_default_resolves() -> None:
    assert find_by_id(default_model_id()) is not None


# ---------- models_for_wizard ----------


def test_models_for_wizard_returns_pairs() -> None:
    out = models_for_wizard()
    assert len(out) == len(CURATED_MODELS)
    for display, mid in out:
        assert isinstance(display, str)
        assert isinstance(mid, str)
        assert find_by_id(mid) is not None


def test_models_for_wizard_tested_models_first() -> None:
    """Strix-tested models должны быть выше non-tested в порядке."""
    out = models_for_wizard()
    tested_indices = [i for i, (_, mid) in enumerate(out) if find_by_id(mid).strix_tested]
    untested_indices = [i for i, (_, mid) in enumerate(out) if not find_by_id(mid).strix_tested]
    if tested_indices and untested_indices:
        assert max(tested_indices) < min(untested_indices)


def test_model_display_includes_size_and_tps() -> None:
    m = find_by_id("qwen36-a3b-q4km")
    assert m is not None
    d = m.display
    assert "21.2 GB" in d
    assert "t/s" in d


# ---------- ctx + kv presets ----------


def test_ctx_presets_monotonic() -> None:
    sizes = [n for n, _ in CTX_SIZE_PRESETS]
    assert sizes == sorted(sizes)
    assert sizes[0] >= 1024
    assert sizes[-1] >= 65536


def test_kv_cache_q8_recommended_first() -> None:
    assert KV_CACHE_TYPES[0][0] == "q8_0"
