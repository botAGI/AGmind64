# Kubernetes Proof Command Artifact Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Kubernetes proof bundles self-describing by writing and verifying a `proof-command.txt` artifact.

**Architecture:** Extend the existing dry-run harness instead of adding a separate sidecar script. `run_kubernetes_server_dry_run()` already knows target ids, cluster requirements, namespace, kubectl, context, and artifact directory, so it will render a shell-quoted command into `proof-command.txt`, expose its path in `summary.json`, include it in `checksums.txt`, and let the existing verifier detect corruption. The k3s deploy target contract and manual workflow will declare and upload the extra artifact.

**Tech Stack:** Python dataclasses, pytest, existing AGmind deployment target validators, GitHub Actions workflow YAML.

---

### Task 1: Proof Command Tests

**Files:**
- Modify: `tests/test_kubernetes_dry_run.py`
- Modify: `tests/test_deploy_targets.py`

- [x] **Step 1: Write failing dry-run artifact test**

Add a pytest case that runs `run_kubernetes_server_dry_run()` with `artifact_dir`, `--target`, `--require-cluster`, `--require-amd-gpu`, namespace, kubectl, and context metadata. Assert that `proof-command.txt` exists, contains the equivalent `scripts/kubernetes_dry_run.py` command, that `summary.json` records `proof_command_path`, and that `checksums.txt` contains the file digest.

- [x] **Step 2: Write failing verifier test**

Add a pytest case that builds a valid artifact bundle, corrupts `proof-command.txt`, and expects `verify_kubernetes_dry_run_artifacts()` to reject it through the existing checksum path.

- [x] **Step 3: Update deploy-target/workflow expectations**

Update repository contract tests so k3s declares and the manual workflow uploads `local-kubernetes-proof/k3s/proof-command.txt`.

- [x] **Step 4: Run RED**

Run:

```bash
.venv/bin/python -m pytest tests/test_kubernetes_dry_run.py::test_kubernetes_dry_run_artifact_records_proof_command tests/test_kubernetes_dry_run.py::test_kubernetes_dry_run_artifact_verifier_rejects_corrupt_proof_command tests/test_deploy_targets.py::test_repository_k3s_declares_real_proof_artifact_bundle -q
```

Expected: fail because `proof-command.txt` is not written or declared yet.

### Task 2: Harness Implementation

**Files:**
- Modify: `agmind/services/kubernetes_dry_run.py`

- [x] **Step 1: Add report fields**

Add `proof_command: tuple[str, ...] = ()` to `KubernetesDryRunReport` and include `proof_command` plus `proof_command_path` in `to_json()` when `artifact_dir` is set.

- [x] **Step 2: Build command from invocation metadata**

Add a helper that produces the equivalent script command:

```python
("scripts/kubernetes_dry_run.py", "--target", target_id, "--require-cluster", "--require-amd-gpu", "--artifact-dir", str(artifact_dir), "--namespace", namespace, "--kubectl", kubectl, "--context", kube_context)
```

Only include repeated `--target` entries for selected targets and include flags only when enabled.

- [x] **Step 3: Write proof-command.txt**

When `artifact_dir` is present, write `proof-command.txt` using `shlex.join(report.proof_command)` and add the file to `_write_artifact_checksums()`.

- [x] **Step 4: Run focused GREEN**

Run:

```bash
.venv/bin/python -m pytest tests/test_kubernetes_dry_run.py::test_kubernetes_dry_run_artifact_records_proof_command tests/test_kubernetes_dry_run.py::test_kubernetes_dry_run_artifact_verifier_rejects_corrupt_proof_command tests/test_kubernetes_dry_run.py::test_kubernetes_dry_run_artifact_verifier_accepts_valid_bundle -q
```

Expected: all pass.

### Task 3: Contract and Workflow Wiring

**Files:**
- Modify: `agmind/deploy/target_checks.py`
- Modify: `templates/deploy-targets/k3s.yaml`
- Modify: `.github/workflows/kubernetes-proof.yml`
- Modify: `tests/test_deploy_targets.py`
- Modify: `tests/test_kubernetes_dry_run.py`

