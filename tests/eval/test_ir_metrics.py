"""Phase 18 (M11) — rank-aware IR metrics, the judge-free layer.

These metrics are the milestone's PRIMARY product: an operator with no LLM must still get a
real signal, and the regression gate needs a number that does not drift with judge quality.

Contract under test is AI-SPEC §6.1. The degenerate-case rules matter more than the happy path:
a silently-zero metric is the classic eval lie, so "no anchors" and "empty retrieval" must be
distinguishable in the output, never both collapsed to 0.0.
"""

from __future__ import annotations

import math

import pytest

pytestmark = pytest.mark.backend_any


def _case(case_id: str, anchors, ranked, covers=None, errored: bool = False):
    from agmind.eval.ir import CaseRetrieval

    return CaseRetrieval(
        case_id=case_id,
        anchors=tuple(anchors),
        ranked_chunk_ids=tuple(ranked),
        anchors_by_chunk={c: frozenset(a) for c, a in (covers or {}).items()},
        errored=errored,
    )


# --- per-case scoring -------------------------------------------------------------------


def test_perfect_retrieval_scores_one_everywhere() -> None:
    from agmind.eval.ir import score_case

    c = _case("c1", ["a1", "a2"], ["ch1", "ch2"], {"ch1": ["a1"], "ch2": ["a2"]})
    s = score_case(c, k=5)

    assert s.anchor_recall == 1.0
    assert s.anchor_hit == 1.0
    assert s.anchor_mrr == 1.0
    assert s.anchor_ndcg == pytest.approx(1.0)
    assert s.anchor_precision == 1.0  # 2 relevant / min(5, 2 retrieved)


def test_precision_denominator_is_retrieved_not_k() -> None:
    """min(k, |R|) — a small corpus that returns 2 chunks must not be punished for k=5."""
    from agmind.eval.ir import score_case

    c = _case("c1", ["a1"], ["ch1", "ch2"], {"ch1": ["a1"]})
    s = score_case(c, k=5)
    assert s.anchor_precision == pytest.approx(0.5), "denominator must be 2, not k=5"


def test_ranked_dedup_happens_before_truncation() -> None:
    """A retriever that repeats a chunk must not consume two of the k slots."""
    from agmind.eval.ir import score_case

    # ch1 repeated 3x then the anchor-bearing ch9 at raw rank 4
    c = _case("c1", ["a1"], ["ch1", "ch1", "ch1", "ch9"], {"ch9": ["a1"]})
    s = score_case(c, k=2)

    assert s.anchor_recall == 1.0, "after dedup ch9 sits at rank 2 and is inside k=2"
    assert s.anchor_mrr == pytest.approx(0.5), "first relevant at deduped rank 2"
    assert s.retrieved_considered == 2


def test_truncation_at_k_excludes_later_hits() -> None:
    from agmind.eval.ir import score_case

    c = _case("c1", ["a1"], ["ch1", "ch2", "ch3"], {"ch3": ["a1"]})
    assert score_case(c, k=2).anchor_recall == 0.0
    assert score_case(c, k=3).anchor_recall == 1.0


def test_mrr_uses_first_relevant_rank() -> None:
    from agmind.eval.ir import score_case

    c = _case("c1", ["a1"], ["ch1", "ch2", "ch3"], {"ch3": ["a1"]})
    assert score_case(c, k=5).anchor_mrr == pytest.approx(1 / 3)


def test_partial_anchor_coverage() -> None:
    from agmind.eval.ir import score_case

    c = _case("c1", ["a1", "a2", "a3"], ["ch1"], {"ch1": ["a1"]})
    s = score_case(c, k=5)
    assert s.anchor_recall == pytest.approx(1 / 3)
    assert s.anchor_hit == 1.0


def test_one_chunk_covering_several_anchors_counts_once_for_precision() -> None:
    """Precision counts RELEVANT CHUNKS; recall counts COVERED ANCHORS. Do not conflate."""
    from agmind.eval.ir import score_case

    c = _case("c1", ["a1", "a2"], ["ch1"], {"ch1": ["a1", "a2"]})
    s = score_case(c, k=5)
    assert s.anchor_recall == 1.0
    assert s.anchor_precision == 1.0  # 1 relevant chunk / 1 retrieved


def test_ndcg_idcg_uses_min_k_and_anchor_count() -> None:
    """IDCG over min(k, |A|) — an IDCG over k would silently under-score every case with
    fewer anchors than k (AI-SPEC §6.1)."""
    from agmind.eval.ir import score_case

    # 1 anchor, found at rank 2 → DCG = 1/log2(3); ideal = 1 relevant at rank 1 → IDCG = 1/log2(2)=1
    c = _case("c1", ["a1"], ["ch1", "ch2"], {"ch2": ["a1"]})
    s = score_case(c, k=5)
    assert s.anchor_ndcg == pytest.approx(1 / math.log2(3))


