from __future__ import annotations

import json

import pytest

from agmind.ci.monitor import ActionRun, ActionRunner, CIMonitorReport

pytestmark = pytest.mark.backend_any


def _report() -> CIMonitorReport:
    return CIMonitorReport(
        repository="botAGI/AGmind64",
        runs=(
            ActionRun(
                database_id=101,
                title="develop smoke",
                workflow="ci",
                status="queued",
                conclusion="",
                event="push",
                branch="develop",
                created_at="2026-05-25T08:00:00Z",
                url="https://github.com/botAGI/AGmind64/actions/runs/101",
            ),
        ),
        runners=(
            ActionRunner(
                runner_id=1,
                name="strix",
                os="Linux",
                status="online",
                busy=True,
                labels=("self-hosted", "strix-halo"),
            ),
        ),
        warnings=(),
    )


def test_ci_status_json_output(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agmind.cli import ci_cmd

    monkeypatch.setattr(ci_cmd, "collect_ci_status", lambda repository, run_limit: _report())

    rc = ci_cmd.cmd_status(repository="botAGI/AGmind64", run_limit=3, as_json=True)

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["repository"] == "botAGI/AGmind64"
    assert payload["run_summary"] == {"queued": 1}
    assert payload["runner_summary"] == {"online_busy": 1}
    assert payload["runs"][0]["workflow"] == "ci"


def test_ci_status_text_output_includes_runner_and_queue(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agmind.cli import ci_cmd

    monkeypatch.setattr(ci_cmd, "collect_ci_status", lambda repository, run_limit: _report())

    rc = ci_cmd.cmd_status(repository=None, run_limit=10, as_json=False)

    assert rc == 0
    out = capsys.readouterr().out
    assert "Repository: botAGI/AGmind64" in out
    assert "Runs: queued=1" in out
    assert "ci" in out
    assert "Runners: online_busy=1" in out
    assert "strix" in out


def test_ci_status_returns_error_when_monitor_has_warnings_without_data(
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agmind.cli import ci_cmd

    monkeypatch.setattr(
        ci_cmd,
        "collect_ci_status",
        lambda repository, run_limit: CIMonitorReport(
            repository="botAGI/AGmind64",
            warnings=("gh not found",),
        ),
    )

    rc = ci_cmd.cmd_status(repository=None, run_limit=10, as_json=False)

    assert rc == 1
    assert "gh not found" in capsys.readouterr().err
