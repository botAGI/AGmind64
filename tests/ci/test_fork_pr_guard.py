"""Fail-closed lint: every self-hosted CI job must carry the fork-PR guard.

Purpose
-------
A `pull_request` event from a fork executes verbatim code (conftest/tests/
pre-commit/scripts) on the production Strix Halo box that holds every secret
and the docker group.  Only `test-strix-halo` (ci.yml:286-290) is guarded
today.  This test fails closed when ANY self-hosted job lacks the guard — so
a future self-hosted job cannot slip in without it.

Defense-in-depth: workflow_dispatch-only / push-only jobs are ALSO required
to carry the guard.  If a future `on:` trigger adds `pull_request`, the guard
must already be present.

Guard string (verbatim, from ci.yml:289-290):
    github.event.pull_request.head.repo.full_name == github.repository
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = pytest.mark.backend_any

# Canonical guard substring — MUST appear in the job-level `if:` of every
# self-hosted job.  This is the critical clause from the proven three-clause
# allow-list on test-strix-halo (ci.yml:286-290).
GUARD = "github.event.pull_request.head.repo.full_name == github.repository"

_WF_DIR = Path(__file__).resolve().parents[2] / ".github" / "workflows"


def _jobs(wf_path: Path) -> dict[str, Any]:
    """Return the jobs mapping from a workflow YAML file."""
    with wf_path.open() as fh:
        data = yaml.safe_load(fh)
    return data.get("jobs", {})


def _runs_on_self_hosted(job: dict[str, Any]) -> bool:
    """Return True if the job runs on any self-hosted runner."""
    runs_on = job.get("runs-on", [])
    if isinstance(runs_on, str):
        return runs_on == "self-hosted"
    if isinstance(runs_on, list):
        return "self-hosted" in runs_on
    # strategy.matrix jobs: runs-on may be an expression string
    return "self-hosted" in str(runs_on)


def _workflows_with_self_hosted_jobs() -> list[str]:
    """Every workflow file that actually declares a self-hosted job.

    DERIVED, never hard-coded: this test's docstring promises it fails closed when ANY
    self-hosted job lacks the guard, but a hard-coded ["ci.yml", "kubernetes-proof.yml"] pair
    silently exempted every other workflow — perf-nightly.yml ran an unguarded self-hosted job
    for exactly that reason. That is the repo's documented "guard in code the real path never
    calls = false coverage" class, so the parametrization is computed from the workflow dir.
    """
    names: list[str] = []
    for path in sorted(_WF_DIR.glob("*.yml")):
        try:
            jobs = _jobs(path)
        except (OSError, yaml.YAMLError):  # pragma: no cover - malformed workflow
            continue
        if any(_runs_on_self_hosted(job) for job in jobs.values()):
            names.append(path.name)
    return names


def test_self_hosted_workflow_discovery_is_not_empty() -> None:
    """Guard the guard: if discovery silently returned [], every parametrized case would
    vanish and the suite would go green while checking nothing."""
    discovered = _workflows_with_self_hosted_jobs()
    assert discovered, "no workflow with a self-hosted job found — discovery is broken"
    assert "ci.yml" in discovered, f"ci.yml must be discovered; got {discovered}"


@pytest.mark.parametrize("workflow_name", _workflows_with_self_hosted_jobs())
def test_every_self_hosted_job_is_fork_guarded(workflow_name: str) -> None:
    """Every self-hosted job must carry the fork-PR guard in its `if:` clause.

    Fail message names the workflow file and the first unguarded job key so the
    developer knows exactly what to fix.
    """
    wf_path = _WF_DIR / workflow_name
    assert wf_path.exists(), f"Workflow not found: {wf_path}"

    jobs = _jobs(wf_path)
    unguarded: list[str] = []

    for job_key, job in jobs.items():
        if not _runs_on_self_hosted(job):
            continue
        job_if = str(job.get("if", ""))
        if GUARD not in job_if:
            unguarded.append(job_key)

    assert not unguarded, (
        f"{workflow_name}: the following self-hosted jobs are missing the fork-PR guard"
        f" (GUARD substring not found in job `if:`):\n"
        + "\n".join(f"  - {j}" for j in unguarded)
        + f"\n\nRequired guard substring:\n  {GUARD!r}\n"
        "Add the proven `if: >-` three-clause block from test-strix-halo (ci.yml:286-290)"
        " to each listed job."
    )