def test_ndcg_is_one_when_all_relevant_are_ranked_first() -> None:
    from agmind.eval.ir import score_case

    c = _case("c1", ["a1", "a2"], ["ch1", "ch2", "ch3"], {"ch1": ["a1"], "ch2": ["a2"]})
    assert score_case(c, k=3).anchor_ndcg == pytest.approx(1.0)


# --- degenerate cases: the whole point ---------------------------------------------------


def test_case_without_anchors_is_skipped_not_zero() -> None:
    from agmind.eval.ir import score_case

    s = score_case(_case("c1", [], ["ch1"]), k=5)
    assert s.skipped_no_anchors is True
    assert s.anchor_recall is None, "a case with no anchors must not contribute a 0.0"


def test_empty_retrieval_is_a_real_zero() -> None:
    from agmind.eval.ir import score_case

    s = score_case(_case("c1", ["a1"], []), k=5)
    assert s.empty_retrieval is True
    assert s.skipped_no_anchors is False
    assert s.anchor_recall == 0.0, "returning nothing IS a failure, not a skip"
    assert s.anchor_precision == 0.0
    assert s.anchor_ndcg == 0.0
    assert s.anchor_mrr == 0.0


def test_errored_case_is_not_scored() -> None:
    from agmind.eval.ir import score_case

    s = score_case(_case("c1", ["a1"], [], errored=True), k=5)
    assert s.errored is True
    assert s.anchor_recall is None


def test_k_larger_than_retrieved_is_fine() -> None:
    from agmind.eval.ir import score_case

    s = score_case(_case("c1", ["a1"], ["ch1"], {"ch1": ["a1"]}), k=100)
    assert s.anchor_recall == 1.0
    assert s.retrieved_considered == 1


def test_k_must_be_positive() -> None:
    from agmind.eval.ir import score_case

    with pytest.raises(ValueError, match="k must be >= 1"):
        score_case(_case("c1", ["a1"], ["ch1"]), k=0)


# --- aggregation ------------------------------------------------------------------------


def test_aggregate_is_macro_over_cases() -> None:
    """Macro, never micro: a case with 20 anchors must not drown the rest (AI-SPEC §6.1)."""
    from agmind.eval.ir import aggregate, score_case

    many = _case("many", [f"a{i}" for i in range(20)], ["ch1"], {"ch1": ["a0"]})  # recall 0.05
    few = _case("few", ["b1"], ["ch1"], {"ch1": ["b1"]})  # recall 1.0
    agg = aggregate([score_case(many, k=5), score_case(few, k=5)])

    assert agg.anchor_recall == pytest.approx((0.05 + 1.0) / 2)
    assert agg.cases_scored == 2


def test_aggregate_counters_separate_skip_empty_and_error() -> None:
    from agmind.eval.ir import aggregate, score_case

    scored = [
        score_case(_case("ok", ["a"], ["c"], {"c": ["a"]}), k=5),
        score_case(_case("noanchor", [], ["c"]), k=5),
        score_case(_case("empty", ["a"], []), k=5),
        score_case(_case("boom", ["a"], [], errored=True), k=5),
    ]
    agg = aggregate(scored)

    assert agg.cases_scored == 2, "only ok + empty are scoreable"
    assert agg.cases_skipped_no_anchors == 1
    assert agg.cases_empty_retrieval == 1
    assert agg.cases_errored == 1
    # ok=1.0, empty=0.0 → macro mean 0.5; the skipped/errored cases contribute nothing
    assert agg.anchor_recall == pytest.approx(0.5)


def test_aggregate_of_nothing_scoreable_yields_none_not_zero() -> None:
    from agmind.eval.ir import aggregate, score_case

    agg = aggregate([score_case(_case("boom", ["a"], [], errored=True), k=5)])
    assert agg.cases_scored == 0
    assert agg.anchor_recall is None, "no data must read as 'no data', never as 0.0"


def test_aggregate_exposes_per_case_values_for_bootstrap() -> None:
    """stats.py resamples CASES, so the aggregate must carry the per-case vector."""
    from agmind.eval.ir import aggregate, score_case

    agg = aggregate(
        [
            score_case(_case("a", ["x"], ["c"], {"c": ["x"]}), k=5),
            score_case(_case("b", ["x"], ["c"]), k=5),
        ]
    )
    assert agg.per_case["anchor_recall"] == (1.0, 0.0)


def test_f1_is_harmonic_mean_and_zero_safe() -> None:
    from agmind.eval.ir import aggregate, score_case

    s = score_case(_case("c1", ["a1", "a2"], ["ch1", "ch2"], {"ch1": ["a1"]}), k=5)
    # precision = 1 relevant / 2 retrieved = 0.5 ; recall = 1/2 = 0.5 → f1 = 0.5
    assert s.f1 == pytest.approx(0.5)

    zero = score_case(_case("c2", ["a1"], ["ch1"]), k=5)
    assert zero.f1 == 0.0, "0/0 must be 0.0, not a ZeroDivisionError"

    assert aggregate([s, zero]).f1 == pytest.approx(0.25)
