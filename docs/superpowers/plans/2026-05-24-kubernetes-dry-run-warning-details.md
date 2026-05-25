# Kubernetes Dry-Run Warning Details Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Include actionable Kubernetes render warning records in dry-run evidence bundles before the real k3s proof run.

**Architecture:** Keep `kubernetes_render_check.py` as the source of render warning policy. Teach the dry-run harness to carry the same warning records alongside `warning_summary`, enriched with whether each warning code is expected by the deployment target. This keeps `summary.json` and `<target>.dry-run.json` self-contained for review without requiring a separate render-check JSON artifact.

**Tech Stack:** Python 3.12, existing Kubernetes renderer/check/dry-run modules, pytest, ruff, mypy, pre-commit.

---

## Scope

This is local M7.D.21 readiness work. It does not invoke a real cluster; it
improves the reviewability of future `--target k3s --require-cluster
--require-amd-gpu` evidence.

Rules:

- Target dry-run JSON includes `warnings`, not only `warning_summary`.
- Each warning record includes `service`, `code`, `severity`, `message`,
  `remediation`, and `expected`.
- `expected` is derived from
  `DeploymentVerification.expected_warning_codes`.
- Existing summary counts and status behavior stay unchanged.
- Local skipped no-kubectl runs still write warning details.

## Files

- Modify: `agmind/services/kubernetes_dry_run.py`
- Modify: `tests/test_kubernetes_dry_run.py`
- Modify: `.planning/STATE.md`, `.planning/ROADMAP.md`, `.planning/BACKLOG.md`,
  `.planning/codebase/ARCHITECTURE.md`, `.planning/codebase/INDEX.md`

## Tasks

- [x] **Task 1: RED warning-detail tests**
  - Add tests proving dry-run report JSON includes a `warnings` list.
  - Add an artifact smoke proving k3s skipped evidence records the current
    warning details and marks target-declared warning codes as expected.
  - Run focused dry-run tests and observe failures for missing warning records.

- [x] **Task 2: Implement warning details**
  - Extend `KubernetesDryRunTargetReport` with warning records.
  - Replace the summary-only helper with one render-check helper that returns
    both summary and warning records.
  - Enrich records with `expected` using the deployment target verification
    policy.

- [x] **Task 3: Planning and verification**
  - Update `.planning` and this plan with the M7.D.21 checkpoint.
  - Run focused dry-run tests, focused ruff/format/mypy, skipped k3s artifact
    smoke, aggregate governance, expanded M7 pytest, `git diff --check`, and
    full pre-commit.

## Progress

- Selected this step because the dry-run proof bundle already records target
  selection and invocation metadata, but reviewers still need to cross-reference
  render-check JSON to see exact warning codes and remediation text.
- RED observed: focused Kubernetes dry-run tests failed because target dry-run
  JSON did not include the `warnings` field.
- GREEN observed: focused Kubernetes dry-run tests passed 18 tests; focused
  ruff format check, ruff check, and mypy passed; local skipped
  `--target k3s --artifact-dir` smoke wrote four warning records with
  `expected=true`.
- Fresh verification before final docs gate: focused Kubernetes dry-run tests
  passed 18 tests; focused ruff format check, ruff check, and mypy passed;
  local skipped `--target k3s --artifact-dir` smoke wrote four warning records;
  aggregate governance passed 5 checks; strict Kubernetes render-check passed
  at 34 objects, 4 warnings, and 0 blockers; expanded M7-focused pytest passed
  185 tests.
- Final docs gate observed: `git diff --check` passed with no output and full
  `pre-commit run --all-files --show-diff-on-failure` passed after code,
  planning, and codebase memory updates.
