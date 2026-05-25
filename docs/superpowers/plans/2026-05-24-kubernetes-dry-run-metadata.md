# Kubernetes Dry-Run Metadata Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Kubernetes dry-run evidence bundles self-describing before the real k3s proof run.

**Architecture:** Keep the existing manifest, per-target report, and summary artifact shape. Extend only the aggregate `KubernetesDryRunReport` metadata so `summary.json` records the invocation context: namespace, kubectl binary, kube context, `require_cluster`, `require_amd_gpu`, artifact directory, and summary path. This helps a future real k3s run be reviewable without reconstructing CLI flags from shell history.

**Tech Stack:** Python 3.12, existing Kubernetes dry-run harness, pytest, ruff, mypy, pre-commit.

---

## Scope

This is local M7.D.19 readiness work because the current environment has no
`kubectl` binary or kubeconfig. The real cluster proof remains external.

Rules:

- `summary.json` includes `kubectl`, `kube_context`, `namespace`,
  `require_cluster`, `require_amd_gpu`, `artifact_dir`, and `summary_path`.
- Existing target-level JSON remains backward-compatible.
- Skipped no-cluster runs still write the same metadata so operators can tell
  exactly why the proof did not run.
- No service renderer behavior changes in this step.

## Files

- Modify: `agmind/services/kubernetes_dry_run.py`
- Modify: `tests/test_kubernetes_dry_run.py`
- Modify: `.planning/STATE.md`, `.planning/ROADMAP.md`, `.planning/BACKLOG.md`,
  `.planning/codebase/ARCHITECTURE.md`, `.planning/codebase/INDEX.md`

## Tasks

- [x] **Task 1: RED metadata tests**
  - Add a dry-run artifact test that checks `summary.json` records namespace,
    kubectl, kube context, cluster/GPU requirements, artifact directory, and
    summary path.
  - Run focused dry-run tests and observe failures for missing metadata fields.

- [x] **Task 2: Implement aggregate metadata**
  - Add aggregate metadata fields to `KubernetesDryRunReport`.
  - Populate them from `run_kubernetes_server_dry_run`.
  - Keep existing target-level report JSON unchanged.

- [x] **Task 3: Planning and verification**
  - Update `.planning` and this plan with the M7.D.19 checkpoint.
  - Run focused dry-run tests, focused ruff/format/mypy, local skipped
    `--require-cluster --require-amd-gpu --artifact-dir` smoke, aggregate
    governance, expanded M7 pytest, `git diff --check`, and full pre-commit.

## Progress

- Real proof attempt in the local environment failed as expected because
  `kubectl` and `~/.kube` are unavailable. The existing harness wrote a skipped
  evidence bundle, so this step improves that bundle before an operator reruns
  it on a real k3s host.
- RED observed: focused Kubernetes dry-run tests failed because aggregate JSON
  lacked `kubectl`, `kube_context`, `namespace`, `require_amd_gpu`,
  `artifact_dir`, and `summary_path`.
- GREEN observed: focused Kubernetes dry-run tests passed 13 tests; focused
  ruff format check, ruff check, and mypy passed; local skipped
  `--require-cluster --require-amd-gpu --artifact-dir` smoke writes the new
  metadata and fails only because `kubectl` is unavailable in this environment.
- Fresh verification before final docs gate: focused dry-run tests passed
  13 tests; focused ruff format check, ruff check, and mypy passed; local
  skipped `--require-cluster --require-amd-gpu --artifact-dir` smoke wrote
  self-describing metadata; aggregate governance passed 5 checks; strict
  Kubernetes render-check passed at 34 objects, 4 warnings, and 0 blockers;
  expanded M7-focused pytest passed 180 tests.
- Final docs gate observed: `git diff --check` passed with no output and full
  `pre-commit run --all-files --show-diff-on-failure` passed after planning
  and codebase memory updates.
