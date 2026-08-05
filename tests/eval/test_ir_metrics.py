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


# --- graded nDCG: units bug found by the 2026 research sweep --------------------------------


def test_ndcg_never_exceeds_one_when_an_anchor_spans_several_chunks() -> None:
    """REGRESSION (shipped bug): DCG counted CHUNKS while IDCG normalised over ANCHORS — two
    different units — so any case whose anchor appears in more than one retrieved chunk scored
    above 1.0 (measured 1.63 with 2 of 3 chunks, 2.13 with 3 of 3). On the real corpus 6 of 13
    golden anchors occur in several chunks, so this fired constantly."""
    from agmind.eval.ir import score_case

    for hits in (2, 3):
        covers = {f"c{i}": frozenset({"a1"}) for i in range(1, hits + 1)}
        case = _case("multi", ["a1"], [f"c{i}" for i in range(1, 4)], covers)
        score = score_case(case, k=5)
        assert score.anchor_ndcg is not None
        assert 0.0 <= score.anchor_ndcg <= 1.0, f"{hits} hits -> nDCG {score.anchor_ndcg}"


def test_ndcg_uses_graded_gains_from_anchor_coverage() -> None:
    """A chunk covering TWO anchors is worth more than one covering a single anchor — that is
    what makes numerator and denominator the same unit (TREC 2025 RAG grades a segment by how
    many sub-narratives it covers)."""
    from agmind.eval.ir import score_case

    # The corpus contains ONE chunk covering both anchors, so the ideal ranking has gain 2 at
    # rank 1. Against that ideal, finding that chunk beats spreading the anchors over two chunks.
    # (Without a corpus-wide ideal both orderings are optimal for what they retrieved and score
    # 1.0 — correct for the fallback, which measures ordering only.)
    ideal = (2,)
    rich = _case("rich", ["a1", "a2"], ["c1", "c2"], {"c1": ["a1", "a2"]})
    thin = _case("thin", ["a1", "a2"], ["c1", "c2"], {"c1": ["a1"], "c2": ["a2"]})
    rich_score = score_case(rich, k=5, ideal_gains=ideal).anchor_ndcg
    thin_score = score_case(thin, k=5, ideal_gains=ideal).anchor_ndcg
    assert rich_score is not None and thin_score is not None
    assert rich_score > thin_score, "front-loading both anchors must rank above spreading them"
    assert rich_score == pytest.approx(1.0)
    assert thin_score < 1.0


def test_ndcg_with_corpus_wide_ideal_is_bounded_and_penalises_misses() -> None:
    """With the frozen corpus we can compute the EXACT ideal ranking, so nDCG measures finding
    AND ordering rather than only ordering what happened to be found."""
    from agmind.eval.ir import score_case

    # corpus knows 3 anchor-bearing chunks (gains 1,1,1) but retrieval surfaced one of them
    case = _case("partial", ["a1", "a2", "a3"], ["c9", "c1"], {"c1": ["a1"]})
    score = score_case(case, k=5, ideal_gains=(1, 1, 1))
    assert score.anchor_ndcg is not None
    assert 0.0 < score.anchor_ndcg < 1.0


# --- negative cases are scored, not discarded ----------------------------------------------


def test_negative_case_is_scored_not_skipped_as_malformed() -> None:
    """The abstention class is the whole trust proposition of a self-hosted stack; it used to be
    counted as 'malformed, the integrity gate should have rejected it' and contributed nothing."""
    from agmind.eval.ir import CaseRetrieval, score_case

    case = CaseRetrieval(
        case_id="neg", anchors=(), ranked_chunk_ids=("c1",), scores=(0.11,), negative=True
    )
    score = score_case(case, k=5, abstain_threshold=0.5)

    assert score.negative is True
    assert score.skipped_no_anchors is False, "a deliberate negative is not an authoring mistake"
    assert score.abstained is True, "top score below threshold == correctly abstained"


def test_negative_case_fails_when_retriever_returns_confident_junk() -> None:
    from agmind.eval.ir import CaseRetrieval, score_case

    case = CaseRetrieval(
        case_id="neg", anchors=(), ranked_chunk_ids=("c1",), scores=(0.93,), negative=True
    )
    assert score_case(case, k=5, abstain_threshold=0.5).abstained is False


