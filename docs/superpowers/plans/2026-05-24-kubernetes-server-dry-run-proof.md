# Kubernetes Server Dry-Run Proof Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an operator-facing proof harness for real `kubectl apply --dry-run=server` validation of AGmind Kubernetes deployment targets.

**Architecture:** Keep local render governance separate from cluster evidence. Add a focused dry-run module that renders Kubernetes targets, invokes `kubectl` through an injectable runner, and reports `passed`, `failed`, or `skipped` without requiring a kubeconfig in ordinary local CI. The script returns non-zero only for real dry-run failures or when `--require-cluster` turns a skipped cluster check into a blocker.

**Tech Stack:** Python 3.12, dataclasses, subprocess, existing deployment target and Kubernetes renderer/check modules, pytest, ruff, mypy, pre-commit.

---

## Scope

This is M7.D.6 local harness work. It does not promote `k3s` out of research,
does not add Helm/Kustomize, and does not hide current portability blockers.
It makes the external proof path explicit and machine-readable:

- render each Kubernetes target from its declared profiles;
- run `kubectl apply --dry-run=server -f <rendered-file>` when kubectl is available;
- report skipped evidence cleanly when the host has no kubectl/cluster;
- fail hard when `--require-cluster` is used and evidence cannot be collected.

## Files

- Create: `agmind/services/kubernetes_dry_run.py`
- Create: `scripts/kubernetes_dry_run.py`
- Create: `tests/test_kubernetes_dry_run.py`
- Modify: `templates/deploy-targets/k3s.yaml`
- Modify: `.planning/BACKLOG.md`, `.planning/STATE.md`, `.planning/ROADMAP.md`

## Tasks

- [x] **Task 1: RED tests for dry-run reports**
  - Add tests for a passed dry-run using an injected runner.
  - Assert the runner receives `kubectl apply --dry-run=server -f <manifest>`.
  - Assert JSON includes target id, status, command, warning summary, stdout, and stderr.
  - Observed RED: focused pytest failed because `agmind.services.kubernetes_dry_run`
    did not exist.

- [x] **Task 2: RED tests for skipped and failed cluster evidence**
  - Add a test for missing kubectl returning `skipped` in default mode.
  - Add a test for `require_cluster=True` making missing kubectl fail the report.
  - Add a test for non-zero kubectl return code producing `failed`.
  - Observed RED: focused pytest failed because `scripts/kubernetes_dry_run.py`
    did not exist.

- [x] **Task 3: Implement the dry-run module and script**
  - Add dataclasses for per-target and aggregate reports.
  - Render target manifests through the existing Kubernetes renderer.
  - Use an injectable command runner for tests and `subprocess.run` in production.
  - Add text and JSON formatters plus `main(argv)`.
  - Implemented in `agmind.services.kubernetes_dry_run` and
    `scripts/kubernetes_dry_run.py`.

- [x] **Task 4: Wire deploy target verification**
  - Update `templates/deploy-targets/k3s.yaml` verification commands to include
    `scripts/kubernetes_dry_run.py --require-cluster`.
  - Keep the raw render command visible for manual artifact inspection.
  - `tests/test_deploy_targets.py` now asserts the k3s target references the
    proof harness.

- [x] **Task 5: Planning and verification**
  - Update `.planning` with the M7.D.6 checkpoint.
  - Run focused tests, lint/type checks, Kubernetes dry-run script in default
    non-cluster mode, governance, expanded M7 pytest, `git diff --check`, and
    full pre-commit.
  - Verification passed:
    - `.venv/bin/pytest tests/test_kubernetes_dry_run.py tests/test_deploy_targets.py -q` — 16 passed.
    - `.venv/bin/python scripts/kubernetes_dry_run.py --json` — `k3s` reports `skipped` without local kubectl.
    - `.venv/bin/python scripts/kubernetes_dry_run.py --require-cluster --kubectl /definitely/missing/kubectl` — exits 1 as expected.
    - Follow-up artifact capture plan added:
      `docs/superpowers/plans/2026-05-24-kubernetes-dry-run-artifacts.md`.
    - `.venv/bin/python scripts/deploy_target_check.py` — deployment targets OK.
    - `.venv/bin/python scripts/governance_check.py` — governance OK, 5 checks.
    - Expanded M7 pytest slice — 159 passed.
    - Focused ruff, format check, and mypy passed.
    - `git diff --check` — clean.
    - `.venv/bin/pre-commit run --all-files --show-diff-on-failure` — passed.
