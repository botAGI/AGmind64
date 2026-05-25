# Kubernetes Dry-Run Artifacts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Kubernetes server dry-run proof runs save reusable YAML and JSON evidence artifacts.

**Architecture:** Extend the existing M7.D.6 dry-run harness with an optional artifact directory. The harness should write the rendered target manifest to a stable path, run kubectl against that path, and emit both per-target and summary JSON reports. Default behavior remains unchanged when no artifact directory is requested.

**Tech Stack:** Python 3.12, dataclasses, pathlib, json, existing `agmind.services.kubernetes_dry_run`, pytest, ruff, mypy, pre-commit.

---

## Scope

This is local evidence plumbing for M7.D.7. It does not require a real
kubeconfig and does not promote the k3s target. It prepares the exact artifact
shape a real k3s proof run should leave behind:

- `<artifact-dir>/<target>.yaml` rendered manifest used by kubectl;
- `<artifact-dir>/<target>.dry-run.json` per-target report;
- `<artifact-dir>/summary.json` aggregate report.

## Files

- Modify: `agmind/services/kubernetes_dry_run.py`
- Modify: `tests/test_kubernetes_dry_run.py`
- Modify: `docs/superpowers/plans/2026-05-24-kubernetes-server-dry-run-proof.md`
- Modify: `.planning/STATE.md`, `.planning/ROADMAP.md`, `.planning/BACKLOG.md`

## Tasks

- [x] **Task 1: RED tests for artifact directory**
  - Add a focused test using an injected runner and `tmp_path`.
  - Assert `<target>.yaml`, `<target>.dry-run.json`, and `summary.json` exist.
  - Assert the kubectl command uses the persisted manifest path.
  - Observed RED: focused pytest failed because `artifact_dir` was not accepted
    and `--artifact-dir` was not a CLI option.

- [x] **Task 2: Implement artifact writing**
  - Add `artifact_dir: Path | None` to `run_kubernetes_server_dry_run`.
  - Persist manifests when `artifact_dir` is provided.
  - Persist per-target and summary JSON reports.
  - Keep temporary-file behavior unchanged when no artifact directory is provided.
  - Implemented manifest persistence before kubectl availability checks so
    skipped runs still leave the rendered YAML evidence.

- [x] **Task 3: Add CLI option**
  - Add `--artifact-dir` to `scripts/kubernetes_dry_run.py` through module `main`.
  - Include artifact paths in JSON reports so CI logs point at evidence files.
  - JSON output now includes `manifest_path` and `report_path`.

- [x] **Task 4: Planning and verification**
  - Update GSD planning with the artifact convention.
  - Run focused tests, lint/type checks, dry-run script with an artifact dir,
    expanded M7 pytest, governance, `git diff --check`, and full pre-commit.
  - Verification passed:
    - `.venv/bin/pytest tests/test_kubernetes_dry_run.py tests/test_deploy_targets.py -q` — 18 passed.
    - Sequential artifact smoke wrote `k3s.yaml`, `k3s.dry-run.json`, and
      `summary.json`.
    - `.venv/bin/python scripts/governance_check.py` — governance OK, 5 checks.
    - Expanded M7 pytest slice — 161 passed.
    - Focused ruff, format check, and mypy passed.
    - `git diff --check` — clean.
    - `.venv/bin/pre-commit run --all-files --show-diff-on-failure` — passed.
