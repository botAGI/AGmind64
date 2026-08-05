"""Phase 18 (M11) — anchor normalisation and containment (AI-SPEC §5.2).

Anchor matching IS the relevance decision for layer 1, so its edge cases are the difference
between a real measurement and a number that quietly always says 1.0.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.backend_any


# --- normalisation ----------------------------------------------------------------------


def test_normalize_is_case_insensitive() -> None:
    from agmind.eval.anchors import normalize

    assert normalize("MEM_INFO_GTT_TOTAL") == normalize("mem_info_gtt_total")


def test_normalize_collapses_wrapped_whitespace() -> None:
    """Chunkers reflow line breaks; a multi-word anchor must survive the wrap."""
    from agmind.eval.anchors import normalize

    assert normalize("Above 4G\n   Decoding") == "above 4g decoding"


def test_normalize_folds_nbsp_and_fullwidth() -> None:
    """NFKC folds what a markdown -> chunker round trip leaves behind."""
    from agmind.eval.anchors import normalize

    assert normalize("agmind doctor") == "agmind doctor"
    assert normalize("ＡＧＭＩＮＤ") == "agmind"


def test_normalize_handles_cyrillic_casefold() -> None:
    """The corpus is bilingual; casefold (not lower) is the Unicode-correct operation."""
    from agmind.eval.anchors import normalize

    assert normalize("ЯДРО") == normalize("ядро")


# --- containment ------------------------------------------------------------------------


def test_contains_anchor_finds_identifier_inside_prose() -> None:
    from agmind.eval.anchors import contains_anchor

    chunk = "agmind при старте детектит `mem_info_gtt_total` через /sys/class/drm/cardN/device/"
    assert contains_anchor(chunk, "mem_info_gtt_total") is True


def test_contains_anchor_matches_across_a_line_break() -> None:
    from agmind.eval.anchors import contains_anchor

    assert contains_anchor("set Above 4G\nDecoding to Enabled", "Above 4G Decoding") is True


def test_contains_anchor_is_false_for_absent_text() -> None:
    from agmind.eval.anchors import contains_anchor

    assert contains_anchor("nothing relevant here", "mem_info_gtt_total") is False


def test_empty_anchor_never_matches() -> None:
    """An empty anchor would make every chunk relevant and silently pin the metric at 1.0."""
    from agmind.eval.anchors import contains_anchor

    assert contains_anchor("any text at all", "") is False
    assert contains_anchor("any text at all", "   ") is False


def test_anchor_matching_is_substring_not_token() -> None:
    """Deliberate: identifiers appear glued to punctuation/backticks in real chunks."""
    from agmind.eval.anchors import contains_anchor

    assert contains_anchor("use `AGMIND_OFFLINE=1` to skip", "AGMIND_OFFLINE=1") is True


# --- coverage ---------------------------------------------------------------------------


def test_anchors_covered_returns_original_spelling() -> None:
    """Matching happens on the normalised form; reports must show the author's spelling."""
    from agmind.eval.anchors import anchors_covered

    covered = anchors_covered("value of MEM_INFO_GTT_TOTAL here", ["mem_info_gtt_total"])
    assert covered == frozenset({"mem_info_gtt_total"})


def test_anchors_covered_subset_only() -> None:
    from agmind.eval.anchors import anchors_covered

    covered = anchors_covered("only ttm.pages_limit appears", ["ttm.pages_limit", "amd_iommu=off"])
    assert covered == frozenset({"ttm.pages_limit"})


def test_coverage_map_feeds_the_metrics_module() -> None:
    from agmind.eval.anchors import coverage_map

    chunks = {"c1": "mentions amd_iommu=off", "c2": "unrelated", "c3": "has ttm.pages_limit"}
    result = coverage_map(chunks, ["amd_iommu=off", "ttm.pages_limit"])

    assert result["c1"] == frozenset({"amd_iommu=off"})
    assert result["c2"] == frozenset()
    assert result["c3"] == frozenset({"ttm.pages_limit"})


def test_coverage_map_output_plugs_into_score_case() -> None:
    """End-to-end shape check: anchors -> ir.score_case without an adapter."""
    from agmind.eval.anchors import coverage_map
    from agmind.eval.ir import CaseRetrieval, score_case

    chunks = {"c1": "irrelevant", "c2": "contains mem_info_gtt_total here"}
    covers = coverage_map(chunks, ["mem_info_gtt_total"])

    score = score_case(
        CaseRetrieval(
            case_id="t1",
            anchors=("mem_info_gtt_total",),
            ranked_chunk_ids=("c1", "c2"),
            anchors_by_chunk=covers,
        ),
        k=5,
    )
    assert score.anchor_recall == 1.0
    assert score.anchor_mrr == pytest.approx(0.5)
