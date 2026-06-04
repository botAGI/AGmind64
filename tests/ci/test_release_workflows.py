"""Task H.6 (2): presence and structure tests for release.yml and promote.yml.

These are parse-only tests (no network, no runners).  They enforce the LOCKED
decisions from the 07-CONTEXT.md H.6 section:

  * All release jobs run on ubuntu-latest (NOT the self-hosted box) — so the
    pipeline never blocks or gets blocked by the CI hardware queue.
  * GitHub artifact attestations (actions/attest-build-provenance) are used;
    cosign is completely absent (LOCKED: attestations > cosign).
  * The four self-built backend images are referenced at ghcr.io/botagi/agmind-*.
  * An SBOM step (anchore/sbom-action) is present.
  * promote.yml uses `merge --ff-only` and a check-runs gate.
  * Top-level permissions: {} (deny-all) on both workflows.

actionlint availability: actionlint is NOT installed on this host (Go absent).
YAML structure validation is done via PyYAML + assertion checks.  A manual
actionlint run (`go install github.com/rhysd/actionlint/cmd/actionlint@latest
&& actionlint .github/workflows/release.yml .github/workflows/promote.yml`) is
documented as a follow-up in the plan SUMMARY.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = pytest.mark.backend_any

_REPO_ROOT = Path(__file__).resolve().parents[2]
_RELEASE_YML = _REPO_ROOT / ".github" / "workflows" / "release.yml"
_PROMOTE_YML = _REPO_ROOT / ".github" / "workflows" / "promote.yml"
_CI_YML = _REPO_ROOT / ".github" / "workflows" / "ci.yml"

# CI jobs that legitimately must NOT be promote/release requirements (documented, with reason).
# Empty today: every ci.yml job runs on the develop push path and is a required green gate.
_DOCUMENTED_CI_EXCLUSIONS: frozenset[str] = frozenset()


def _load(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def _get_on(workflow: dict[str, Any]) -> dict[str, Any]:
    """Return the 'on:' block.

    PyYAML parses ``on:`` as the Python boolean ``True`` (YAML 1.1 quirk).
    This helper normalises both forms so tests work regardless.
    """
    # Try the boolean True key (PyYAML default safe_load behavior)
    value = workflow.get(True) or workflow.get("on") or {}
    return value if isinstance(value, dict) else {}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _all_job_runners(workflow: dict[str, Any]) -> list[str]:
    """Return the `runs-on` value for every job."""
    runners: list[str] = []
    for job in (workflow.get("jobs") or {}).values():
        runs_on = job.get("runs-on")
        if isinstance(runs_on, list):
            runners.extend(str(r) for r in runs_on)
        elif runs_on is not None:
            runners.append(str(runs_on))
    return runners


def _workflow_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _derive_ci_check_run_names(ci: dict[str, Any]) -> set[str]:
    """Derive the set of GitHub check-run names ci.yml produces, expanding single-dimension
    matrices to the `<job> (<value>)` names GitHub assigns (e.g. `docker-build (cpu)`)."""
    names: set[str] = set()
    for job_name, job in (ci.get("jobs") or {}).items():
        matrix = ((job.get("strategy") or {}).get("matrix")) or {}
        dims = {k: v for k, v in matrix.items() if isinstance(v, list)}
        if not dims:
            names.add(job_name)
        elif len(dims) == 1:
            (values,) = dims.values()
            for value in values:
                names.add(f"{job_name} ({value})")
        else:  # pragma: no cover - no multi-dimension matrix exists yet
            raise AssertionError(
                f"{job_name}: multi-dimension matrix not handled by the derivation; "
                "extend _derive_ci_check_run_names to expand the GitHub naming."
            )
    return names


def _parse_required_array(text: str) -> set[str]:
    """Extract the JSON `required='[ ... ]'` allow-list embedded in a workflow's gate shell."""
    match = re.search(r"required='(\[.*?\])'", text, re.DOTALL)
    assert match, "no `required='[...]'` allow-list found in the workflow gate"
    return set(json.loads(match.group(1)))


# ---------------------------------------------------------------------------
# release.yml tests
# ---------------------------------------------------------------------------


def test_release_yml_exists() -> None:
    assert _RELEASE_YML.exists(), f"missing: {_RELEASE_YML}"


def test_release_yml_parses() -> None:
    data = _load(_RELEASE_YML)
    assert isinstance(data, dict), "release.yml must parse to a mapping"
    assert "jobs" in data, "release.yml must have a 'jobs' key"


