# Kubernetes Proof Always Verify Workflow Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ensure the manual Kubernetes proof workflow verifies any generated bundle even when the live dry-run proof step fails.

**Architecture:** Keep the existing manual `kubernetes-proof` workflow and drift guard. Add `if: always()` to the `Verify k3s proof bundle` step so GitHub Actions attempts bundle verification after failed proof runs, then teach `validate_kubernetes_proof_workflow()` to reject workflows where the verifier step is not `always()` guarded.

**Tech Stack:** GitHub Actions YAML-as-text validation, Python contract checks, pytest.

---

### Task 1: RED Tests

**Files:**
- Modify: `tests/test_kubernetes_dry_run.py`
- Modify: `tests/test_governance_cmd.py`

- [x] **Step 1: Assert workflow verifier runs with `if: always()`**

Add an assertion to the manual workflow test that requires:

```yaml
- name: Verify k3s proof bundle
  if: always()
  run: .venv/bin/python scripts/kubernetes_dry_run.py --verify-artifact-dir local-kubernetes-proof/k3s
```

- [x] **Step 2: Assert drift guard rejects missing verifier `if: always()`**

Add a test that writes a copy of `.github/workflows/kubernetes-proof.yml` with
the verifier `if: always()` line removed, then calls
`validate_kubernetes_proof_workflow(load_deploy_targets(), workflow_path=tmp_workflow)`.
Expected error:

```text
k3s: Kubernetes proof workflow verifier must run with if: always()
```

- [x] **Step 3: Run RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_kubernetes_dry_run.py::test_ci_has_manual_kubernetes_proof_artifact_workflow tests/test_governance_cmd.py::test_kubernetes_proof_workflow_guard_rejects_non_always_verifier -q
```

Expected: fail because the verifier step is not `always()` guarded and the drift guard does not enforce it yet.

### Task 2: Workflow and Validator

**Files:**
- Modify: `.github/workflows/kubernetes-proof.yml`
- Modify: `agmind/deploy/target_checks.py`

- [x] **Step 1: Update workflow**

Add `if: always()` to the `Verify k3s proof bundle` step.

- [x] **Step 2: Update validator**

In `_validate_workflow_target_contract()`, require the verifier command to appear in a workflow step block that also contains `if: always()`.

- [x] **Step 3: Run focused GREEN**

Run:

```bash
.venv/bin/python -m pytest tests/test_kubernetes_dry_run.py::test_ci_has_manual_kubernetes_proof_artifact_workflow tests/test_governance_cmd.py::test_kubernetes_proof_workflow_guard_rejects_non_always_verifier tests/test_kubernetes_dry_run.py::test_kubernetes_proof_workflow_check_script_runs -q
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

- [x] **Step 1: Record M7.D.24.E-prep checkpoint**

Update planning docs to say manual proof now verifies generated bundles under `if: always()` and the drift guard enforces it.

- [x] **Step 2: Run verification**

Run focused pytest, ruff format/check, mypy, workflow drift check, governance check, expanded M7 pytest, `git diff --check`, and full pre-commit.

- [x] **Step 3: Record outputs**

Append final verification outputs to this plan and `.planning/STATE.md`.

## Verification Log

- RED observed: focused pytest failed because the real workflow verifier step
  was missing `if: always()` and the workflow drift guard returned no error for
  a non-always verifier.
- GREEN focused workflow/validator slice:
  `.venv/bin/python -m pytest tests/test_kubernetes_dry_run.py::test_ci_has_manual_kubernetes_proof_artifact_workflow tests/test_governance_cmd.py::test_kubernetes_proof_workflow_guard_rejects_non_always_verifier tests/test_kubernetes_dry_run.py::test_kubernetes_proof_workflow_check_script_runs -q`
  passed 3 tests.
- Focused regression set:
  `.venv/bin/python -m pytest tests/test_kubernetes_dry_run.py::test_ci_has_manual_kubernetes_proof_artifact_workflow tests/test_governance_cmd.py::test_kubernetes_proof_workflow_guard_rejects_non_always_verifier tests/test_kubernetes_dry_run.py::test_kubernetes_proof_workflow_check_script_runs tests/test_governance_cmd.py::test_governance_check_script_runs -q`
  passed 4 tests.
- Static checks: `ruff format --check`, `ruff check`, and mypy passed for the
  touched workflow validator and tests.
- Script gates: `scripts/kubernetes_proof_workflow_check.py` reported
  `kubernetes proof workflow OK: 1 targets`; `scripts/governance_check.py`
  reported `governance OK: 6 checks`.
- Expanded M7 pytest set passed 171 tests.
