# Kubernetes Proof CI Artifacts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a manual self-hosted GitHub Actions path that runs the real k3s proof command, verifies the bundle, and uploads the evidence artifacts.

**Architecture:** Keep ordinary push/PR CI independent from live Kubernetes access. Add a separate `workflow_dispatch` workflow that runs only on self-hosted runners labeled `k3s`, installs AGmind through the existing `uv` pattern, runs strict render validation, runs the contract-defined k3s dry-run proof into `local-kubernetes-proof/k3s`, verifies the bundle, and uploads the declared files.

**Tech Stack:** GitHub Actions YAML, self-hosted runner labels, existing `scripts/kubernetes_dry_run.py`, pytest, ruff/pre-commit.

---

## Scope

This is M7.D.24.B-prep. It does not provide real cluster evidence in this local
session. It creates the repeatable workflow that a kubeconfig-equipped
self-hosted runner can execute.

Rules:

- The workflow is manual (`workflow_dispatch`) only.
- The job runs on `[self-hosted, linux, x64, k3s]`.
- The workflow uses the existing local CI dependency pattern: system Python and
  `$HOME/.local/bin/uv`, no `actions/setup-python`.
- The workflow runs `scripts/kubernetes_render_check.py --strict` before the
  real proof command.
- The proof command writes `local-kubernetes-proof/k3s`.
- The workflow runs `scripts/kubernetes_dry_run.py --verify-artifact-dir
  local-kubernetes-proof/k3s`.
- The upload step includes exactly the four declared proof artifacts:
  `k3s.yaml`, `k3s.dry-run.json`, `summary.json`, and `checksums.txt`.

## Files

- Create: `.github/workflows/kubernetes-proof.yml`
- Modify: `tests/test_kubernetes_dry_run.py`
- Modify: `.planning/STATE.md`, `.planning/ROADMAP.md`, `.planning/BACKLOG.md`,
  `.planning/codebase/ARCHITECTURE.md`, `.planning/codebase/DEPENDENCIES.md`,
  `.planning/codebase/INDEX.md`

## Tasks

- [x] **Task 1: RED workflow test**
  - Add a test that reads `.github/workflows/kubernetes-proof.yml` and asserts
    the workflow is manual-only, runs on the k3s self-hosted label, executes
    strict render validation, runs the exact k3s proof command, verifies the
    bundle, and uploads the declared artifacts.
  - Run the focused test and observe failure because the workflow is missing.

- [x] **Task 2: Add manual k3s proof workflow**
  - Create `.github/workflows/kubernetes-proof.yml`.
  - Use existing `uv` install steps.
  - Add optional workflow inputs for `kubectl`, `kube_context`, and
    `namespace`.
  - Run strict render validation, proof, verifier, and artifact upload.

- [x] **Task 3: Planning and verification**
  - Update `.planning` and this plan with the M7.D.24.B-prep checkpoint.
  - Run focused workflow/dry-run tests, YAML validation through pre-commit,
    governance, expanded M7 pytest, `git diff --check`, and full pre-commit.

## Progress

- Selected this step because M7.D.24.A defines the proof bundle contract, but
  the repository still lacks a repeatable self-hosted workflow for producing
  and uploading the real k3s evidence.
- RED observed: the focused workflow test failed because
  `.github/workflows/kubernetes-proof.yml` did not exist.
- GREEN observed: after adding the manual workflow, the focused workflow test
  passed and the full Kubernetes dry-run test file passed 24 tests.
- Final verification observed on 2026-05-24: focused ruff format check and
  ruff check passed; Kubernetes dry-run tests passed 24 tests; aggregate
  governance passed; the expanded M7 pytest set passed 195 tests;
  `git diff --check` and full `pre-commit --all-files --show-diff-on-failure`
  passed.
