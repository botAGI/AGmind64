"""Phase 12-01 (M8): a reproducible, GitHub-hosted public CI lane.

Every quality gate used to run only on `[self-hosted, linux, x64]` behind a fork-PR guard, so
fork PRs got NO CI and the gate depended on a pre-provisioned `$HOME/.local/bin/uv`. This
asserts a GitHub-hosted deterministic lane exists that runs the core checks, installs uv
fresh, and is NOT fork-guarded (external contributions get CI)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = pytest.mark.backend_any

_CI = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci.yml"
_GUARD = "github.event.pull_request.head.repo.full_name == github.repository"


def _jobs() -> dict[str, Any]:
    return yaml.safe_load(_CI.read_text(encoding="utf-8")).get("jobs", {})


def _github_hosted_jobs() -> dict[str, Any]:
    out = {}
    for key, job in _jobs().items():
        runs_on = job.get("runs-on", "")
        if isinstance(runs_on, str) and runs_on.startswith("ubuntu-"):
            out[key] = job
    return out


def test_a_github_hosted_lane_exists() -> None:
    assert _github_hosted_jobs(), "no GitHub-hosted (ubuntu-*) quality lane in ci.yml"


def test_public_lane_runs_core_checks() -> None:
    hosted = _github_hosted_jobs()
    blob = yaml.safe_dump(hosted)
    assert "ruff check" in blob
    assert "mypy" in blob
    assert "pytest" in blob


def test_public_lane_is_not_fork_guarded() -> None:
    """The whole point: a GitHub-hosted lane is safe to run fork code, so it must NOT carry
    the self-hosted fork guard — otherwise fork PRs still get no CI."""
    for key, job in _github_hosted_jobs().items():
        assert _GUARD not in str(job.get("if", "")), (
            f"GitHub-hosted job '{key}' is fork-guarded — fork PRs would still get no CI"
        )


def test_public_lane_installs_uv_fresh() -> None:
    """Must not assume a pre-provisioned `$HOME/.local/bin/uv` (the self-hosted footgun)."""
    blob = yaml.safe_dump(_github_hosted_jobs())
    assert "astral.sh/uv/install.sh" in blob or "setup-uv" in blob
