"""Phase 09-08 (M8): BENCHMARKS.md keeps the numbers, loses the marketing conclusions.

The cross-architecture rows mix engines (FP8 vLLM vs Q4_K_M llama.cpp) and quantizations and
are not apples-to-apples; the doc once drew "обгоняет"/"migration validated"-style verdicts
from them. This gate blocks those conclusion phrases from regressing."""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.backend_any

_DOC = Path(__file__).resolve().parents[2] / "docs" / "BENCHMARKS.md"

# Marketing-conclusion phrases that overstate a non-apples-to-apples comparison.
_BANNED = ["migration validated", "обгоня", "обходит"]


def test_benchmarks_has_no_marketing_conclusions() -> None:
    text = _DOC.read_text(encoding="utf-8").lower()
    hits = [phrase for phrase in _BANNED if phrase in text]
    assert not hits, f"BENCHMARKS.md reads as marketing, not measurement: {hits}"


def test_benchmarks_keeps_apples_caveat() -> None:
    text = _DOC.read_text(encoding="utf-8").lower()
    assert "not apples-to-apples" in text or "apples-to-apples" in text
