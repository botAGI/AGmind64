"""Nightly token-throughput perf gate (SPEC-16.4).

Pure decision logic so the nightly LIVE gate is unit-testable WITHOUT a GPU runner
or a live llama-server:

  * ``evaluate_perf`` compares a measured tokens/sec against the frozen baseline and
    returns ``(ok, human_message)`` — ``ok`` when the measurement clears the floor.
  * ``baseline_tokens_per_sec`` reads that baseline from the model catalog
    (``templates/models.yaml`` ``measured_tg_t_s`` of the wizard default LLM) — the
    SINGLE source of truth, never a duplicated ``73.5`` literal in code.

The workflow (.github/workflows/perf-nightly.yml) runs ``agmind loadtest chat``
against the live llama-llm, lifts ``tokens_per_sec`` from the k6 summary, and feeds
it here; a below-floor measurement fails the job.
"""

from __future__ import annotations

import json
import sys

from agmind.models import load_curated_model_entries, load_model_catalog_defaults

DEFAULT_FLOOR_RATIO = 0.9


def evaluate_perf(
    measured_tps: float,
    baseline_tps: float,
    floor_ratio: float = DEFAULT_FLOOR_RATIO,
) -> tuple[bool, str]:
    """Decide whether a measured tokens/sec clears the regression floor.

    ``ok := measured_tps >= baseline_tps * floor_ratio``. The floor is a fraction of
    the frozen baseline (default ``0.9`` → tolerate ≤10% regression before failing the
    nightly gate). The comparison is ``>=`` so a measurement sitting EXACTLY on the
    floor passes. Returns ``(ok, one-line human-readable verdict)``.
    """
    floor = baseline_tps * floor_ratio
    ok = measured_tps >= floor
    pct = (measured_tps / baseline_tps * 100.0) if baseline_tps > 0 else 0.0
    verdict = "PASS" if ok else "FAIL"
    msg = (
        f"{verdict}: {measured_tps:.1f} t/s measured vs {baseline_tps:.1f} t/s baseline "
        f"({pct:.1f}% of baseline; floor {floor_ratio * 100:.0f}% = {floor:.1f} t/s)"
    )
    return ok, msg


def baseline_tokens_per_sec() -> float:
    """Frozen token-generation baseline (t/s), read from the model catalog.

    Reads ``measured_tg_t_s`` of the wizard default LLM in ``templates/models.yaml``
    (surfaced via ``agmind.models``) — the single source of truth, so bumping the
    catalog moves the gate with it and no ``73.5`` literal is duplicated in code.
    Raises when the default LLM carries no measured baseline (a nightly regression
    gate with no baseline to compare against is meaningless).
    """
    default_llm_id = load_model_catalog_defaults()["llm"]
    for entry in load_curated_model_entries():
        if entry.id == default_llm_id and entry.measured_tg_t_s is not None:
            return float(entry.measured_tg_t_s)
    raise ValueError(
        f"no measured_tg_t_s baseline for default LLM {default_llm_id!r} in model catalog"
    )


def measured_from_loadtest_json(text: str) -> float:
    """Extract ``tokens_per_sec`` from an ``agmind loadtest chat --json`` payload.

    Slices from the first ``{`` (mirroring how the CLI tests read the JSON block) so any
    incidental leading output is tolerated. Missing key → ``0.0`` (which the gate treats
    as a hard regression, i.e. fails — a run that produced no tokens is not a pass).
    """
    data = json.loads(text[text.index("{") :])
    return float(data.get("tokens_per_sec", 0.0))


def main() -> int:
    """Nightly-workflow glue: gate stdin loadtest JSON against the frozen baseline.

    Reads ``agmind loadtest chat --json`` output on stdin, compares its ``tokens_per_sec``
    to :func:`baseline_tokens_per_sec`, prints the one-line verdict, and returns a shell
    exit code (``0`` pass / ``1`` fail) so the workflow step fails on a below-floor run.
    Invoked as ``agmind loadtest chat ... --json | python -m agmind.loadtest.perf_gate``.
    """
    measured = measured_from_loadtest_json(sys.stdin.read())
    baseline = baseline_tokens_per_sec()
    ok, msg = evaluate_perf(measured, baseline)
    print(msg)
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
