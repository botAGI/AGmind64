"""Read-only GitHub Actions and self-hosted runner monitor."""

from __future__ import annotations

import json
import os
import re
from collections import Counter
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any

from agmind.core.proc import CommandResult, run_command

RUN_JSON_FIELDS = (
    "databaseId,displayTitle,workflowName,status,conclusion,event,headBranch,createdAt,url"
)
DEFAULT_RUN_LIMIT = 10


@dataclass(frozen=True)
class ActionRun:
    """GitHub Actions workflow run summary."""

    database_id: int
    title: str
    workflow: str
    status: str
    conclusion: str
    event: str
    branch: str
    created_at: str
    url: str


@dataclass(frozen=True)
class ActionRunner:
    """GitHub Actions runner summary."""

    runner_id: int
    name: str
    os: str
    status: str
    busy: bool
    labels: tuple[str, ...] = ()


@dataclass(frozen=True)
class CIMonitorReport:
    """Combined GitHub Actions queue and runner report."""

    repository: str
    runs: tuple[ActionRun, ...] = ()
    runners: tuple[ActionRunner, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def run_summary(self) -> dict[str, int]:
        return dict(sorted(Counter(run.status for run in self.runs).items()))

    @property
    def runner_summary(self) -> dict[str, int]:
        summary: Counter[str] = Counter()
        for runner in self.runners:
            if runner.status == "online" and runner.busy:
                summary["online_busy"] += 1
            elif runner.status == "online":
                summary["online_idle"] += 1
            else:
                summary[runner.status or "unknown"] += 1
        return dict(sorted(summary.items()))

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["run_summary"] = self.run_summary
        payload["runner_summary"] = self.runner_summary
        return payload


CommandRunner = Callable[[tuple[str, ...]], CommandResult]


def collect_ci_status(
    *,
    repository: str | None = None,
    run: CommandRunner | None = None,
    run_limit: int = DEFAULT_RUN_LIMIT,
) -> CIMonitorReport:
    """Collect GitHub Actions queue and self-hosted runner state through ``gh``."""
    runner = run or _run_command
    repo = repository or detect_repository(run=runner)
    warnings: list[str] = []
    if not repo:
        return CIMonitorReport(
            repository="",
            warnings=("GitHub repository not detected; pass --repo owner/name",),
        )

    runs = _collect_runs(repo, run_limit, runner, warnings)
    runners = _collect_runners(repo, runner, warnings)
    return CIMonitorReport(
        repository=repo,
        runs=runs,
        runners=runners,
        warnings=tuple(warnings),
    )


def detect_repository(*, run: CommandRunner | None = None) -> str:
    """Detect ``owner/name`` from env or ``git remote.origin.url``."""
    env_repo = os.environ.get("AGMIND_GITHUB_REPO", "").strip()
    if _is_repo_slug(env_repo):
        return env_repo

    runner = run or _run_command
    result = runner(("git", "config", "--get", "remote.origin.url"))
    if result.returncode != 0:
        return ""
    return _parse_github_remote(result.stdout.strip())


def _collect_runs(
    repo: str,
    run_limit: int,
    run: CommandRunner,
    warnings: list[str],
) -> tuple[ActionRun, ...]:
    result = run(
        (
            "gh",
            "run",
            "list",
            "--repo",
            repo,
            "--limit",
            str(run_limit),
            "--json",
            RUN_JSON_FIELDS,
        )
    )
    if result.returncode != 0:
        warnings.append(f"gh run list failed: {_command_error(result)}")
        return ()

    data = _json_value(result.stdout, default=[])
    if not isinstance(data, list):
        warnings.append("gh run list returned non-list JSON")
        return ()

    runs: list[ActionRun] = []
    for item in data:
        if isinstance(item, dict):
            runs.append(_action_run(item))
    return tuple(runs)


def _collect_runners(
    repo: str,
    run: CommandRunner,
    warnings: list[str],
) -> tuple[ActionRunner, ...]:
    result = run(("gh", "api", f"repos/{repo}/actions/runners"))
    if result.returncode != 0:
        warnings.append(f"gh runner API failed: {_command_error(result)}")
        return ()

    data = _json_value(result.stdout, default={})
    if not isinstance(data, dict):
        warnings.append("gh runner API returned non-object JSON")
        return ()
    raw_runners = data.get("runners", [])
    if not isinstance(raw_runners, list):
        warnings.append("gh runner API returned invalid runners list")
        return ()

    runners: list[ActionRunner] = []
    for item in raw_runners:
        if isinstance(item, dict):
            runners.append(_action_runner(item))
    return tuple(runners)


def _action_run(item: dict[str, Any]) -> ActionRun:
    return ActionRun(
        database_id=_int_value(item.get("databaseId")),
        title=_str_value(item.get("displayTitle")),
        workflow=_str_value(item.get("workflowName")),
        status=_str_value(item.get("status")),
        conclusion=_str_value(item.get("conclusion")),
        event=_str_value(item.get("event")),
        branch=_str_value(item.get("headBranch")),
        created_at=_str_value(item.get("createdAt")),
        url=_str_value(item.get("url")),
    )


def _action_runner(item: dict[str, Any]) -> ActionRunner:
    return ActionRunner(
        runner_id=_int_value(item.get("id")),
        name=_str_value(item.get("name")),
        os=_str_value(item.get("os")),
        status=_str_value(item.get("status")),
        busy=bool(item.get("busy")),
        labels=_runner_labels(item.get("labels")),
    )


def _runner_labels(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    labels: list[str] = []
    for item in value:
        if isinstance(item, dict):
            name = item.get("name")
            if isinstance(name, str) and name:
                labels.append(name)
    return tuple(labels)


def _parse_github_remote(remote: str) -> str:
    patterns = (
        r"github\.com[:/](?P<repo>[^/\s]+/[^/\s]+?)(?:\.git)?$",
        r"https://github\.com/(?P<repo>[^/\s]+/[^/\s]+?)(?:\.git)?$",
    )
    for pattern in patterns:
        match = re.search(pattern, remote)
        if match:
            repo = match.group("repo")
            return repo.removesuffix(".git") if _is_repo_slug(repo.removesuffix(".git")) else ""
    return ""


def _is_repo_slug(value: str) -> bool:
    return bool(re.match(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", value))


def _json_value(stdout: str, *, default: object) -> object:
    try:
        return json.loads(stdout or json.dumps(default))
    except json.JSONDecodeError:
        return default


def _command_error(result: CommandResult) -> str:
    return (result.stderr or result.stdout or f"exit {result.returncode}").strip()


def _str_value(value: object) -> str:
    return value if isinstance(value, str) else ""


def _int_value(value: object) -> int:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return 0


def _run_command(args: tuple[str, ...]) -> CommandResult:
    return run_command(args, timeout=20)


__all__ = [
    "ActionRun",
    "ActionRunner",
    "CIMonitorReport",
    "CommandResult",
    "collect_ci_status",
    "detect_repository",
]
