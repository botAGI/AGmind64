"""Evaluation report: the honesty layer (AI-SPEC §6.3, §6.5).

Every rule here exists because of a documented way benchmark reports mislead, several of them
recorded in this repo's own error journal (2026-07-17, five classes of benchmark-report error):

* **No number without its ``n`` and its interval.** ``Interval.format`` enforces it, and a bare
  point estimate has no code path to the output at all. A rate that loses its denominator on the
  way into a slide is the single most common way a caption outlives its caveat.
* **A ``scope`` block is mandatory.** "AGmind's RAG quality" is not expressible; "recall@5 on
  this corpus, this retriever config, this golden set" is. The scope is printed last in text and
  present in JSON, so a quoted number carries its own limits.
* **Retrieval and abstention are never blended.** A system can be excellent at finding passages
  and terrible at knowing when there is nothing to find; one score would hide exactly that.
* **Client-side latency is named ``retrieval_latency_client_ms``** so it can never be confused
  with a server-reported figure (error class 2: server metric ≠ client metric).
* **Raw per-case data is always written** and its path printed, because "reproducible" without
  the underlying data is error class 5.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from agmind.eval.ir import METRIC_NAMES, AggregateScore, CaseScore
from agmind.eval.stats import Interval, bootstrap_mean, wilson_interval

#: Seed for every bootstrap in a report. Fixed and printed so an interval reproduces exactly.
DEFAULT_SEED = 20260803

#: The ONE metric a gate may fail on. Everything else is exploratory — six metrics over eleven
#: cases carry roughly two independent signals, and gating on all of them inflates the
#: family-wise error rate to the point where a green build means little.
HEADLINE_METRIC = "anchor_ndcg"


@dataclass(frozen=True)
class RunScope:
    """What this measurement is actually about. Printed with every report, no exceptions."""

    retriever: str
    corpus_ref: str
    corpus_fingerprint: str
    corpus_docs: int
    corpus_chunks: int
    golden_set_cases: int
    k: int
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "retriever": self.retriever,
            "corpus_ref": self.corpus_ref,
            "corpus_fingerprint": self.corpus_fingerprint,
            "corpus_docs": self.corpus_docs,
            "corpus_chunks": self.corpus_chunks,
            "golden_set_cases": self.golden_set_cases,
            "k": self.k,
            **self.extra,
        }

    def comparable_key(self) -> str:
        """Two runs may be compared only when this matches — corpus, config and set identity.

        Refusing to compare is a feature: a metric computed over a different corpus is not a
        regression, it is a different question, and reporting it as a delta is how a benchmark
        starts lying.
        """
        return "|".join(
            [
                self.retriever,
                self.corpus_fingerprint,
                str(self.k),
                str(self.golden_set_cases),
                *(f"{key}={value}" for key, value in sorted(self.extra.items())),
            ]
        )


@dataclass(frozen=True)
class EvalReport:
    """A complete measurement: aggregate, intervals, scope, and the raw data path."""

    scope: RunScope
    aggregate: AggregateScore
    intervals: dict[str, Interval]
    abstention: Interval | None
    per_case: tuple[CaseScore, ...]
    latency_ms_p50: float | None = None
    raw_log_path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "scope": self.scope.to_dict(),
            "headline_metric": HEADLINE_METRIC,
            "metrics": {name: iv.to_dict() for name, iv in self.intervals.items()},
            "abstention": self.abstention.to_dict() if self.abstention else None,
            "counters": self.aggregate.to_dict(),
            "retrieval_latency_client_ms_p50": self.latency_ms_p50,
            "raw_log_path": self.raw_log_path,
        }


def build_report(
    scope: RunScope,
    scores: Sequence[CaseScore],
    *,
    aggregate_score: AggregateScore,
    seed: int = DEFAULT_SEED,
    latency_ms_p50: float | None = None,
    raw_log_path: str | None = None,
) -> EvalReport:
    """Attach an interval to every metric that has data behind it."""
    intervals: dict[str, Interval] = {}
    for name in METRIC_NAMES:
        values = aggregate_score.per_case.get(name, ())
        if values:
            intervals[name] = bootstrap_mean(values, seed=seed)

    abstention: Interval | None = None
    if aggregate_score.cases_negative:
        # Wilson, not bootstrap: abstention is a genuinely binary rate, and Wilson stays inside
        # [0,1] and remains sensible at n=4 where a normal approximation would not.
        abstention = wilson_interval(
            aggregate_score.cases_abstained, aggregate_score.cases_negative
        )

    return EvalReport(
        scope=scope,
        aggregate=aggregate_score,
        intervals=intervals,
        abstention=abstention,
        per_case=tuple(scores),
        latency_ms_p50=latency_ms_p50,
        raw_log_path=raw_log_path,
    )


def format_report_text(report: EvalReport) -> str:
    """Human-readable report. Every number carries n and an interval; scope is printed last."""
    lines: list[str] = []
    scope = report.scope
    agg = report.aggregate

    lines.append(f"RAG retrieval evaluation — {scope.retriever}")
    lines.append("")

    headline = report.intervals.get(HEADLINE_METRIC)
    if headline is not None:
        lines.append(f"  {HEADLINE_METRIC}@{scope.k}   {headline.format()}   [headline]")
    else:
        lines.append(f"  {HEADLINE_METRIC}@{scope.k}   no data")

    lines.append("")
    lines.append("  exploratory (not gated — see docs/eval/MEASUREMENT.md):")
    for name in METRIC_NAMES:
        if name == HEADLINE_METRIC:
            continue
        interval = report.intervals.get(name)
        lines.append(f"    {name}@{scope.k:<3} {interval.format() if interval else 'no data'}")

    lines.append("")
    if report.abstention is not None:
        lines.append(
            f"  abstention (unanswerable questions declined)   {report.abstention.format()}"
        )
    else:
        lines.append("  abstention   no negative cases in this set")

    lines.append("")
    lines.append(
        f"  cases: {agg.cases_scored} scored · {agg.cases_negative} negative · "
        f"{agg.cases_empty_retrieval} empty-retrieval · {agg.cases_errored} errored · "
        f"{agg.cases_skipped_no_anchors} malformed"
    )
    if agg.cases_errored:
        lines.append(
            "  WARNING: errored cases make every average a biased subsample — treat as invalid."
        )
    if report.latency_ms_p50 is not None:
        lines.append(f"  retrieval_latency_client_ms p50: {report.latency_ms_p50:.0f}")

    lines.append("")
    lines.append("  scope — this measurement is about EXACTLY this and nothing wider:")
    lines.append(f"    corpus      {scope.corpus_docs} docs / {scope.corpus_chunks} chunks")
    lines.append(
        f"    corpus_ref  {scope.corpus_ref[:12]} (fingerprint {scope.corpus_fingerprint[:12]})"
    )
    lines.append(f"    golden set  {scope.golden_set_cases} cases, k={scope.k}")
    for key, value in sorted(scope.extra.items()):
        lines.append(f"    {key:<11} {value}")
    if report.raw_log_path:
        lines.append(f"    raw data    {report.raw_log_path}")
    return "\n".join(lines) + "\n"


def format_report_json(report: EvalReport) -> str:
    return json.dumps(report.to_dict(), indent=2, ensure_ascii=False, sort_keys=True)


def raw_case_lines(report: EvalReport) -> str:
    """Per-case JSONL: the evidence behind every aggregate in the report."""
    out: list[str] = []
    for score in report.per_case:
        out.append(
            json.dumps(
                {
                    "case_id": score.case_id,
                    "k": score.k,
                    **{name: getattr(score, name) for name in METRIC_NAMES},
                    "negative": score.negative,
                    "abstained": score.abstained,
                    "empty_retrieval": score.empty_retrieval,
                    "errored": score.errored,
                    "retrieved_considered": score.retrieved_considered,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
    return "\n".join(out) + "\n"


__all__ = [
    "DEFAULT_SEED",
    "HEADLINE_METRIC",
    "EvalReport",
    "RunScope",
    "build_report",
    "format_report_json",
    "format_report_text",
    "raw_case_lines",
]