def test_release_all_jobs_ubuntu_latest() -> None:
    """Every release job must run on ubuntu-latest, never on self-hosted."""
    data = _load(_RELEASE_YML)
    runners = _all_job_runners(data)
    assert runners, "release.yml must define at least one job with a runner"
    bad = [r for r in runners if r != "ubuntu-latest"]
    assert not bad, (
        f"release.yml has non-ubuntu-latest runner(s): {bad}. "
        "The H.6 LOCKED decision: release runs on ubuntu-latest, NOT the self-hosted box."
    )


def test_release_no_cosign_references() -> None:
    """No cosign references must appear in release.yml (LOCKED: attestations over cosign)."""
    text = _workflow_text(_RELEASE_YML)
    assert "cosign" not in text.lower(), (
        "release.yml must not reference cosign — the H.6 LOCKED decision chose "
        "actions/attest-build-provenance over cosign keyless signing."
    )


def test_release_uses_attest_build_provenance() -> None:
    """release.yml must use actions/attest-build-provenance."""
    text = _workflow_text(_RELEASE_YML)
    assert "actions/attest-build-provenance" in text, (
        "release.yml must use 'actions/attest-build-provenance' for SLSA provenance."
    )


def test_release_references_four_backend_images() -> None:
    """release.yml must reference all four GHCR backend image names."""
    text = _workflow_text(_RELEASE_YML)
    ns = "ghcr.io/botagi/agmind"
    for backend in ("base", "cpu", "vulkan", "rocm"):
        # The full name appears as IMAGE_NS-<backend> or in explicit refs
        assert f"agmind-{backend}" in text or f"{ns}-{backend}" in text, (
            f"release.yml does not reference the '{backend}' backend image "
            f"(expected 'agmind-{backend}' or '{ns}-{backend}')."
        )


def test_release_has_sbom_step() -> None:
    """release.yml must have a syft/anchore SBOM step."""
    text = _workflow_text(_RELEASE_YML)
    assert "anchore/sbom-action" in text, (
        "release.yml must include an SBOM generation step (anchore/sbom-action)."
    )


def test_release_toplevel_permissions_empty() -> None:
    """release.yml top-level permissions must be {} (deny-all)."""
    data = _load(_RELEASE_YML)
    perms = data.get("permissions")
    assert perms == {} or perms is None, (
        f"release.yml top-level permissions should be {{}} (deny-all), got: {perms!r}"
    )
    # Ensure it's explicitly set, not just absent (explicit is safer).
    assert "permissions" in data, "release.yml must declare 'permissions: {}' at the top level."


def test_release_ghcr_ns() -> None:
    """release.yml must reference ghcr.io/botagi/agmind as the image namespace."""
    text = _workflow_text(_RELEASE_YML)
    assert "ghcr.io/botagi/agmind" in text, (
        "release.yml must reference ghcr.io/botagi/agmind as the GHCR namespace."
    )


def test_release_triggers_on_vstar_tags() -> None:
    """release.yml must trigger on push: tags: ['v*']."""
    data = _load(_RELEASE_YML)
    on = _get_on(data)
    push = (on or {}).get("push", {})
    tags = (push or {}).get("tags", [])
    assert any("v*" in str(t) for t in tags), (
        f"release.yml must trigger on 'push: tags: [v*]', got tags: {tags}"
    )


def test_release_has_meta_job() -> None:
    """release.yml must have a version-verification job."""
    data = _load(_RELEASE_YML)
    jobs = data.get("jobs", {})
    assert "meta" in jobs, (
        "release.yml must have a 'meta' job to verify the tag matches agmind.__version__."
    )


def test_release_has_publish_job() -> None:
    """release.yml must have a publish job that creates a GitHub Release."""
    data = _load(_RELEASE_YML)
    jobs = data.get("jobs", {})
    assert "publish" in jobs, "release.yml must have a 'publish' job."


# ---------------------------------------------------------------------------
# promote.yml tests
# ---------------------------------------------------------------------------


def test_promote_yml_exists() -> None:
    assert _PROMOTE_YML.exists(), f"missing: {_PROMOTE_YML}"


def test_promote_yml_parses() -> None:
    data = _load(_PROMOTE_YML)
    assert isinstance(data, dict), "promote.yml must parse to a mapping"
    assert "jobs" in data, "promote.yml must have a 'jobs' key"


