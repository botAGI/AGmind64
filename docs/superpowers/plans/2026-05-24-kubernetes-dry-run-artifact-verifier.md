# Kubernetes Dry-Run Artifact Verifier Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a local verifier for Kubernetes dry-run evidence bundles so copied or uploaded proof artifacts can be checked before review.

**Architecture:** Keep generation and verification in the existing dry-run harness. Add a `verify_kubernetes_dry_run_artifacts()` function that reads `summary.json` and `checksums.txt`, verifies file existence and SHA256 digests, and cross-checks target manifest byte/digest metadata. Expose it through `scripts/kubernetes_dry_run.py --verify-artifact-dir <dir>` with text and JSON output.

**Tech Stack:** Python 3.12, existing Kubernetes dry-run harness, SHA256 from the standard library, pytest, ruff, mypy, pre-commit.

---

## Scope

This is local M7.D.23 readiness work. It does not invoke a cluster; it verifies
artifact bundles produced by previous local or future real k3s proof runs.

Rules:

- `--verify-artifact-dir <dir>` verifies an existing bundle instead of running
  a new dry-run.
- Verification checks `summary.json`, `checksums.txt`, listed files, SHA256
  digests, and per-target `manifest_bytes`/`manifest_sha256`.
- Verification works when the bundle was copied to a new directory by resolving
  artifact basenames relative to the supplied directory.
- Text mode prints an operator-readable OK/FAILED line.
- JSON mode returns a machine-readable report with per-file results and errors.

## Files

- Modify: `agmind/services/kubernetes_dry_run.py`
- Modify: `tests/test_kubernetes_dry_run.py`
- Modify: `.planning/STATE.md`, `.planning/ROADMAP.md`, `.planning/BACKLOG.md`,
  `.planning/codebase/ARCHITECTURE.md`, `.planning/codebase/INDEX.md`

## Tasks

- [x] **Task 1: RED verifier tests**
  - Add tests proving a generated artifact bundle verifies successfully.
  - Add a corruption test proving checksum mismatch fails verification.
  - Add script tests for text and JSON verifier modes.
  - Run focused dry-run tests and observe failures for missing verifier API/CLI.

- [x] **Task 2: Implement artifact verification**
  - Add verification dataclasses and JSON formatting.
  - Parse `checksums.txt` and verify hashes.
  - Cross-check target manifest byte/digest metadata from target reports.
  - Add `--verify-artifact-dir` to the CLI.

- [x] **Task 3: Planning and verification**
  - Update `.planning` and this plan with the M7.D.23 checkpoint.
  - Run focused dry-run tests, focused ruff/format/mypy, verifier smoke,
    aggregate governance, expanded M7 pytest, `git diff --check`, and full
    pre-commit.

## Progress

- Selected this step because M7.D.22 writes checksums, but reviewers still need
  a first-party command to validate copied or CI-uploaded evidence bundles.
- RED observed: focused Kubernetes dry-run tests failed because
  `verify_kubernetes_dry_run_artifacts` was missing and the CLI rejected
  `--verify-artifact-dir`.
- GREEN observed: focused Kubernetes dry-run tests passed 23 tests; focused
  ruff format check, ruff check, and mypy passed; verifier smoke accepted a
  generated bundle and rejected a deliberately corrupted manifest with checksum
  and manifest metadata errors.
- Final verification observed on 2026-05-24: focused dry-run tests passed 23
  tests; governance and strict Kubernetes render checks passed; the expanded
  M7 pytest set passed 190 tests; `git diff --check` and full
  `pre-commit --all-files --show-diff-on-failure` passed.
