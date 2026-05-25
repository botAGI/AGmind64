# Kubernetes Proof Workflow Drift Guard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a governance guard that keeps the manual Kubernetes proof workflow aligned with the k3s deployment target proof contract.

**Architecture:** Reuse deployment target loading as the source of truth. Add a focused validator that reads `.github/workflows/kubernetes-proof.yml` as text, extracts the target-declared proof artifact directory from `scripts/kubernetes_dry_run.py --require-cluster`, and verifies the workflow is manual-only, k3s-runner scoped, uses the self-hosted install pattern, runs strict render validation, runs/verifies the same proof directory, and uploads all declared proof artifacts. Wire the validator into script, pre-commit, aggregate governance, and CI.

**Tech Stack:** Python 3.12, deployment target contracts, `shlex`, pytest, pre-commit, GitHub Actions YAML.

---

## Scope

This is M7.D.24.C-prep. It does not execute a real cluster proof. It prevents
the manual proof workflow from drifting away from the target contract that
operators and reviewers rely on.

Rules:

- `.github/workflows/kubernetes-proof.yml` must exist.
- The workflow must be `workflow_dispatch` only: no `push` or `pull_request`
  triggers.
- The workflow must run on `[self-hosted, linux, x64, k3s]`.
- The workflow must not use `actions/setup-python`.
- The workflow must run strict Kubernetes render validation.
- For every Kubernetes target proof command with `--require-cluster`, the
  workflow must include the same target id, artifact directory, matching
  `--verify-artifact-dir`, and all `verification.artifacts` paths.
- The new check is visible through `scripts/kubernetes_proof_workflow_check.py`,
  aggregate governance, pre-commit, and self-hosted CI.

## Files

- Modify: `agmind/deploy/target_checks.py`
- Create: `scripts/kubernetes_proof_workflow_check.py`
- Modify: `agmind/governance.py`
- Modify: `.pre-commit-config.yaml`
- Modify: `.github/workflows/ci.yml`
- Modify: `tests/test_kubernetes_dry_run.py`
- Modify: `tests/test_governance_cmd.py`
- Modify: `.planning/STATE.md`, `.planning/ROADMAP.md`, `.planning/BACKLOG.md`,
  `.planning/codebase/ARCHITECTURE.md`, `.planning/codebase/INDEX.md`

## Tasks

- [x] **Task 1: RED tests**
  - Add a focused test proving `scripts/kubernetes_proof_workflow_check.py`
    runs and reports OK.
  - Update governance tests to expect the new `kubernetes-proof-workflow`
    aggregate check, pre-commit hook, and self-hosted CI job.
  - Run focused tests and observe failures because the validator and wiring do
    not exist.

- [x] **Task 2: Implement drift guard**
  - Add `validate_kubernetes_proof_workflow()` to deployment target checks.
  - Add the script wrapper.
  - Add governance check registration and formatting count update.
  - Add pre-commit hook and self-hosted CI job.

- [x] **Task 3: Planning and verification**
  - Update `.planning` and this plan with the M7.D.24.C-prep checkpoint.
  - Run focused tests, ruff/mypy, proof workflow check, governance, expanded
    M7 pytest, `git diff --check`, and full pre-commit.

## Progress

- Selected this step because M7.D.24.B-prep added the manual workflow, but no
  local gate prevented later edits from changing the workflow while leaving the
  `k3s` deployment target contract stale.
- RED observed: focused tests failed because
  `scripts/kubernetes_proof_workflow_check.py` did not exist, aggregate
  governance still reported five checks, and pre-commit/CI had no workflow
  guard wiring.
- GREEN observed: focused tests passed 7 tests after adding the workflow
  validator, script wrapper, aggregate governance registration, pre-commit
  coverage, and self-hosted CI job.
- Final verification observed on 2026-05-24: focused workflow/governance tests
  passed 9 tests; focused ruff format check, ruff check, and mypy passed;
  `scripts/kubernetes_proof_workflow_check.py` passed; aggregate governance
  passed with 6 checks; the expanded M7 pytest set passed 196 tests; `git diff
  --check` and full `pre-commit --all-files --show-diff-on-failure` passed.
