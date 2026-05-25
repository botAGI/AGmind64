# Kubernetes Proof Bundle Diagnostics Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make manual Kubernetes proof workflow logs show which proof bundle files were produced before artifact upload.

**Architecture:** Keep artifact generation unchanged. Add an `if: always()` diagnostic step between bundle verification and upload that lists files in the target-declared artifact directory and prints `checksums.txt` when present. Extend the existing workflow drift guard so this diagnostic step cannot drift out of the manual proof workflow.

**Tech Stack:** GitHub Actions shell steps, Python workflow contract validation, pytest.

---

### Task 1: RED Tests

**Files:**
- Modify: `tests/test_kubernetes_dry_run.py`
- Modify: `tests/test_governance_cmd.py`

- [x] **Step 1: Assert workflow has always-run bundle diagnostics**

Update the manual workflow test to require:

```yaml
- name: Summarize k3s proof bundle
  if: always()
```

and command fragments:

```bash
find local-kubernetes-proof/k3s -maxdepth 1 -type f -print | sort
cat local-kubernetes-proof/k3s/checksums.txt
```

- [x] **Step 2: Assert drift guard rejects missing diagnostics**

Add a test that removes the summary step from a temporary copy of
`.github/workflows/kubernetes-proof.yml` and expects
`validate_kubernetes_proof_workflow()` to return:

```text
k3s: Kubernetes proof workflow must summarize proof bundle contents with if: always()
```

- [x] **Step 3: Run RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_kubernetes_dry_run.py::test_ci_has_manual_kubernetes_proof_artifact_workflow tests/test_governance_cmd.py::test_kubernetes_proof_workflow_guard_rejects_missing_bundle_diagnostics -q
```

Expected: fail because the summary step and validator rule do not exist yet.

### Task 2: Workflow and Validator

**Files:**
- Modify: `.github/workflows/kubernetes-proof.yml`
- Modify: `agmind/deploy/target_checks.py`

- [x] **Step 1: Add workflow diagnostic step**

Insert this step after verification and before upload:

```yaml
- name: Summarize k3s proof bundle
  if: always()
  run: |
    set -euo pipefail
    if [ ! -d local-kubernetes-proof/k3s ]; then
      echo "k3s proof bundle directory is missing"
      exit 0
    fi
    find local-kubernetes-proof/k3s -maxdepth 1 -type f -print | sort
    if [ -f local-kubernetes-proof/k3s/checksums.txt ]; then
      echo "checksums.txt:"
      cat local-kubernetes-proof/k3s/checksums.txt
    fi
```

- [x] **Step 2: Extend workflow drift guard**

Teach `_validate_workflow_target_contract()` to require an always-run workflow step containing both the `find <artifact_dir> ... | sort` command and `cat <artifact_dir>/checksums.txt`.

- [x] **Step 3: Run focused GREEN**

Run:

```bash
.venv/bin/python -m pytest tests/test_kubernetes_dry_run.py::test_ci_has_manual_kubernetes_proof_artifact_workflow tests/test_governance_cmd.py::test_kubernetes_proof_workflow_guard_rejects_missing_bundle_diagnostics tests/test_kubernetes_dry_run.py::test_kubernetes_proof_workflow_check_script_runs -q
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

- [x] **Step 1: Record M7.D.24.F-prep checkpoint**

Update planning docs to say the manual workflow now prints proof bundle file and checksum diagnostics before upload, and the drift guard enforces the step.

- [x] **Step 2: Run verification**

Run focused pytest, ruff format/check, mypy, workflow drift check, governance check, expanded M7 pytest, `git diff --check`, and full pre-commit.

- [x] **Step 3: Record outputs**

Append final verification outputs to this plan and `.planning/STATE.md`.

## Verification Log

- RED observed: focused pytest failed because the manual workflow did not have
  a `Summarize k3s proof bundle` step and the workflow drift guard returned no
  error when that diagnostic step was absent.
- GREEN focused workflow/validator slice:
  `.venv/bin/python -m pytest tests/test_kubernetes_dry_run.py::test_ci_has_manual_kubernetes_proof_artifact_workflow tests/test_governance_cmd.py::test_kubernetes_proof_workflow_guard_rejects_missing_bundle_diagnostics tests/test_kubernetes_dry_run.py::test_kubernetes_proof_workflow_check_script_runs -q`
  passed 3 tests.
- Focused regression set:
  `.venv/bin/python -m pytest tests/test_kubernetes_dry_run.py::test_ci_has_manual_kubernetes_proof_artifact_workflow tests/test_governance_cmd.py::test_kubernetes_proof_workflow_guard_rejects_missing_bundle_diagnostics tests/test_kubernetes_dry_run.py::test_kubernetes_proof_workflow_check_script_runs tests/test_governance_cmd.py::test_governance_check_script_runs -q`
  passed 4 tests.
- Static checks: `ruff format --check`, `ruff check`, and mypy passed for the
  touched workflow validator and tests after formatting
  `tests/test_governance_cmd.py`.
- Script gates: `scripts/kubernetes_proof_workflow_check.py` reported
  `kubernetes proof workflow OK: 1 targets`; `scripts/governance_check.py`
  reported `governance OK: 6 checks`.
- Expanded M7 pytest set passed 172 tests.
