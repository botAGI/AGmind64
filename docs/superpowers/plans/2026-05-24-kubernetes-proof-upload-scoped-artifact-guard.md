# Kubernetes Proof Upload Scoped Artifact Guard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ensure the Kubernetes proof workflow drift guard verifies every target-declared proof artifact in the upload step itself.

**Architecture:** Reuse the existing upload-step helper added for `verification.json`. Change the target artifact loop in `_validate_workflow_target_contract()` from a broad workflow substring check to an `actions/upload-artifact@v4` step check. This closes false-positives where an artifact path appears in diagnostics, shell commands, or docs inside the workflow but is not uploaded.

**Tech Stack:** Python workflow contract validation, pytest.

---

### Task 1: RED Test

**Files:**
- Modify: `tests/test_governance_cmd.py`

- [x] **Step 1: Assert declared artifact upload path is required**

Add a test that removes only the upload path:

```text
local-kubernetes-proof/k3s/checksums.txt
```

while leaving diagnostic commands that reference `checksums.txt`. Expect:

```text
k3s: Kubernetes proof workflow missing artifact: local-kubernetes-proof/k3s/checksums.txt
```

- [x] **Step 2: Run RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_governance_cmd.py::test_kubernetes_proof_workflow_guard_rejects_missing_declared_artifact_upload -q
```

Expected: fail because the current guard accepts the `checksums.txt` reference from the diagnostic step.

### Task 2: Validator Fix

**Files:**
- Modify: `agmind/deploy/target_checks.py`

- [x] **Step 1: Use upload-step helper for declared artifacts**

Change the loop over `target.verification.artifacts` to call `_workflow_uploads_artifact(workflow, artifact)`.

- [x] **Step 2: Run focused GREEN**

Run:

```bash
.venv/bin/python -m pytest tests/test_governance_cmd.py::test_kubernetes_proof_workflow_guard_rejects_missing_declared_artifact_upload tests/test_governance_cmd.py::test_kubernetes_proof_workflow_guard_rejects_missing_verification_report_upload tests/test_kubernetes_dry_run.py::test_kubernetes_proof_workflow_check_script_runs -q
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

- [x] **Step 1: Record M7.D.24.I-prep checkpoint**

Update planning docs to say all proof artifact declarations are now checked against the upload-artifact step.

- [x] **Step 2: Run verification**

Run focused pytest, ruff format/check, mypy, workflow drift check, governance check, expanded M7 pytest, `git diff --check`, and full pre-commit.

- [x] **Step 3: Record outputs**

Append final verification outputs to this plan and `.planning/STATE.md`.

## Verification Log

- RED observed: the new test removed only
  `local-kubernetes-proof/k3s/checksums.txt` from the upload-artifact path
  list while leaving diagnostic references to `checksums.txt`. The old
  validator returned no errors.
- GREEN focused upload-scoped artifact slice:
  `.venv/bin/python -m pytest tests/test_governance_cmd.py::test_kubernetes_proof_workflow_guard_rejects_missing_declared_artifact_upload tests/test_governance_cmd.py::test_kubernetes_proof_workflow_guard_rejects_missing_verification_report_upload tests/test_kubernetes_dry_run.py::test_kubernetes_proof_workflow_check_script_runs -q`
  passed 3 tests.
- Focused regression set:
  `.venv/bin/python -m pytest tests/test_governance_cmd.py::test_kubernetes_proof_workflow_guard_rejects_missing_declared_artifact_upload tests/test_governance_cmd.py::test_kubernetes_proof_workflow_guard_rejects_missing_verification_report_upload tests/test_kubernetes_dry_run.py::test_kubernetes_proof_workflow_check_script_runs tests/test_governance_cmd.py::test_governance_check_script_runs -q`
  passed 4 tests.
- Static checks: `ruff format --check`, `ruff check`, and mypy passed for the
  touched workflow validator and tests after formatting
  `tests/test_governance_cmd.py`.
- Script gates: `scripts/kubernetes_proof_workflow_check.py` reported
  `kubernetes proof workflow OK: 1 targets`; `scripts/governance_check.py`
  reported `governance OK: 6 checks`.
- Expanded M7 pytest set passed 175 tests.
