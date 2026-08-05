"""Rank-aware IR metrics for RAG retrieval evaluation — the judge-free layer (AI-SPEC §6.1).

This module is pure arithmetic: no I/O, no network, no text matching. Anchor *containment*
(does this chunk's text contain this anchor?) is decided upstream in ``agmind.eval.anchors``;
here a case arrives already reduced to "which anchors does each retrieved chunk cover".
Keeping the split means the metrics are exhaustively testable without a corpus or a retriever.

Two deliberate divergences from RAGFlow's own
``api/db/services/evaluation_service.py::_compute_retrieval_metrics`` (read live, 2026-08-03),
both documented in ``docs/eval/MEASUREMENT.md`` so the numbers are never silently compared:

1. **Explicit ``@k``.** RAGFlow's precision divides by the whole returned set, so its value
   moves when ``top_k`` changes even though ranking quality did not. Ours cuts at an explicit
   ``k`` and divides by ``min(k, |R|)`` — the denominator is what actually came back, so a
   small corpus returning 2 chunks is not punished for a k of 5.
2. **Ranked dedup instead of ``set()``.** RAGFlow set-ifies retrieved ids, which corrupts its
   own precision denominator. We dedup preserving first occurrence, *before* truncation, so a
   retriever that repeats a chunk does not get to consume two of the k slots.

Degenerate cases are modelled explicitly rather than collapsing to ``0.0``: a silently-zero
metric is the classic evaluation lie. "No anchors" (a malformed case) and "empty retrieval"
(a real failure) are different facts and are counted separately.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field


@dataclass(frozen=True)
class CaseRetrieval:
    """One evaluation case reduced to what the metrics need.

    ``ranked_chunk_ids`` is the retriever's order, pre-dedup and pre-truncation — dedup and the
    ``k`` cut happen here so the rules are enforced in one place. ``anchors_by_chunk`` maps a
    chunk id to the anchors that chunk covers; a chunk absent from the mapping covers nothing.
    """

    case_id: str
    anchors: tuple[str, ...]
    ranked_chunk_ids: tuple[str, ...]
    anchors_by_chunk: Mapping[str, frozenset[str]] = field(default_factory=dict)
    errored: bool = False
    #: Retriever scores parallel to ``ranked_chunk_ids``. Only needed for negative cases, where
    #: the question is whether anything cleared the abstention threshold at all.
    scores: tuple[float, ...] = ()
    #: A deliberately unanswerable case. Distinct from "no anchors were authored", which is a
    #: mistake; this is the abstention class and it is scored, not discarded.
    negative: bool = False


#: Metric names carried per case; the aggregate exposes each as a per-case vector so
#: ``agmind.eval.stats`` can bootstrap over CASES (never over chunks — they are not independent).
METRIC_NAMES: tuple[str, ...] = (
    "anchor_recall",
    "anchor_precision",
    "anchor_hit",
    "anchor_mrr",
    "anchor_ndcg",
    "f1",
)


@dataclass(frozen=True)
class CaseScore:
    """Per-case metrics. ``None`` means "not measurable", which is never the same as ``0.0``."""

    case_id: str
    k: int
    anchor_recall: float | None
    anchor_precision: float | None
    anchor_hit: float | None
    anchor_mrr: float | None
    anchor_ndcg: float | None
    f1: float | None
    retrieved_considered: int
    skipped_no_anchors: bool = False
    empty_retrieval: bool = False
    errored: bool = False
    negative: bool = False
    #: For a negative case: did the retriever correctly surface nothing above the threshold?
    abstained: bool | None = None

    @property
    def scoreable(self) -> bool:
        """Contributes to the retrieval metrics. Negatives are scored, but on their own axis —
        folding them into recall would let abstention failures hide inside a retrieval average."""
        return not (self.errored or self.skipped_no_anchors or self.negative)


@dataclass(frozen=True)
class AggregateScore:
    """Macro aggregate over cases (each case weight 1).

    Never micro: a case carrying 20 anchors would otherwise drown every other case. ``per_case``
    keeps the raw vectors because every interval in the report is a case-level bootstrap on
    exactly these numbers, not a closed-form approximation.
    """

    anchor_recall: float | None
    anchor_precision: float | None
    anchor_hit: float | None
    anchor_mrr: float | None
    anchor_ndcg: float | None
    f1: float | None
    cases_scored: int
    cases_skipped_no_anchors: int
    cases_empty_retrieval: int
    cases_errored: int
    per_case: Mapping[str, tuple[float, ...]]
    cases_negative: int = 0
    cases_abstained: int = 0
    cases_abstention_unmeasured: int = 0
    #: False abstention on ANSWERABLE cases — abstention without its paired false-positive rate
    #: is half an ROC point and cannot be falsified in the over-declining direction.
    cases_false_abstained: int = 0
    cases_abstention_answerable: int = 0

    @property
    def abstention_rate(self) -> float | None:
        """Share of unanswerable cases the retriever correctly declined to answer.

        Reported separately from every retrieval metric and never blended into a single score:
        a system can be excellent at finding passages and terrible at knowing when there is
        nothing to find, and one number would hide exactly that.
        """
        measured = self.cases_negative - self.cases_abstention_unmeasured
        if measured <= 0:
            return None
        return self.cases_abstained / measured

    def to_dict(self) -> dict[str, object]:
        return {
            "cases_scored": self.cases_scored,
            "cases_skipped_no_anchors": self.cases_skipped_no_anchors,
            "cases_empty_retrieval": self.cases_empty_retrieval,
            "cases_errored": self.cases_errored,
            "cases_negative": self.cases_negative,
            "cases_abstained": self.cases_abstained,
            "cases_abstention_unmeasured": self.cases_abstention_unmeasured,
            "cases_false_abstained": self.cases_false_abstained,
            "cases_abstention_answerable": self.cases_abstention_answerable,
        }
        # NOTE: no bare point estimates here. The intervalled `metrics` block of the report
        # carries every mean with its n and interval; duplicating them as naked floats is how a
        # number escapes its uncertainty on the way into a spreadsheet.


def _dedup_preserving_order(chunk_ids: Sequence[str]) -> tuple[str, ...]:
    return tuple(cid for cid, _pos in _dedup_with_positions(chunk_ids))


def _dedup_with_positions(chunk_ids: Sequence[str]) -> tuple[tuple[str, int], ...]:
    """Deduped ids paired with the ORIGINAL index of their first occurrence.

    The positions matter: a parallel ``scores`` list is indexed by the pre-dedup rank, so slicing
    it by the post-dedup length silently pairs a chunk with another chunk's score whenever the
    retriever repeats an id.
    """
    seen: set[str] = set()
    out: list[tuple[str, int]] = []
    for index, cid in enumerate(chunk_ids):
        if cid not in seen:
            seen.add(cid)
            out.append((cid, index))
    return tuple(out)


def _dcg(gains: Sequence[float]) -> float:
    """DCG with a ``log2(rank + 1)`` discount, rank 1-indexed.

    Gains are GRADED: a chunk's gain is the number of distinct case anchors it covers. Binary
    gains were a units bug — the numerator counted *chunks* while the ideal normalised over
    *anchors*, so any case whose anchor appeared in several retrieved chunks scored above 1.0
    (measured 1.63 and 2.13 on real data; 6 of 13 golden anchors occur in multiple chunks).
    Grading also matches how TREC 2025 RAG scores a segment — by how many sub-narratives it
    covers, not by a yes/no.
    """
    return sum(g / math.log2(rank + 1) for rank, g in enumerate(gains, start=1) if g)


def score_case(
    case: CaseRetrieval,
    *,
    k: int,
    ideal_gains: Sequence[int] | None = None,
    abstain_threshold: float | None = None,
) -> CaseScore:
    """Score one case at cutoff ``k``.

    Order of operations is load-bearing: dedup first, then truncate to ``k``. Doing it the other
    way lets a duplicated chunk eat a slot that a relevant chunk would otherwise have occupied.

    ``ideal_gains`` is the per-chunk anchor-coverage vector over the WHOLE frozen corpus. Supplying
    it makes nDCG measure finding *and* ordering against the exactly-known best ranking — possible
    here precisely because the corpus is small and pinned by manifest, unlike a TREC-scale pool.
    Omitting it falls back to the best ordering of what was actually retrieved, which is bounded
    but only measures ordering.

    ``abstain_threshold`` scores the negative class: the correct behaviour on an unanswerable
    question is that nothing clears the retriever's confidence threshold.
    """
    if k < 1:
        raise ValueError(f"k must be >= 1, got {k}")

    def _unmeasurable(
        *, considered: int, skipped: bool = False, errored: bool = False
    ) -> CaseScore:
        """A case we cannot score. Every metric is ``None`` — never ``0.0``, which would read
        downstream as "the retriever failed" instead of "there was nothing to measure"."""
        return CaseScore(
            case_id=case.case_id,
            k=k,
            anchor_recall=None,
            anchor_precision=None,
            anchor_hit=None,
            anchor_mrr=None,
            anchor_ndcg=None,
            f1=None,
            retrieved_considered=considered,
            skipped_no_anchors=skipped,
            errored=errored,
        )

    if case.errored:
        return _unmeasurable(considered=0, errored=True)

    if case.negative:
        # Abstention: on a deliberately unanswerable question the retriever is right when nothing
        # clears the threshold. Scored on its own axis so a failure here can never be averaged
        # away inside a healthy-looking recall number.
        deduped = _dedup_with_positions(case.ranked_chunk_ids)[:k]
        ranked = [cid for cid, _ in deduped]
        # Index the score list by the ORIGINAL rank of each surviving chunk, never by a slice of
        # the deduped length — those are different chunks the moment an id repeats.
        usable_scores = [case.scores[pos] for _cid, pos in deduped if pos < len(case.scores)]
        if abstain_threshold is None or not usable_scores:
            # Unmeasurable, NOT success. Defaulting to True made every negative case pass for a
            # retriever that reports no scores at all — a perfect abstention rate earned by
            # returning nothing measurable.
            abstained: bool | None = None
        else:
            abstained = bool(max(usable_scores) < abstain_threshold)
        return CaseScore(
            case_id=case.case_id,
            k=k,
            anchor_recall=None,
            anchor_precision=None,
            anchor_hit=None,
            anchor_mrr=None,
            anchor_ndcg=None,
            f1=None,
            retrieved_considered=len(ranked),
            negative=True,
            abstained=abstained,
        )

    anchors = frozenset(case.anchors)
    if not anchors:
        # A case with no anchors cannot be right or wrong — it is malformed input, and the
        # golden-set integrity gate should have rejected it. Counting it as 0.0 would quietly
        # drag every average down and read as a retrieval regression.
        return _unmeasurable(
            considered=len(_dedup_preserving_order(case.ranked_chunk_ids)), skipped=True
        )

    deduped_pos = _dedup_with_positions(case.ranked_chunk_ids)[:k]
    ranked = [cid for cid, _ in deduped_pos]
    considered = len(ranked)

    # FALSE abstention: an ANSWERABLE question where nothing cleared the threshold. Measured so
    # the abstention rate can be falsified — without it a retriever that declines everything
    # would post a perfect abstention score and no metric would contradict it.
    positive_scores = [case.scores[pos] for _cid, pos in deduped_pos if pos < len(case.scores)]
    false_abstained: bool | None = (
        None
        if (abstain_threshold is None or not positive_scores)
        else bool(max(positive_scores) < abstain_threshold)
    )

    if considered == 0:
        # Returning nothing IS the failure we are trying to detect — a genuine zero.
        return CaseScore(
            case_id=case.case_id,
            k=k,
            anchor_recall=0.0,
            anchor_precision=0.0,
            anchor_hit=0.0,
            anchor_mrr=0.0,
            anchor_ndcg=0.0,
            f1=0.0,
            retrieved_considered=0,
            empty_retrieval=True,
            abstained=false_abstained,
        )

    covered_by: list[frozenset[str]] = [
        frozenset(case.anchors_by_chunk.get(cid, frozenset())) & anchors for cid in ranked
    ]
    relevant_flags = [bool(cov) for cov in covered_by]
    covered_anchors: set[str] = set().union(*covered_by) if covered_by else set()

    recall = len(covered_anchors) / len(anchors)
    # Denominator is what actually came back, capped at k — never k itself.
    precision = sum(relevant_flags) / considered
    hit = 1.0 if any(relevant_flags) else 0.0
    mrr = next(
        (1.0 / rank for rank, rel in enumerate(relevant_flags, start=1) if rel),
        0.0,
    )

    # Graded gains keep numerator and denominator in the same unit (anchors covered per chunk).
    gains = [float(len(cov)) for cov in covered_by]
    if ideal_gains is not None:
        ideal = sorted((float(g) for g in ideal_gains), reverse=True)[:k]
    else:
        # Fallback: the best possible ordering of what was actually retrieved. Bounded by
        # construction, but measures ordering only — the caller is expected to pass the
        # corpus-wide vector when it wants "did you find it AND rank it well".
        ideal = sorted(gains, reverse=True)
    idcg = _dcg(ideal)
    ndcg = min(_dcg(gains) / idcg, 1.0) if idcg > 0 else 0.0

    denom = precision + recall
    f1 = (2 * precision * recall / denom) if denom > 0 else 0.0

    return CaseScore(
        case_id=case.case_id,
        k=k,
        anchor_recall=recall,
        anchor_precision=precision,
        anchor_hit=hit,
        anchor_mrr=mrr,
        anchor_ndcg=ndcg,
        f1=f1,
        retrieved_considered=considered,
        abstained=false_abstained,
    )


def aggregate(scores: Iterable[CaseScore]) -> AggregateScore:
    """Macro-average the scoreable cases and count the unscoreable ones by reason.

    ``cases_errored > 0`` makes every average a biased subsample — the caller's gate is expected
    to hard-fail on it rather than report a mean over whatever happened to succeed.
    """
    scores = list(scores)
    scoreable = [s for s in scores if s.scoreable]

    per_case: dict[str, tuple[float, ...]] = {
        name: tuple(
            value
            for s in scoreable
            if (value := getattr(s, name)) is not None  # noqa: F841
        )
        for name in METRIC_NAMES
    }

    def _mean(name: str) -> float | None:
        values = per_case[name]
        return (sum(values) / len(values)) if values else None

    return AggregateScore(
        anchor_recall=_mean("anchor_recall"),
        anchor_precision=_mean("anchor_precision"),
        anchor_hit=_mean("anchor_hit"),
        anchor_mrr=_mean("anchor_mrr"),
        anchor_ndcg=_mean("anchor_ndcg"),
        f1=_mean("f1"),
        cases_scored=len(scoreable),
        cases_skipped_no_anchors=sum(1 for s in scores if s.skipped_no_anchors),
        cases_empty_retrieval=sum(1 for s in scores if s.empty_retrieval),
        cases_errored=sum(1 for s in scores if s.errored),
        per_case=per_case,
        cases_negative=sum(1 for s in scores if s.negative),
        cases_abstained=sum(1 for s in scores if s.negative and s.abstained is True),
        cases_abstention_unmeasured=sum(1 for s in scores if s.negative and s.abstained is None),
        cases_false_abstained=sum(1 for s in scores if not s.negative and s.abstained is True),
        cases_abstention_answerable=sum(
            1 for s in scores if not s.negative and s.abstained is not None
        ),
    )


__all__ = [
    "METRIC_NAMES",
    "AggregateScore",
    "CaseRetrieval",
    "CaseScore",
    "aggregate",
    "score_case",
]
