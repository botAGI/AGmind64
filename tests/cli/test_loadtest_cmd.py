"""Phase 4.2: `agmind loadtest chat` CLI wrapper tests (typer CliRunner).

No live LLM and no k6 binary in CI: the run path is mocked at the seam
(`run_chat_loadtest`); the only un-mocked behaviour asserted live is the
k6-missing guard — it must produce a MANAGED actionable error (rc != 0, install
guidance) rather than a traceback.
"""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

pytestmark = pytest.mark.backend_any

runner = CliRunner()


def _app():  # type: ignore[no-untyped-def]
    from agmind.cli import _make_app

    return _make_app()


def test_loadtest_group_help_lists_chat() -> None:
    res = runner.invoke(_app(), ["loadtest", "--help"])
    assert res.exit_code == 0, res.output
    assert "chat" in res.output


def test_chat_k6_missing_is_managed_error_not_traceback(monkeypatch) -> None:
    from agmind.loadtest import k6

    # k6 not on PATH → actionable failure, no Python traceback.
    monkeypatch.setattr(k6, "which_k6", lambda: None)
    res = runner.invoke(_app(), ["loadtest", "chat", "--model", "qwen"])
    assert res.exit_code != 0
    assert "Traceback" not in res.output
    assert "k6" in res.output
    # Actionable: points the operator at how to get k6.
    assert "install" in res.output.lower()


def test_chat_runs_k6_and_prints_metrics_table(monkeypatch) -> None:
    from agmind.cli import loadtest_cmd
    from agmind.loadtest import k6

    monkeypatch.setattr(k6, "which_k6", lambda: "/usr/bin/k6")

    captured: dict[str, object] = {}

    def fake_run(*, endpoint, model, vus, duration, api_key) -> k6.LoadTestMetrics:
        captured.update(endpoint=endpoint, model=model, vus=vus, duration=duration, api_key=api_key)
        return k6.LoadTestMetrics(
            p50_ms=120.5,
            p95_ms=612.8,
            requests_per_sec=61.25,
            total_requests=1840,
            error_rate=0.0163,
        )

    monkeypatch.setattr(loadtest_cmd, "run_chat_loadtest", fake_run)

    res = runner.invoke(
        _app(),
        ["loadtest", "chat", "--model", "qwen2.5", "--vus", "12", "--duration", "45s"],
    )
    assert res.exit_code == 0, res.output
    assert captured["model"] == "qwen2.5"
    assert captured["vus"] == 12
    assert captured["duration"] == "45s"
    assert captured["endpoint"] == "http://127.0.0.1:8080/v1/chat/completions"
    # Behavioural: latency / throughput figures rendered.
    assert "120.5" in res.output
    assert "612.8" in res.output


def test_chat_json_emits_machine_parseable_metrics(monkeypatch) -> None:
    from agmind.cli import loadtest_cmd
    from agmind.loadtest import k6

    monkeypatch.setattr(k6, "which_k6", lambda: "/usr/bin/k6")
    monkeypatch.setattr(
        loadtest_cmd,
        "run_chat_loadtest",
        lambda **_kw: k6.LoadTestMetrics(
            p50_ms=120.5,
            p95_ms=612.8,
            requests_per_sec=61.25,
            total_requests=1840,
            error_rate=0.0163,
        ),
    )
    res = runner.invoke(_app(), ["loadtest", "chat", "--model", "qwen", "--json"])
    assert res.exit_code == 0, res.output
    payload = json.loads(res.output[res.output.index("{") :])
    assert payload["p95_ms"] == 612.8
    assert payload["total_requests"] == 1840


def test_chat_nonzero_exit_when_run_fails(monkeypatch) -> None:
    from agmind.cli import loadtest_cmd
    from agmind.loadtest import k6

    monkeypatch.setattr(k6, "which_k6", lambda: "/usr/bin/k6")

    def boom(**_kw):  # type: ignore[no-untyped-def]
        raise k6.LoadTestError("k6 run exited non-zero (rc=1): boom")

    monkeypatch.setattr(loadtest_cmd, "run_chat_loadtest", boom)
    res = runner.invoke(_app(), ["loadtest", "chat", "--model", "qwen"])
    assert res.exit_code != 0
    assert "Traceback" not in res.output
    assert "k6 run" in res.output
