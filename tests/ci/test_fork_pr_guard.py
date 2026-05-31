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


@pytest.mark.parametrize("workflow_name", ["ci.yml", "kubernetes-proof.yml"])
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
