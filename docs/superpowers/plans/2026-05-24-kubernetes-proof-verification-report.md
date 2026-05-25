# Kubernetes Proof Verification Report Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upload a machine-readable verifier report from the manual Kubernetes proof workflow.

**Architecture:** Keep the existing proof bundle and verifier. Change the manual workflow verifier step to run `scripts/kubernetes_dry_run.py --json --verify-artifact-dir local-kubernetes-proof/k3s`, tee the JSON output into `local-kubernetes-proof/k3s/verification.json`, preserve the verifier exit status, and upload the report with the proof bundle. Extend the workflow drift guard so the verifier report cannot silently disappear from future workflow edits.

**Tech Stack:** GitHub Actions shell pipeline, Python workflow contract validation, pytest.

---

### Task 1: RED Tests

**Files:**
- Modify: `tests/test_kubernetes_dry_run.py`
- Modify: `tests/test_governance_cmd.py`

- [x] **Step 1: Assert workflow captures verifier JSON**

Update the manual workflow test to require:

```bash
mkdir -p local-kubernetes-proof/k3s
.venv/bin/python scripts/kubernetes_dry_run.py --json --verify-artifact-dir local-kubernetes-proof/k3s | tee local-kubernetes-proof/k3s/verification.json
status=${PIPESTATUS[0]}
exit "$status"
```

and upload path:

```text
local-kubernetes-proof/k3s/verification.json
```

- [x] **Step 2: Assert drift guard rejects missing JSON report**

Add a test that removes `| tee local-kubernetes-proof/k3s/verification.json`
from a temporary workflow copy and expects:

```text
k3s: Kubernetes proof workflow verifier must write verification.json
```

- [x] **Step 3: Run RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_kubernetes_dry_run.py::test_ci_has_manual_kubernetes_proof_artifact_workflow tests/test_governance_cmd.py::test_kubernetes_proof_workflow_guard_rejects_missing_verification_report -q
```

Expected: fail because the verifier is still text-only and the drift guard does not require `verification.json`.

### Task 2: Workflow and Drift Guard

**Files:**
- Modify: `.github/workflows/kubernetes-proof.yml`
- Modify: `agmind/deploy/target_checks.py`
- Modify: `tests/test_governance_cmd.py`

- [x] **Step 1: Capture verifier JSON**

Convert the verifier step to a shell block that creates the proof directory,
runs the verifier in JSON mode, tees output to `verification.json`, stores
`${PIPESTATUS[0]}`, and exits with that status.

- [x] **Step 2: Upload verifier report**

Add `local-kubernetes-proof/k3s/verification.json` to the upload artifact path.

- [x] **Step 3: Extend drift guard**

Require the verifier step to contain `--json --verify-artifact-dir`, `tee
<artifact_dir>/verification.json`, `${PIPESTATUS[0]}`, and require the upload
path to include `<artifact_dir>/verification.json`.

- [x] **Step 4: Run focused GREEN**

Run:

```bash
.venv/bin/python -m pytest tests/test_kubernetes_dry_run.py::test_ci_has_manual_kubernetes_proof_artifact_workflow tests/test_governance_cmd.py::test_kubernetes_proof_workflow_guard_rejects_missing_verification_report tests/test_kubernetes_dry_run.py::test_kubernetes_proof_workflow_check_script_runs -q
```

Expected: all pass.

### Task 3: Documentation and Verification

**Files:**
- Modify: `.planning/STATE.md`
- Modify: `.planning/BACKLOG.md`
- Modify: `.planning/ROADMAP.md`
- Modify: `.planning/codebase/ARCHITECTURE.md`
- Modify: `.planning/codebase/INDEX.md`
- Modify: this plan file

- [x] **Step 1: Record M7.D.24.G-prep checkpoint**

Update planning docs to say the manual workflow uploads `verification.json` and
the drift guard enforces it.

- [x] **Step 2: Run verification**

Run focused pytest, ruff format/check, mypy, workflow drift check, governance
check, expanded M7 pytest, `git diff --check`, and full pre-commit.

- [x] **Step 3: Record outputs**

Append final verification outputs to this plan and `.planning/STATE.md`.

## Verification Log

- RED observed: focused pytest failed because the manual workflow verifier was
  still text-only and the workflow drift guard did not require
  `verification.json`.
- GREEN focused workflow/validator slice:
  `.venv/bin/python -m pytest tests/test_kubernetes_dry_run.py::test_ci_has_manual_kubernetes_proof_artifact_workflow tests/test_governance_cmd.py::test_kubernetes_proof_workflow_guard_rejects_missing_verification_report tests/test_governance_cmd.py::test_kubernetes_proof_workflow_guard_rejects_non_always_verifier tests/test_kubernetes_dry_run.py::test_kubernetes_proof_workflow_check_script_runs -q`
  passed 4 tests.
- Focused regression set:
  `.venv/bin/python -m pytest tests/test_kubernetes_dry_run.py::test_ci_has_manual_kubernetes_proof_artifact_workflow tests/test_governance_cmd.py::test_kubernetes_proof_workflow_guard_rejects_missing_verification_report tests/test_governance_cmd.py::test_kubernetes_proof_workflow_guard_rejects_non_always_verifier tests/test_kubernetes_dry_run.py::test_kubernetes_proof_workflow_check_script_runs tests/test_governance_cmd.py::test_governance_check_script_runs -q`
  passed 5 tests.
- Static checks: `ruff format --check`, `ruff check`, and mypy passed for the
  touched workflow validator and tests after formatting
  `agmind/deploy/target_checks.py`.
- Script gates: `scripts/kubernetes_proof_workflow_check.py` reported
  `kubernetes proof workflow OK: 1 targets`; `scripts/governance_check.py`
  reported `governance OK: 6 checks`.
- Expanded M7 pytest set passed 173 tests.
