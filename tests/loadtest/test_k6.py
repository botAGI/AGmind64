"""Phase 4.2: unit tests for the k6 chat load-test business logic (agmind.loadtest.k6).

No live LLM and no k6 binary in CI — these exercise ONLY the pure parts: the shipped
script template resolves + parameterizes via __ENV (so the .js stays static/shippable),
and the end-of-test summary JSON parses into the metrics the CLI surfaces (p50/p95
latency, req/s, error rate). The k6-missing guard is asserted in test_loadtest_cmd.py.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agmind.loadtest import k6

pytestmark = pytest.mark.backend_any

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "loadtest" / "k6_summary.json"


def test_script_path_points_at_a_shipped_static_js() -> None:
    path = k6.script_path()
    assert path.is_file(), path
    assert path.suffix == ".js"
    text = path.read_text(encoding="utf-8")
    # Parameterized via __ENV so the file never hard-codes endpoint/model/load:
    # the CLI feeds those through `k6 run -e ...` / the process env.
    assert "__ENV.ENDPOINT" in text
    assert "__ENV.MODEL" in text
    assert "__ENV.VUS" in text
    assert "__ENV.DURATION" in text
    # Hits an OpenAI-compatible chat-completions endpoint and emits a parseable summary.
    assert "/v1/chat/completions" in text
    assert "handleSummary" in text


def test_build_env_maps_options_to_k6_env_vars() -> None:
    env = k6.build_env(
        endpoint="http://127.0.0.1:8080/v1/chat/completions",
        model="qwen2.5",
        vus=12,
        duration="45s",
        api_key="sk-test",
    )
    assert env["ENDPOINT"] == "http://127.0.0.1:8080/v1/chat/completions"
    assert env["MODEL"] == "qwen2.5"
    assert env["VUS"] == "12"  # k6 __ENV values are strings
    assert env["DURATION"] == "45s"
    assert env["API_KEY"] == "sk-test"


def test_parse_summary_extracts_latency_throughput_errors() -> None:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    m = k6.parse_summary(data)
    assert m.p50_ms == pytest.approx(120.5)
    assert m.p95_ms == pytest.approx(612.8)  # bracket key data.metrics...values["p(95)"]
    assert m.requests_per_sec == pytest.approx(61.25)
    assert m.total_requests == 1840
    assert m.error_rate == pytest.approx(0.0163)
    # Convenience: error_rate surfaced as a percentage too.
    assert m.error_pct == pytest.approx(1.63, abs=1e-6)


def test_parse_summary_tolerates_missing_metrics() -> None:
    # A partial summary (e.g. zero requests issued) must not KeyError — defaults to 0.
    m = k6.parse_summary({"metrics": {}})
    assert m.p50_ms == 0.0
    assert m.p95_ms == 0.0
    assert m.requests_per_sec == 0.0
    assert m.total_requests == 0
    assert m.error_rate == 0.0


def test_parse_summary_reads_tokens_per_sec_when_present() -> None:
    # SPEC-16.4: chat.js emits token throughput into the summary; the wrapper lifts it.
    data = {"metrics": {"tokens_per_second": {"values": {"rate": 73.5}}}}
    m = k6.parse_summary(data)
    assert m.tokens_per_sec == pytest.approx(73.5)


def test_parse_summary_defaults_tokens_per_sec_to_zero_when_absent() -> None:
    # Old (pre-token-metric) summaries must still parse — tokens_per_sec defaults to 0.0.
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert "tokens_per_second" not in data["metrics"]  # fixture predates the metric
    m = k6.parse_summary(data)
    assert m.tokens_per_sec == 0.0


def test_format_metrics_table_is_renderable_text() -> None:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    m = k6.parse_summary(data)
    text = k6.format_metrics_text(m)
    # Behavioural: the figures appear; we do not pin exact column layout.
    assert "120.5" in text  # p50
    assert "612.8" in text  # p95
    assert "61.2" in text or "61.25" in text  # req/s
    assert "1.63" in text or "1.6" in text  # error %


def test_metrics_to_dict_round_trips_as_json() -> None:
    data = json.loads(FIXTURE.read_text(encoding="utf-8"))
    m = k6.parse_summary(data)
    payload = m.to_dict()
    assert json.loads(json.dumps(payload)) == payload
    assert payload["p95_ms"] == pytest.approx(612.8)
    assert payload["total_requests"] == 1840