- [x] **Step 1: Extend expected artifacts**

Update `_expected_kubernetes_proof_artifacts()` to include `<artifact_dir>/proof-command.txt`.

- [x] **Step 2: Update k3s target contract**

Add `local-kubernetes-proof/k3s/proof-command.txt` to `verification.artifacts`.

- [x] **Step 3: Update workflow upload**

Add the same path to the `actions/upload-artifact@v4` path list.

- [x] **Step 4: Run focused contract tests**

Run:

```bash
.venv/bin/python -m pytest tests/test_deploy_targets.py tests/test_kubernetes_dry_run.py::test_ci_has_manual_kubernetes_proof_artifact_workflow tests/test_kubernetes_dry_run.py::test_kubernetes_proof_workflow_check_script_runs -q
```

Expected: all pass.

### Task 4: Documentation and Verification

**Files:**
- Modify: `.planning/STATE.md`
- Modify: `.planning/ROADMAP.md`
- Modify: `.planning/BACKLOG.md`
- Modify: `.planning/codebase/ARCHITECTURE.md`
- Modify: `.planning/codebase/INDEX.md`
- Modify: this plan file

- [x] **Step 1: Record M7.D.24.D-prep checkpoint**

Update planning docs to say proof bundles now include `proof-command.txt`, `summary.json` records its path, checksums verify it, and the manual workflow uploads it.

- [x] **Step 2: Run verification**

Run focused pytest, ruff format/check, mypy, workflow drift check, governance check, expanded M7 pytest, `git diff --check`, and pre-commit.

- [x] **Step 3: Record outputs**

Append the final verification outputs to this plan and the planning state.

## Verification Log

- RED observed: focused pytest failed for the missing `proof-command.txt`, the
  verifier not catching a corrupt command artifact, and the missing k3s
  deployment target declaration.
- GREEN focused artifact slice:
  `.venv/bin/python -m pytest tests/test_kubernetes_dry_run.py::test_kubernetes_dry_run_artifact_records_proof_command tests/test_kubernetes_dry_run.py::test_kubernetes_dry_run_artifact_verifier_rejects_corrupt_proof_command tests/test_kubernetes_dry_run.py::test_kubernetes_dry_run_artifact_verifier_accepts_valid_bundle tests/test_deploy_targets.py::test_repository_k3s_declares_real_proof_artifact_bundle -q`
  passed 4 tests.
- Contract/workflow focused tests:
  `.venv/bin/python -m pytest tests/test_deploy_targets.py tests/test_kubernetes_dry_run.py::test_ci_has_manual_kubernetes_proof_artifact_workflow tests/test_kubernetes_dry_run.py::test_kubernetes_proof_workflow_check_script_runs -q`
  passed 17 tests.
- Full Kubernetes dry-run tests:
  `.venv/bin/python -m pytest tests/test_kubernetes_dry_run.py -q` passed 27
  tests.
- Focused governance/contract regression set:
  `.venv/bin/python -m pytest tests/test_kubernetes_dry_run.py tests/test_deploy_targets.py tests/test_governance_cmd.py tests/test_kubernetes_render_check.py::test_ci_runs_kubernetes_render_gate tests/test_cli.py::test_governance_validate_command -q`
  passed 50 tests.
- Static checks: `ruff format --check`, `ruff check`, and mypy passed for the
  touched Kubernetes proof modules and tests.
- Script gates: `scripts/kubernetes_proof_workflow_check.py` reported
  `kubernetes proof workflow OK: 1 targets`; `scripts/governance_check.py`
  reported `governance OK: 6 checks`.
- CLI smoke: generated `/tmp/agmind-proof-command-smoke`,
  `proof-command.txt` was checksum-covered, and
  `scripts/kubernetes_dry_run.py --verify-artifact-dir
  /tmp/agmind-proof-command-smoke` reported
  `kubernetes dry-run artifact bundle OK: 4 files`.
- Expanded M7 pytest set passed 170 tests.
- `git diff --check` passed.
- Full `.venv/bin/pre-commit run --all-files --show-diff-on-failure` passed.
