"""Phase 18 (M11) — the regression gate (AI-SPEC §6.4).

The gate's most valuable behaviour is not passing or failing; it is REFUSING to compare two runs
that are not the same experiment. A gate that happily reports a delta between different corpora
teaches its operators to distrust it, and a distrusted gate gets switched off.
"""

from __future__ import annotations

import json

import pytest

pytestmark = pytest.mark.backend_any


def _report(
    *,
    point: float = 0.5,
    n: int = 11,
    errored: int = 0,
    retriever: str = "dense",
    fingerprint: str = "abc123",
    k: int = 5,
    cases: int = 15,
    chunks: int = 643,
    extra: dict | None = None,
) -> dict:
    scope = {
        "retriever": retriever,
        "corpus_fingerprint": fingerprint,
        "corpus_ref": "deadbeef",
        "corpus_docs": 30,
        "corpus_chunks": chunks,
        "golden_set_cases": cases,
        "k": k,
        **(extra or {}),
    }
    return {
        "scope": scope,
        "headline_metric": "anchor_ndcg",
        "metrics": {"anchor_ndcg": {"point": point, "low": 0.1, "high": 0.9, "n": n}},
        "counters": {"cases_errored": errored},
    }


def _baseline_from(payload: dict):
    from agmind.eval.gate import baseline_from_report

    return baseline_from_report(payload, recorded_at="2026-08-05")


# --- the ordinary pass/fail path ---------------------------------------------------------


def test_equal_to_baseline_passes() -> None:
    from agmind.eval.gate import evaluate

    payload = _report(point=0.5)
    ok, message = evaluate(payload, _baseline_from(payload))
    assert ok is True
    assert "PASS" in message


def test_small_drop_within_the_floor_passes() -> None:
    from agmind.eval.gate import evaluate

    base = _baseline_from(_report(point=0.50))
    ok, _ = evaluate(_report(point=0.46), base)  # 92% of baseline, floor is 90%
    assert ok is True


def test_drop_below_the_floor_fails() -> None:
    from agmind.eval.gate import evaluate

    base = _baseline_from(_report(point=0.50))
    ok, message = evaluate(_report(point=0.40), base)  # 80%
    assert ok is False
    assert "FAIL" in message and "floor" in message


def test_improvement_passes() -> None:
    from agmind.eval.gate import evaluate

    base = _baseline_from(_report(point=0.30))
    ok, _ = evaluate(_report(point=0.55), base)
    assert ok is True


# --- refuse-to-compare: the point of the gate ---------------------------------------------


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("fingerprint", "different-corpus"),
        ("k", 10),
        ("cases", 20),
        ("chunks", 900),
        ("retriever", "lexical"),
    ],
)
def test_refuses_to_compare_across_a_changed_experiment(field: str, value: object) -> None:
    """Corpus, cutoff, case count, CHUNKING and retriever all change the number. Reporting any of
    them as a delta would send someone hunting a regression that does not exist."""
    from agmind.eval.gate import evaluate

    base = _baseline_from(_report(point=0.50))
    ok, message = evaluate(_report(point=0.50, **{field: value}), base)

    assert ok is False, f"changing {field} must not be silently compared"
    assert "REFUSING TO COMPARE" in message
    assert "not a regression" in message


def test_refuses_to_compare_when_a_scope_extra_differs() -> None:
    """Scope extras carry things like the abstention threshold and the scoring function."""
    from agmind.eval.gate import evaluate

    base = _baseline_from(_report(point=0.50, extra={"scoring": "cosine(bge-m3)"}))
    ok, message = evaluate(_report(point=0.50, extra={"scoring": "cosine(other-model)"}), base)
    assert ok is False
    assert "REFUSING TO COMPARE" in message


# --- hard invalidations -------------------------------------------------------------------


def test_any_errored_case_invalidates_the_run() -> None:
    """Averages over the cases that happened to succeed are a biased subsample, not a score."""
    from agmind.eval.gate import evaluate

    base = _baseline_from(_report(point=0.50))
    ok, message = evaluate(_report(point=0.99, errored=1), base)

    assert ok is False, "a high score with an errored case must not pass"
    assert "INVALID" in message and "biased subsample" in message


def test_missing_headline_metric_is_invalid() -> None:
    from agmind.eval.gate import evaluate

    payload = _report()
    payload["metrics"] = {}
    ok, message = evaluate(payload, None)
    assert ok is False
    assert "INVALID" in message


# --- first run ---------------------------------------------------------------------------


def test_first_run_without_a_baseline_passes_but_says_so() -> None:
    """The only run allowed to pass unmeasured — and it must announce that loudly, or a missing
    baseline becomes a permanently green gate that proves nothing."""
    from agmind.eval.gate import evaluate

    ok, message = evaluate(_report(point=0.42), None)
    assert ok is True
    assert "NO BASELINE" in message
    assert "--write-baseline" in message


# --- plumbing -----------------------------------------------------------------------------


def test_main_reads_stdin_and_returns_an_exit_code(monkeypatch: pytest.MonkeyPatch) -> None:
    import io

    from agmind.eval import gate

    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(_report(point=0.42))))
    monkeypatch.setattr(gate, "load_baseline", lambda _path: None)
    assert gate.main() == 0


def test_main_rejects_non_report_stdin(monkeypatch: pytest.MonkeyPatch) -> None:
    import io

    from agmind.eval import gate

    monkeypatch.setattr("sys.stdin", io.StringIO("not json at all"))
    assert gate.main() == 2


def test_baseline_roundtrips() -> None:
    from agmind.eval.gate import Baseline

    original = _baseline_from(_report(point=0.37))
    restored = Baseline.from_dict(original.to_dict())
    assert restored == original


def test_malformed_baseline_is_a_managed_error() -> None:
    from agmind.eval.gate import Baseline, GateError

    with pytest.raises(GateError, match="malformed baseline"):
        Baseline.from_dict({"metric": "anchor_ndcg"})