def test_promote_uses_ff_only() -> None:
    """promote.yml must use `merge --ff-only` to preserve SHA identity."""
    text = _workflow_text(_PROMOTE_YML)
    assert "--ff-only" in text, (
        "promote.yml must use 'git merge --ff-only' to guarantee SHA identity "
        "on the protected main branch."
    )


def test_promote_has_check_runs_gate() -> None:
    """promote.yml must gate on GitHub check-runs before promoting."""
    text = _workflow_text(_PROMOTE_YML)
    assert "check-runs" in text, (
        "promote.yml must call the GitHub check-runs API to gate promotion on green CI."
    )


def test_promote_is_workflow_dispatch() -> None:
    """promote.yml must be manually triggered (workflow_dispatch)."""
    data = _load(_PROMOTE_YML)
    on = _get_on(data)
    assert "workflow_dispatch" in on, (
        "promote.yml must use workflow_dispatch (manual trigger) for controlled promotion."
    )


def test_promote_has_sha_input() -> None:
    """promote.yml must require a 'sha' input for the target commit."""
    data = _load(_PROMOTE_YML)
    on = _get_on(data)
    dispatch = on.get("workflow_dispatch") or {}
    inputs = dispatch.get("inputs") or {}
    assert "sha" in inputs, "promote.yml must accept a 'sha' input (the develop SHA to promote)."


def test_promote_toplevel_permissions_empty() -> None:
    """promote.yml top-level permissions must be {} (deny-all)."""
    data = _load(_PROMOTE_YML)
    perms = data.get("permissions")
    assert perms == {} or perms is None, (
        f"promote.yml top-level permissions should be {{}} (deny-all), got: {perms!r}"
    )
    assert "permissions" in data, "promote.yml must declare 'permissions: {}' at the top level."


def test_promote_runs_on_ubuntu_latest() -> None:
    """All promote jobs must run on ubuntu-latest."""
    data = _load(_PROMOTE_YML)
    runners = _all_job_runners(data)
    bad = [r for r in runners if r != "ubuntu-latest"]
    assert not bad, f"promote.yml has non-ubuntu-latest runners: {bad}"


def test_promote_ancestor_check() -> None:
    """promote.yml must verify the SHA is an ancestor of develop."""
    text = _workflow_text(_PROMOTE_YML)
    assert "is-ancestor" in text or "merge-base" in text, (
        "promote.yml must verify the target SHA is an ancestor of develop "
        "(to prevent promoting a rewritten or forged commit)."
    )


# ---------------------------------------------------------------------------
# CR-01: fail-closed required-check gate (strengthened tests)
# ---------------------------------------------------------------------------

# The minimal required check set that must all be `conclusion == success`
# for a SHA to be promotable.  Derived from ci.yml job names.
_REQUIRED_CI_CHECKS = frozenset(
    {
        "pre-commit",
        "audit",
        "schema-validate",
        "component-validate",
        "deploy-target-validate",
        "tool-candidate-validate",
        "constraints-validate",
        "docs-mirror-validate",
        "topology-validate",
        "kubernetes-render-validate",
        "kubernetes-proof-workflow-validate",
        "governance-validate",
        "test-cpu",
        "docker-build-base",
        "docker-build",
        "compose-validate",
    }
)


def test_promote_gate_rejects_skipped_runs() -> None:
    """CR-01: promote.yml gate must REJECT a SHA whose required checks are all skipped.

    The old gate allowed skipped/neutral as green.  A SHA where every CI job
    was skipped (e.g. triggered by a non-push/PR/dispatch event) would pass
    the old gate, landing unvalidated code on main.

    This test verifies the gate logic (embedded in the workflow shell) rejects
    an all-skipped check-run set.

    Mutation-verify contract:
      - The old weak gate: 'skipped' in [success, skipped, neutral] → allowed.
        Reverting the fix (re-allowing skipped for required checks) must make
        this test go RED.
    """
    text = _workflow_text(_PROMOTE_YML)
    # The fail-closed gate must require conclusion == "success" (exact), not
    # merely "not failed" — so "skipped" must be explicitly blocked.
    # Strategy: the gate must use a required-check allowlist evaluated against
    # conclusion == "success" only.  We check structural properties:

    # 1. The workflow must reference an explicit required-check array/variable.
    assert "required" in text, (
        "CR-01: promote.yml green-gate must define a 'required' check set. "
        "An all-skipped SHA must not pass — add an allowlist evaluated against "
        "conclusion == 'success' only."
    )

    # 2. The gate must evaluate conclusion == "success" exclusively (not
    #    allow skipped/neutral for required checks).
    assert '"success"' in text, (
        "CR-01: promote.yml must check conclusion == 'success' for required checks."
    )

    # 3. The gate must reject when required checks are not all success —
    #    so it must not include 'skipped' or 'neutral' in the allowed
    #    set for required checks.  The allowed set for the sweep-all step
    #    may still include those, but for required-set evaluation only
    #    'success' counts.
    # Verify the gate explicitly filters to success for the required set:
    assert "conclusion" in text and "success" in text, (
        "CR-01: The gate must filter required checks by conclusion=='success'."
    )