def test_aggregate_reports_abstention_separately_from_recall() -> None:
    from agmind.eval.ir import CaseRetrieval, aggregate, score_case

    positive = score_case(_case("p", ["a"], ["c"], {"c": ["a"]}), k=5)
    good_neg = score_case(
        CaseRetrieval("n1", (), ("c",), scores=(0.1,), negative=True), k=5, abstain_threshold=0.5
    )
    bad_neg = score_case(
        CaseRetrieval("n2", (), ("c",), scores=(0.9,), negative=True), k=5, abstain_threshold=0.5
    )
    agg = aggregate([positive, good_neg, bad_neg])

    assert agg.cases_negative == 2
    assert agg.cases_skipped_no_anchors == 0, "negatives must not land in the malformed bucket"
    assert agg.abstention_rate == pytest.approx(0.5)
    assert agg.anchor_recall == 1.0, "negatives must not dilute the retrieval metric"


# --- abstention must be measurable, not assumed (adversarial review) -----------------------


def test_abstention_is_unmeasured_not_success_without_a_threshold() -> None:
    """REGRESSION: `abstained` defaulted to True whenever no threshold was given, so a run with
    no threshold scored a perfect abstention rate without measuring anything."""
    from agmind.eval.ir import CaseRetrieval, score_case

    case = CaseRetrieval("n", (), ("c1",), scores=(0.9,), negative=True)
    assert score_case(case, k=5, abstain_threshold=None).abstained is None


def test_abstention_is_unmeasured_when_the_retriever_reports_no_scores() -> None:
    """A retriever that returns chunks but no scores cannot be judged on abstention — claiming
    success there rewards returning nothing measurable."""
    from agmind.eval.ir import CaseRetrieval, score_case

    case = CaseRetrieval("n", (), ("c1",), scores=(), negative=True)
    assert score_case(case, k=5, abstain_threshold=0.5).abstained is None


def test_unmeasured_negatives_are_excluded_from_the_abstention_denominator() -> None:
    from agmind.eval.ir import CaseRetrieval, aggregate, score_case

    good = score_case(
        CaseRetrieval("n1", (), ("c",), scores=(0.1,), negative=True), k=5, abstain_threshold=0.5
    )
    unmeasured = score_case(
        CaseRetrieval("n2", (), ("c",), scores=(), negative=True), k=5, abstain_threshold=0.5
    )
    agg = aggregate([good, unmeasured])

    assert agg.cases_negative == 2
    assert agg.cases_abstention_unmeasured == 1
    assert agg.abstention_rate == 1.0, "1 of 1 MEASURABLE negative, not 1 of 2"


def test_negative_scores_are_indexed_by_original_rank_not_a_dedup_slice() -> None:
    """REGRESSION: scores were sliced by the post-dedup length against the pre-dedup list, so a
    repeated chunk id silently paired a chunk with a different chunk's score."""
    from agmind.eval.ir import CaseRetrieval, score_case

    # raw ranks:  c1(0.1) c1(0.1) c9(0.95)  -> deduped ids [c1, c9], original positions [0, 2]
    case = CaseRetrieval("n", (), ("c1", "c1", "c9"), scores=(0.1, 0.1, 0.95), negative=True)
    score = score_case(case, k=5, abstain_threshold=0.5)
    assert score.abstained is False, (
        "c9 scores 0.95 and must be seen; a length-2 slice would only read 0.1, 0.1 and "
        "wrongly report a correct abstention"
    )


def test_false_abstention_is_counted_on_answerable_cases() -> None:
    """Abstention without its paired false-decline rate cannot be falsified: a retriever that
    declines EVERYTHING would otherwise post a perfect score."""
    from agmind.eval.ir import CaseRetrieval, aggregate, score_case

    # answerable case where nothing clears the threshold => the retriever wrongly declined
    shy = score_case(
        CaseRetrieval("a1", ("x",), ("c",), {"c": frozenset()}, scores=(0.05,)),
        k=5,
        abstain_threshold=0.5,
    )
    agg = aggregate([shy])
    assert agg.cases_abstention_answerable == 1
    assert agg.cases_false_abstained == 1
