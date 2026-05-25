# Kubernetes AMD GPU Proof Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extend the Kubernetes server dry-run harness so real k3s proof runs can require allocatable `amd.com/gpu` evidence before promotion.

**Architecture:** Keep the renderer and deployment target contracts unchanged. Add an optional AMD GPU preflight to `agmind.services.kubernetes_dry_run`: when `--require-amd-gpu` is used, run `kubectl get nodes -o json`, sum `status.allocatable["amd.com/gpu"]`, include that result in text/JSON/artifacts, and fail the proof if no allocatable GPU is present. The existing local no-cluster behavior stays safe and skipped unless proof is explicitly required.

**Tech Stack:** Python 3.12, kubectl JSON output, existing Kubernetes dry-run service, pytest, ruff, mypy, pre-commit.

---

## Scope

This is M7.D.11 local harness work. It does not install the AMD GPU device
plugin, create a GPU Operator manifest, or require a local cluster in dev. It
only makes the real-cluster proof command encode the new M7.D.10 prerequisite:
the node must expose allocatable `amd.com/gpu`.

Expected behavior:

- default `scripts/kubernetes_dry_run.py` behavior remains unchanged;
- `--require-amd-gpu` runs an extra `kubectl get nodes -o json` preflight;
- JSON target reports include a `gpu_preflight` object;
- artifact target reports persist the same `gpu_preflight` object;
- missing kubectl or cluster access is skipped unless `--require-cluster` is
  also used, matching existing proof semantics;
- a real cluster with zero allocatable `amd.com/gpu` makes the target fail.

## Files

- Modify: `agmind/services/kubernetes_dry_run.py`
- Modify: `tests/test_kubernetes_dry_run.py`
- Modify: `templates/deploy-targets/k3s.yaml`
- Modify: `.planning/STATE.md`, `.planning/ROADMAP.md`, `.planning/BACKLOG.md`,
  `.planning/codebase/ARCHITECTURE.md`, `.planning/codebase/INDEX.md`

## Tasks

- [x] **Task 1: RED tests for AMD GPU preflight pass/fail**
  - Add tests with a fake runner that returns node JSON for
    `kubectl get nodes -o json`.
  - Assert `require_amd_gpu=True` records `allocatable=1`, status `passed`, and
    then runs the existing server dry-run command.
  - Assert zero allocatable `amd.com/gpu` records status `failed`, includes a
    useful stderr message, and prevents the target report from being OK.
  - Run focused dry-run tests and record RED failures for missing
    `require_amd_gpu` support.
  - Observed RED: focused dry-run tests failed because
    `run_kubernetes_server_dry_run()` did not accept `require_amd_gpu` and the
    CLI rejected `--require-amd-gpu`.

- [x] **Task 2: RED tests for CLI/artifact shape**
  - Add a script/JSON test that invokes the service path with artifact output
    and checks `gpu_preflight` is present in the target JSON.
  - Update the k3s deployment target verification commands to include
    `scripts/kubernetes_dry_run.py --require-cluster --require-amd-gpu`.
  - Run focused dry-run/deploy target tests and record RED failures.
  - Observed RED: `templates/deploy-targets/k3s.yaml` still advertised the old
    proof command without `--require-amd-gpu`.

- [x] **Task 3: Implement AMD GPU proof preflight**
  - Add `KubernetesGpuPreflightReport`.
  - Add `require_amd_gpu` parameter to `run_kubernetes_server_dry_run()` and
    `--require-amd-gpu` to CLI args.
  - Build `kubectl get nodes -o json` with optional context.
  - Parse `status.allocatable["amd.com/gpu"]` and tolerate integer or string
    quantities.
  - Fail the target when the preflight runs successfully but reports zero
    allocatable GPUs.
  - Keep missing kubectl/cluster access as skipped unless `require_cluster`
    makes skipped evidence fail.
  - Implemented in `agmind.services.kubernetes_dry_run`; focused dry-run and
    deploy-target tests now pass 22 tests.

- [x] **Task 4: Planning and verification**
  - Update `.planning` with the M7.D.11 checkpoint and real-cluster command.
  - Run focused dry-run/deploy target tests, focused ruff/format/mypy, render
    governance, expanded M7 pytest, `git diff --check`, and full pre-commit.
  - Verified focused dry-run/deploy-target tests: 22 passed.
  - Verified focused ruff format check, ruff check, and mypy.
  - Verified local no-kubectl smoke:
    `scripts/kubernetes_dry_run.py --json --require-amd-gpu --kubectl /definitely/missing/kubectl`
    reported skipped `gpu_preflight` evidence.
  - Verified render governance: k3s renders 36 objects, 26 warnings, and 0
    blockers.
  - Verified aggregate governance: 5 checks passed.
  - Verified expanded M7 pytest slice: 169 passed.
  - Verified `git diff --check` and full
    `pre-commit run --all-files --show-diff-on-failure`.
