# Kubernetes Dry-Run Target Selection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let operators run Kubernetes dry-run proof against an explicit deployment target before the real k3s evidence run.

**Architecture:** Keep the existing dry-run harness and report model. Add a narrow target-selection layer before the Kubernetes target loop, record the selected target ids in aggregate `summary.json`, and expose it through repeatable CLI `--target <id>` flags. Unknown target ids should fail early with a concise CLI error instead of producing a stack trace.

**Tech Stack:** Python 3.12, existing `agmind.services.kubernetes_dry_run`, argparse, pytest, ruff, mypy, pre-commit.

---

## Scope

This is local M7.D.20 readiness work. It does not require `kubectl` or a real
cluster; it reduces risk for the external k3s proof and future RKE2/Talos lanes.

Rules:

- Default behavior remains unchanged: no `--target` means all Kubernetes
  deployment targets are considered.
- `--target k3s` limits the run to the `k3s` deployment target.
- Repeatable `--target` flags are accepted for future multi-target proof runs.
- Unknown target ids fail before rendering or invoking kubectl.
- Aggregate `summary.json` records the effective target selection.

## Files

- Modify: `agmind/services/kubernetes_dry_run.py`
- Modify: `tests/test_kubernetes_dry_run.py`
- Modify: `.planning/STATE.md`, `.planning/ROADMAP.md`, `.planning/BACKLOG.md`,
  `.planning/codebase/ARCHITECTURE.md`, `.planning/codebase/INDEX.md`

## Tasks

- [x] **Task 1: RED target-selection tests**
  - Add a unit test proving `run_kubernetes_server_dry_run(..., target_ids=(...))`
    runs only the requested Kubernetes target and records `target_ids`.
  - Add script tests for `--target k3s` and unknown `--target missing`.
  - Run focused dry-run tests and observe failures for the missing API/CLI.

- [x] **Task 2: Implement target selection**
  - Add `target_ids` to `run_kubernetes_server_dry_run` and
    `KubernetesDryRunReport`.
  - Validate unknown target ids before rendering.
  - Add repeatable `--target` to the script CLI and convert validation errors
    into argparse errors.

- [x] **Task 3: Planning and verification**
  - Update `.planning` and this plan with the M7.D.20 checkpoint.
  - Run focused dry-run tests, focused ruff/format/mypy, target-selection smoke,
    aggregate governance, expanded M7 pytest, `git diff --check`, and full
    pre-commit.

## Progress

- Selected this step because real k3s proof is blocked locally by missing
  `kubectl` and kubeconfig, while explicit target selection improves the proof
  harness before the external run.
- RED observed: focused Kubernetes dry-run tests failed because
  `run_kubernetes_server_dry_run` did not accept `target_ids`, the CLI rejected
  `--target`, and unknown target errors were still generic argparse unknown
  argument messages.
- GREEN observed: focused dry-run tests plus the deploy-target command contract
  passed 18 tests; `scripts/deploy_target_check.py` passed after the `k3s`
  verification command was updated to include `--target k3s`.
- Fresh verification before final docs gate: focused ruff format check, ruff
  check, and mypy passed; focused dry-run plus deploy-target contract tests
  passed 18 tests; `--target k3s` CLI smoke wrote a single-target skipped
  bundle with `target_ids: ["k3s"]`; unknown target CLI smoke exited 2 with a
  concise validation error; aggregate governance passed 5 checks; strict
  Kubernetes render-check passed at 34 objects, 4 warnings, and 0 blockers;
  expanded M7-focused pytest passed 184 tests.
- Final docs gate observed: `git diff --check` passed with no output and full
  `pre-commit run --all-files --show-diff-on-failure` passed after code,
  deployment target, and planning updates.
