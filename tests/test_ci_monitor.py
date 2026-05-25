from __future__ import annotations

import json

import pytest

from agmind.ci.monitor import CommandResult

pytestmark = pytest.mark.backend_any


def _runner(fixtures: dict[tuple[str, ...], CommandResult]):
    def run(args: tuple[str, ...]) -> CommandResult:
        return fixtures.get(args, CommandResult(returncode=127, stderr="not found"))

    return run


def test_collect_ci_status_reports_runs_and_self_hosted_runners() -> None:
    from agmind.ci.monitor import collect_ci_status

    fixtures = {
        (
            "gh",
            "run",
            "list",
            "--repo",
            "botAGI/AGmind64",
            "--limit",
            "5",
            "--json",
            "databaseId,displayTitle,workflowName,status,conclusion,event,headBranch,createdAt,url",
        ): CommandResult(
            returncode=0,
            stdout=json.dumps(
                [
                    {
                        "databaseId": 101,
                        "displayTitle": "develop smoke",
                        "workflowName": "ci",
                        "status": "queued",
                        "conclusion": "",
                        "event": "push",
                        "headBranch": "develop",
                        "createdAt": "2026-05-25T08:00:00Z",
                        "url": "https://github.com/botAGI/AGmind64/actions/runs/101",
                    },
                    {
                        "databaseId": 100,
                        "displayTitle": "k3s proof",
                        "workflowName": "kubernetes-proof",
                        "status": "in_progress",
                        "conclusion": "",
                        "event": "workflow_dispatch",
                        "headBranch": "develop",
                        "createdAt": "2026-05-25T07:30:00Z",
                        "url": "https://github.com/botAGI/AGmind64/actions/runs/100",
                    },
                ]
            ),
        ),
        ("gh", "api", "repos/botAGI/AGmind64/actions/runners"): CommandResult(
            returncode=0,
            stdout=json.dumps(
                {
                    "total_count": 2,
                    "runners": [
                        {
                            "id": 1,
                            "name": "strix",
                            "os": "Linux",
                            "status": "online",
                            "busy": True,
                            "labels": [{"name": "self-hosted"}, {"name": "strix-halo"}],
                        },
                        {
                            "id": 2,
                            "name": "k3s-proof",
                            "os": "Linux",
                            "status": "offline",
                            "busy": False,
                            "labels": [{"name": "self-hosted"}, {"name": "k3s"}],
                        },
                    ],
                }
            ),
        ),
    }

    report = collect_ci_status(
        repository="botAGI/AGmind64",
        run=_runner(fixtures),
        run_limit=5,
    )

    assert report.repository == "botAGI/AGmind64"
    assert [item.workflow for item in report.runs] == ["ci", "kubernetes-proof"]
    assert report.runs[0].status == "queued"
    assert report.runners[0].busy is True
    assert report.runners[0].labels == ("self-hosted", "strix-halo")
    assert report.runner_summary == {"offline": 1, "online_busy": 1}
    assert report.run_summary == {"in_progress": 1, "queued": 1}


def test_collect_ci_status_reports_gh_errors_without_crashing() -> None:
    from agmind.ci.monitor import collect_ci_status

    fixtures = {
        (
            "gh",
            "run",
            "list",
            "--repo",
            "botAGI/AGmind64",
            "--limit",
            "10",
            "--json",
            "databaseId,displayTitle,workflowName,status,conclusion,event,headBranch,createdAt,url",
        ): CommandResult(returncode=1, stderr="not authenticated"),
        ("gh", "api", "repos/botAGI/AGmind64/actions/runners"): CommandResult(
            returncode=1, stderr="HTTP 401"
        ),
    }

    report = collect_ci_status(repository="botAGI/AGmind64", run=_runner(fixtures))

    assert report.runs == ()
    assert report.runners == ()
    assert "gh run list failed: not authenticated" in report.warnings
    assert "gh runner API failed: HTTP 401" in report.warnings


def test_detect_repository_from_github_remote() -> None:
    from agmind.ci.monitor import detect_repository

    report = detect_repository(
        run=_runner(
            {
                ("git", "config", "--get", "remote.origin.url"): CommandResult(
                    returncode=0, stdout="git@github.com:botAGI/AGmind64.git\n"
                )
            }
        )
    )

    assert report == "botAGI/AGmind64"
