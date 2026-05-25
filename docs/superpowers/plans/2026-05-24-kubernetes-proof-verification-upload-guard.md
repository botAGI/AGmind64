# Kubernetes Proof Verification Upload Guard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ensure the Kubernetes proof workflow drift guard verifies that `verification.json` is actually uploaded, not merely written by the verifier step.

**Architecture:** Keep the manual workflow unchanged. Tighten `validate_kubernetes_proof_workflow()` so verifier report validation looks inside the `actions/upload-artifact@v4` step. This closes the false-positive where `verification.json` appears in the verifier `tee` command but is absent from the upload path list.

**Tech Stack:** Python text-based workflow contract validation, pytest.

---

### Task 1: RED Test

**Files:**
- Modify: `tests/test_governance_cmd.py`

- [x] **Step 1: Assert missing upload path is rejected**

Add a test that removes only this upload path from a temporary workflow copy:

```text
local-kubernetes-proof/k3s/verification.json
```

while leaving the verifier `tee .../verification.json` command intact. Expect:

```text
k3s: Kubernetes proof workflow missing verifier artifact: local-kubernetes-proof/k3s/verification.json
```

- [x] **Step 2: Run RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_governance_cmd.py::test_kubernetes_proof_workflow_guard_rejects_missing_verification_report_upload -q
```

Expected: fail because the current guard accepts any occurrence of `verification.json` in the workflow.

### Task 2: Validator Fix

**Files:**
- Modify: `agmind/deploy/target_checks.py`

- [x] **Step 1: Add upload-step helper**

Add a helper that finds the workflow step containing `actions/upload-artifact@v4` and checks whether that same step contains a given artifact path.

- [x] **Step 2: Use it for verifier report**

Replace the broad `verifier_report not in workflow` check with the upload-step helper.

- [x] **Step 3: Run focused GREEN**

Run:

```bash
.venv/bin/python -m pytest tests/test_governance_cmd.py::test_kubernetes_proof_workflow_guard_rejects_missing_verification_report_upload tests/test_governance_cmd.py::test_kubernetes_proof_workflow_guard_rejects_missing_verification_report tests/test_kubernetes_dry_run.py::test_kubernetes_proof_workflow_check_script_runs -q
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

- [x] **Step 1: Record M7.D.24.H-prep checkpoint**

Update planning docs to say the drift guard verifies `verification.json` in the upload-artifact step specifically.

- [x] **Step 2: Run verification**

Run focused pytest, ruff format/check, mypy, workflow drift check, governance check, expanded M7 pytest, `git diff --check`, and full pre-commit.

- [x] **Step 3: Record outputs**

Append final verification outputs to this plan and `.planning/STATE.md`.

## Verification Log

- RED observed: the new test removed only
  `local-kubernetes-proof/k3s/verification.json` from the upload-artifact path
  list while keeping the verifier `tee .../verification.json` command. The old
  validator returned no errors.
- GREEN focused verifier-upload slice:
  `.venv/bin/python -m pytest tests/test_governance_cmd.py::test_kubernetes_proof_workflow_guard_rejects_missing_verification_report_upload tests/test_governance_cmd.py::test_kubernetes_proof_workflow_guard_rejects_missing_verification_report tests/test_kubernetes_dry_run.py::test_kubernetes_proof_workflow_check_script_runs -q`
  passed 3 tests.
- Focused regression set:
  `.venv/bin/python -m pytest tests/test_governance_cmd.py::test_kubernetes_proof_workflow_guard_rejects_missing_verification_report_upload tests/test_governance_cmd.py::test_kubernetes_proof_workflow_guard_rejects_missing_verification_report tests/test_kubernetes_dry_run.py::test_kubernetes_proof_workflow_check_script_runs tests/test_governance_cmd.py::test_governance_check_script_runs -q`
  passed 4 tests.
- Static checks: `ruff format --check`, `ruff check`, and mypy passed for the
  touched workflow validator and tests.
- Script gates: `scripts/kubernetes_proof_workflow_check.py` reported
  `kubernetes proof workflow OK: 1 targets`; `scripts/governance_check.py`
  reported `governance OK: 6 checks`.
- Expanded M7 pytest set passed 174 tests.
