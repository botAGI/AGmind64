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

    @property
    def scoreable(self) -> bool:
        return not (self.errored or self.skipped_no_anchors)


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

    def to_dict(self) -> dict[str, object]:
        return {
            "cases_scored": self.cases_scored,
            "cases_skipped_no_anchors": self.cases_skipped_no_anchors,
            "cases_empty_retrieval": self.cases_empty_retrieval,
            "cases_errored": self.cases_errored,
            "metrics": {name: getattr(self, name) for name in METRIC_NAMES},
        }


def _dedup_preserving_order(chunk_ids: Sequence[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    out: list[str] = []
    for cid in chunk_ids:
        if cid not in seen:
            seen.add(cid)
            out.append(cid)
    return tuple(out)


def _dcg(relevances: Sequence[bool]) -> float:
    """Binary-gain DCG with a ``log2(rank + 1)`` discount, rank being 1-indexed."""
    return sum(1.0 / math.log2(rank + 1) for rank, rel in enumerate(relevances, start=1) if rel)


def score_case(case: CaseRetrieval, *, k: int) -> CaseScore:
    """Score one case at cutoff ``k``.

    Order of operations is load-bearing: dedup first, then truncate to ``k``. Doing it the other
    way lets a duplicated chunk eat a slot that a relevant chunk would otherwise have occupied.
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

    anchors = frozenset(case.anchors)
    if not anchors:
        # A case with no anchors cannot be right or wrong — it is malformed input, and the
        # golden-set integrity gate should have rejected it. Counting it as 0.0 would quietly
        # drag every average down and read as a retrieval regression.
        return _unmeasurable(
            considered=len(_dedup_preserving_order(case.ranked_chunk_ids)), skipped=True
        )

    ranked = _dedup_preserving_order(case.ranked_chunk_ids)[:k]
    considered = len(ranked)

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

    # IDCG over min(k, |A|): using k would silently under-score every case that has fewer
    # anchors than the cutoff, since no ranking could ever fill k relevant slots.
    ideal_slots = min(k, len(anchors))
    idcg = _dcg([True] * ideal_slots)
    ndcg = (_dcg(relevant_flags) / idcg) if idcg > 0 else 0.0

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
    )


__all__ = [
    "METRIC_NAMES",
    "AggregateScore",
    "CaseRetrieval",
    "CaseScore",
    "aggregate",
    "score_case",
]
