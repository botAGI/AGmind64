"""Regression gate: compare a run against a frozen baseline (AI-SPEC §6.4).

One measurement proves the retriever works today. A gate proves it has not got worse — which is
the property an operator actually needs, and the reason the harness exists at all.

The most important behaviour here is **refusing to compare**. A metric computed over a different
corpus, a different chunker configuration, a different golden set or a different ``k`` is not a
regression, it is a different question; reporting it as a delta is how a benchmark starts lying.
So a mismatched identity is a hard failure with an explicit message, never a quiet pass and never
a scary-looking red number that sends someone hunting a regression that does not exist.

Mirrors ``agmind/loadtest/perf_gate.py``: pure decision logic, a ``main()`` that reads a report
on stdin and returns a shell exit code, so the nightly workflow is one pipe.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: Tolerated fraction of the baseline before the gate fails. Retrieval scores on a 15-case set
#: are noisy; a 10% floor is a starting point, printed in the verdict so it is never implicit.
DEFAULT_FLOOR_RATIO = 0.9


class GateError(RuntimeError):
    """Raised when the comparison cannot be made at all (managed, never a traceback)."""


@dataclass(frozen=True)
class Baseline:
    """A frozen reference measurement plus the identity it is valid for."""

    comparable_key: str
    metric: str
    value: float
    n: int
    recorded_at: str
    scope: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "comparable_key": self.comparable_key,
            "metric": self.metric,
            "value": self.value,
            "n": self.n,
            "recorded_at": self.recorded_at,
            "scope": self.scope,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> Baseline:
        try:
            return cls(
                comparable_key=str(payload["comparable_key"]),
                metric=str(payload["metric"]),
                value=float(payload["value"]),
                n=int(payload["n"]),
                recorded_at=str(payload.get("recorded_at", "")),
                scope=dict(payload.get("scope") or {}),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise GateError(f"malformed baseline: {exc}") from exc


def baseline_from_report(payload: dict[str, Any], *, recorded_at: str) -> Baseline:
    """Build a baseline from an ``agmind eval run --json`` payload."""
    metric = str(payload.get("headline_metric") or "")
    metrics = payload.get("metrics") or {}
    entry = metrics.get(metric)
    if not isinstance(entry, dict):
        raise GateError(f"report carries no headline metric {metric!r}")

    scope = dict(payload.get("scope") or {})
    return Baseline(
        comparable_key=_comparable_key(scope),
        metric=metric,
        value=float(entry["point"]),
        n=int(entry["n"]),
        recorded_at=recorded_at,
        scope=scope,
    )


def _comparable_key(scope: dict[str, Any]) -> str:
    """Identity a run must share with a baseline to be comparable.

    Deliberately includes the chunker settings and every ``scope`` extra: changing chunk size
    changes the numbers substantially, so a key that ignored it would silently compare two
    different experiments and call the difference a regression.
    """
    parts = [
        str(scope.get("retriever", "")),
        str(scope.get("corpus_fingerprint", "")),
        f"k={scope.get('k')}",
        f"cases={scope.get('golden_set_cases')}",
        f"chunks={scope.get('corpus_chunks')}",
    ]
    for key in sorted(scope):
        if key in {
            "retriever",
            "corpus_fingerprint",
            "corpus_ref",
            "k",
            "golden_set_cases",
            "corpus_chunks",
            "corpus_docs",
        }:
            continue
        parts.append(f"{key}={scope[key]}")
    return "|".join(parts)


def evaluate(
    payload: dict[str, Any],
    baseline: Baseline | None,
    *,
    floor_ratio: float = DEFAULT_FLOOR_RATIO,
) -> tuple[bool, str]:
    """Decide pass/fail. Returns ``(ok, one-line verdict)``.

    Hard failures that are NOT "the retriever got worse", and are reported as such:
      * any errored case — the averages are a biased subsample of whatever happened to succeed;
      * a comparable-key mismatch — see the module docstring;
      * a missing headline metric.
    """
    counters = payload.get("counters") or {}
    errored = int(counters.get("cases_errored") or 0)
    if errored:
        return False, (
            f"INVALID: {errored} case(s) errored — every average is a biased subsample of the "
            "cases that happened to succeed. Fix the retrieval failure before reading any number."
        )

    metric = str(payload.get("headline_metric") or "")
    entry = (payload.get("metrics") or {}).get(metric)
    if not isinstance(entry, dict):
        return False, f"INVALID: report carries no headline metric {metric!r}"

    measured = float(entry["point"])
    n = int(entry["n"])

    if baseline is None:
        return True, (
            f"NO BASELINE: {metric} {measured:.3f} (n={n}). Nothing to compare against — record "
            "this run with `--write-baseline` if it is representative. This is the only run that "
            "may pass without a baseline."
        )

    key = _comparable_key(dict(payload.get("scope") or {}))
    if key != baseline.comparable_key:
        return False, (
            "REFUSING TO COMPARE: this run and the baseline are not the same experiment.\n"
            f"  run      {key}\n"
            f"  baseline {baseline.comparable_key}\n"
            "A different corpus, chunker, retriever, k or case count is a different question, "
            "not a regression. Re-record the baseline if the change was intended."
        )

    if metric != baseline.metric:
        return False, (
            f"REFUSING TO COMPARE: baseline is on {baseline.metric!r}, run is on {metric!r}"
        )

    floor = baseline.value * floor_ratio
    ok = measured >= floor
    pct = (measured / baseline.value * 100.0) if baseline.value > 0 else 0.0
    verdict = "PASS" if ok else "FAIL"
    return ok, (
        f"{verdict}: {metric} {measured:.3f} vs baseline {baseline.value:.3f} "
        f"({pct:.0f}% of baseline; floor {floor_ratio * 100:.0f}% = {floor:.3f}; n={n})"
    )


def load_baseline(path: Path) -> Baseline | None:
    """Read a baseline file, or ``None`` when it does not exist (the first-run path)."""
    if not path.is_file():
        return None
    try:
        return Baseline.from_dict(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError) as exc:
        raise GateError(f"cannot read baseline {path}: {exc}") from exc


def default_baseline_path() -> Path:
    """Host-local baseline. Deliberately NOT shipped in the repo: a baseline recorded on someone
    else's corpus and hardware would fail for every operator, and the first thing anyone does with
    a gate that always fails is switch it off."""
    return Path.home() / ".local" / "share" / "agmind" / "eval" / "baseline.json"


def main() -> int:
    """Read ``agmind eval run --json`` on stdin, compare to the baseline, return an exit code."""
    text = sys.stdin.read()
    try:
        payload = json.loads(text[text.index("{") :])
    except (ValueError, json.JSONDecodeError) as exc:
        print(f"INVALID: stdin is not an eval report ({exc})", file=sys.stderr)
        return 2

    try:
        baseline = load_baseline(default_baseline_path())
    except GateError as exc:
        print(f"INVALID: {exc}", file=sys.stderr)
        return 2

    ok, verdict = evaluate(payload, baseline)
    print(verdict)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "DEFAULT_FLOOR_RATIO",
    "Baseline",
    "GateError",
    "baseline_from_report",
    "default_baseline_path",
    "evaluate",
    "load_baseline",
    "main",
]