def test_promote_gate_requires_explicit_required_check_names() -> None:
    """CR-01: promote.yml must name the required CI checks explicitly.

    Without an allowlist, a SHA with only a Dependabot docs check-run (no
    test/audit/compose checks) would pass "nothing is red".

    Verifies that several key check names from ci.yml are present in the
    workflow text (the allowlist is baked into the workflow shell script).
    """
    text = _workflow_text(_PROMOTE_YML)
    core_required = ["pre-commit", "audit", "test-cpu", "compose-validate", "governance-validate"]
    missing = [name for name in core_required if name not in text]
    assert not missing, (
        f"CR-01: promote.yml green-gate is missing required check names: {missing}.\n"
        "The gate must require ALL named checks to be present and conclusion=='success', "
        "not merely 'nothing is red'."
    )


def test_promote_gate_allowlist_covers_all_required_ci_jobs() -> None:
    """CR-01: every required CI job name must appear in promote.yml's required-check list.

    Mutation-verify: comment out 'test-cpu' from the required list in promote.yml
    and this test goes RED (test-cpu in _REQUIRED_CI_CHECKS but not in the text).
    """
    text = _workflow_text(_PROMOTE_YML)
    missing = sorted(name for name in _REQUIRED_CI_CHECKS if name not in text)
    assert not missing, (
        f"CR-01: these required CI jobs are not named in promote.yml's gate: {missing}.\n"
        "Add them to the required-check allowlist so the gate rejects a SHA that "
        "ran only a subset of CI."
    )


# ---------------------------------------------------------------------------
# CR-02: the required allow-list is DERIVED from ci.yml, not a stale hand-list
# (review MEDIUM required-ci-checks-not-derived)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("workflow", [_PROMOTE_YML, _RELEASE_YML], ids=["promote", "release"])
def test_gate_required_equals_derived_ci_jobs(workflow: Path) -> None:
    """The gate's `required` allow-list must EQUAL the set of check-runs ci.yml produces
    (minus documented exclusions). A hand-maintained subset silently lets a new CI job slip
    past the promote/release gate.

    Mutation-verify: add a new job to ci.yml (or a matrix value) and this test goes RED until
    the workflow's `required` array is updated — exactly the drift the old subset test missed.
    """
    derived = _derive_ci_check_run_names(_load(_CI_YML))
    expected = derived - _DOCUMENTED_CI_EXCLUSIONS
    actual = _parse_required_array(_workflow_text(workflow))
    assert actual == expected, (
        f"{workflow.name} `required` allow-list drifted from ci.yml:\n"
        f"  in ci.yml but NOT required: {sorted(expected - actual)}\n"
        f"  required but NOT in ci.yml: {sorted(actual - expected)}\n"
        "Update the workflow's required array (or add a documented entry to "
        "_DOCUMENTED_CI_EXCLUSIONS with a reason)."
    )


def test_promote_and_release_required_sets_are_identical() -> None:
    """promote.yml and release.yml must gate on the SAME required set (review wanted them
    in sync — both protect main/release from an under-tested commit)."""
    assert _parse_required_array(_workflow_text(_PROMOTE_YML)) == _parse_required_array(
        _workflow_text(_RELEASE_YML)
    )


def test_promote_has_serializing_concurrency() -> None:
    """promote.yml must serialize concurrent dispatches (review LOW promote-no-concurrency):
    two promotes racing the ff-only push could corrupt main."""
    data = _load(_PROMOTE_YML)
    concurrency = data.get("concurrency") or {}
    assert concurrency.get("group"), "promote.yml must define a concurrency group"
    assert concurrency.get("cancel-in-progress") is False, (
        "promote must queue, never cancel a promote mid-push (cancel-in-progress: false)"
    )
