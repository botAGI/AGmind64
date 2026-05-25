# Kubernetes Dry-Run Artifact Checksums Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Kubernetes dry-run evidence bundles integrity-checkable before the real k3s proof run.

**Architecture:** Keep the existing evidence bundle shape and add deterministic artifact metadata. Target reports record the rendered manifest byte size and SHA256 digest. When `--artifact-dir` is used, the harness also writes `checksums.txt` with SHA256 lines for persisted bundle files after `summary.json` is written. The checksum file is outside the JSON self-reference loop, so it can include the final `summary.json` digest without mutating it.

**Tech Stack:** Python 3.12, existing Kubernetes dry-run harness, SHA256 from the standard library, pytest, ruff, mypy, pre-commit.

---

## Scope

This is local M7.D.22 readiness work. It does not call a real cluster; it makes
future real proof artifacts easier to verify and attach to CI/release evidence.

Rules:

- Target dry-run JSON includes `manifest_bytes` and `manifest_sha256` when a
  rendered manifest artifact exists.
- Aggregate `summary.json` records `checksum_path` when `--artifact-dir` is
  used.
- `checksums.txt` includes persisted manifest, per-target report, and
  `summary.json` SHA256 lines with paths relative to `artifact_dir`.
- No checksum line is written for `checksums.txt` itself.
- Existing status, warning, and GPU preflight behavior stays unchanged.

## Files

- Modify: `agmind/services/kubernetes_dry_run.py`
- Modify: `tests/test_kubernetes_dry_run.py`
- Modify: `.planning/STATE.md`, `.planning/ROADMAP.md`, `.planning/BACKLOG.md`,
  `.planning/codebase/ARCHITECTURE.md`, `.planning/codebase/INDEX.md`

## Tasks

- [x] **Task 1: RED checksum tests**
  - Add a focused artifact test proving target reports include manifest digest
    metadata and `summary.json` records `checksum_path`.
  - Assert `checksums.txt` contains SHA256 lines for the manifest, target
    report, and `summary.json`.
  - Run focused dry-run tests and observe failures for missing checksum fields
    and checksum file.

- [x] **Task 2: Implement artifact checksums**
  - Add manifest digest fields to `KubernetesDryRunTargetReport`.
  - Compute manifest bytes/SHA256 after writing the rendered manifest artifact.
  - Add `checksum_path` to aggregate report JSON.
  - Write `checksums.txt` after `summary.json`.

- [x] **Task 3: Planning and verification**
  - Update `.planning` and this plan with the M7.D.22 checkpoint.
  - Run focused dry-run tests, focused ruff/format/mypy, skipped k3s artifact
    smoke, aggregate governance, expanded M7 pytest, `git diff --check`, and
    full pre-commit.

## Progress

- Selected this step because M7.D.19-M7.D.21 made proof bundles
  self-describing, target-scoped, and warning-rich; checksums are the next
  small local improvement before external real-cluster evidence.
- RED observed: focused Kubernetes dry-run tests failed because `checksums.txt`
  was not written and target reports did not include manifest digest metadata.
- GREEN observed: focused Kubernetes dry-run tests passed 19 tests; focused
  ruff format check, ruff check, and mypy passed; local skipped
  `--target k3s --artifact-dir` smoke wrote `checksums.txt`, `checksum_path`,
  `manifest_bytes`, and `manifest_sha256`.
- Fresh verification before final docs gate: focused Kubernetes dry-run tests
  passed 19 tests; focused ruff format check, ruff check, and mypy passed;
  local skipped `--target k3s --artifact-dir` smoke wrote `checksums.txt`;
  aggregate governance passed 5 checks; strict Kubernetes render-check passed
  at 34 objects, 4 warnings, and 0 blockers; expanded M7-focused pytest passed
  186 tests.
- Final docs gate observed: `git diff --check` passed with no output and full
  `pre-commit run --all-files --show-diff-on-failure` passed after code,
  planning, and codebase memory updates.
