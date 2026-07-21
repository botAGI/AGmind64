"""SPEC-16.4: unit tests for the nightly token-throughput perf gate (pure logic).

No GPU runner / live llama-server here: ``evaluate_perf`` is a pure decision fn and
``baseline_tokens_per_sec`` reads the frozen baseline from the model catalog. The
baseline is NOT hard-coded in the test — it is read from the SAME catalog the
implementation reads (``templates/models.yaml`` ``measured_tg_t_s`` of the default
LLM), so the two move together (single source of truth: no duplicated 73.5 literal).
"""

from __future__ import annotations

import io
import json

import pytest

from agmind.loadtest import perf_gate
from agmind.loadtest.perf_gate import (
    baseline_tokens_per_sec,
    evaluate_perf,
    main,
    measured_from_loadtest_json,
)
from agmind.models import load_curated_model_entries, load_model_catalog_defaults

pytestmark = pytest.mark.backend_any


def _catalog_baseline() -> float:
    """Read the default LLM's measured_tg_t_s straight from the catalog (no literal)."""
    default_llm = load_model_catalog_defaults()["llm"]
    for entry in load_curated_model_entries():
        if entry.id == default_llm:
            assert entry.measured_tg_t_s is not None, default_llm
            return float(entry.measured_tg_t_s)
    raise AssertionError(f"default LLM {default_llm!r} missing from catalog")


def test_baseline_reads_from_catalog_not_hardcoded() -> None:
    # Whatever the catalog says for the default LLM — read it, don't hardcode.
    assert baseline_tokens_per_sec() == pytest.approx(_catalog_baseline())


def test_pass_at_baseline() -> None:
    baseline = _catalog_baseline()
    ok, msg = evaluate_perf(baseline, baseline)
    assert ok is True
    assert "PASS" in msg


def test_pass_at_95_percent() -> None:
    baseline = _catalog_baseline()
    ok, _msg = evaluate_perf(baseline * 0.95, baseline)
    assert ok is True


def test_fail_at_80_percent() -> None:
    baseline = _catalog_baseline()
    ok, msg = evaluate_perf(baseline * 0.80, baseline)
    assert ok is False
    assert "FAIL" in msg


def test_boundary_at_exactly_90_percent_passes() -> None:
    # measured == baseline * floor_ratio → clears the floor (>=, not >).
    baseline = _catalog_baseline()
    ok, _msg = evaluate_perf(baseline * 0.90, baseline)
    assert ok is True


def test_explicit_floor_ratio_is_honored() -> None:
    baseline = _catalog_baseline()
    # A stricter floor (0.98) fails a 95% measurement that the default 0.9 would pass.
    ok, _msg = evaluate_perf(baseline * 0.95, baseline, floor_ratio=0.98)
    assert ok is False


def test_measured_from_loadtest_json_reads_tokens_per_sec() -> None:
    payload = json.dumps({"tokens_per_sec": 71.2, "p95_ms": 600.0})
    assert measured_from_loadtest_json(payload) == pytest.approx(71.2)


def test_measured_from_loadtest_json_tolerates_leading_output() -> None:
    # The extractor slices from the first '{', so stray leading text does not break it.
    assert measured_from_loadtest_json('noise\n{"tokens_per_sec": 5.0}') == pytest.approx(5.0)


def test_measured_from_loadtest_json_defaults_to_zero_when_absent() -> None:
    assert measured_from_loadtest_json('{"p95_ms": 1.0}') == 0.0


def test_main_exits_zero_when_at_baseline(monkeypatch, capsys) -> None:
    baseline = _catalog_baseline()
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"tokens_per_sec": baseline})))
    assert main() == 0
    assert "PASS" in capsys.readouterr().out


def test_main_exits_nonzero_below_floor(monkeypatch, capsys) -> None:
    baseline = _catalog_baseline()
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps({"tokens_per_sec": baseline * 0.5})))
    assert main() == 1
    assert "FAIL" in capsys.readouterr().out


def test_module_exposes_default_floor_ratio() -> None:
    assert perf_gate.DEFAULT_FLOOR_RATIO == pytest.approx(0.9)
