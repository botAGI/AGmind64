# Kubernetes Render Governance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a local governance gate that proves Kubernetes research targets still render from current service descriptors.

**Architecture:** Keep `agmind render kubernetes` as the manifest generator and add a lightweight validation layer in `agmind.services.kubernetes_checks`. The validator loads deployment targets, finds Kubernetes targets using `agmind render kubernetes`, renders their declared profiles, verifies parseable objects are produced, and reports warnings without failing research targets. A script wrapper joins aggregate governance as the fifth M7 gate.

**Tech Stack:** Python 3.12, PyYAML, existing `DeploymentTarget` loader, existing Kubernetes renderer, pytest, pre-commit, self-hosted GitHub Actions.

---

## Scope

This is M7.D.2. It is not a real cluster smoke and does not call `kubectl`.
It catches local drift:

- `k3s` target points at a renderer that cannot run;
- declared Kubernetes target profiles select no services;
- rendered YAML is malformed or empty;
- strict mode exposes current portability warnings.

## Files

- Create: `agmind/services/kubernetes_checks.py`
- Create: `scripts/kubernetes_render_check.py`
- Create: `tests/test_kubernetes_render_check.py`
- Modify: `agmind/governance.py`
- Modify: `.pre-commit-config.yaml`
- Modify: `.github/workflows/ci.yml`
- Modify: `tests/test_governance_cmd.py`
- Modify: `.planning/BACKLOG.md`, `.planning/STATE.md`

## Tasks

- [x] **Task 1: RED tests for Kubernetes render checks**
  - Add tests for a passing `k3s` local render report.
  - Add tests that strict mode fails while Docker-only warnings remain.
  - Add script smoke test for `scripts/kubernetes_render_check.py`.

- [x] **Task 2: Implement Kubernetes render check module and script**
  - Add report dataclasses.
  - Render every target where `runtime.kind == "kubernetes"` and
    `runtime.renderer == "agmind render kubernetes"`.
  - Treat warnings as non-fatal unless strict mode is requested.
  - Emit text and JSON output.

- [x] **Task 3: Wire aggregate governance**
  - Add `kubernetes-render` to `DEFAULT_CHECKS`.
  - Update governance tests from 4 to 5 checks.
  - Ensure JSON output includes the new check.

- [x] **Task 4: Wire local/CI visibility**
  - Add a pre-commit hook for Kubernetes renderer/check/target drift.
  - Add a self-hosted CI job before aggregate governance.
  - Make aggregate governance depend on the new job.

- [x] **Task 5: Documentation and verification**
  - Update planning state/backlog.
  - Run focused tests, governance, diff check, and full pre-commit.

## Observed Verification

Local results, 2026-05-24:

- RED test set failed first because `agmind.services.kubernetes_checks`,
  `scripts/kubernetes_render_check.py`, pre-commit hook, CI job, and fifth
  governance check did not exist yet.
- Kubernetes render governance slice passed: 15 tests.
- Expanded M7-focused pytest passed: 150 tests.
- `ruff check` and `ruff format --check` passed for the touched modules/tests.
- Focused mypy passed for `agmind/services/kubernetes_checks.py`,
  `agmind/services/kubernetes_renderer.py`, and `agmind/governance.py`.
- `scripts/kubernetes_render_check.py` passed with one `k3s` target, 38
  rendered objects, 23 Deployments, 14 Services, and 27 portability warnings.
- `scripts/governance_check.py` passed with five checks, including
  `kubernetes-render`.
- `git diff --check` passed.
- Full `pre-commit run --all-files --show-diff-on-failure` passed, including
  the new `AGmind Kubernetes render check` hook.

Remaining external proof:

- real `kubectl apply --dry-run=server -f <rendered>` against a k3s cluster;
- deciding which warnings become hard blockers for experimental/support status;
- storage, secrets, ingress, and GPU device-plugin mappings.
