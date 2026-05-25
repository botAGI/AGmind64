# Kubernetes Portability Policy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn Kubernetes renderer warnings into actionable policy data with stable codes, severity, and remediation.

**Architecture:** Extend `KubernetesRenderWarning` without changing the manifest object model. The renderer will emit structured warning metadata and keep the existing human-readable comments. The render governance check will aggregate warning severities so M7 can later choose which warnings block k3s promotion.

**Tech Stack:** Python 3.12, dataclasses, existing Kubernetes renderer/check modules, pytest, mypy, pre-commit.

---

## Scope

This is M7.D.3 local policy work. It does not change Kubernetes manifests or
call `kubectl`. It only makes current warnings machine-readable:

- stable warning code;
- severity: `info`, `warning`, or `blocker`;
- remediation text;
- JSON/report severity summary.

## Files

- Modify: `agmind/services/kubernetes_renderer.py`
- Modify: `agmind/services/kubernetes_checks.py`
- Modify: `tests/test_kubernetes_renderer.py`
- Modify: `tests/test_kubernetes_render_check.py`
- Modify: `.planning/BACKLOG.md`, `.planning/STATE.md`

## Tasks

- [x] **Task 1: RED tests for structured warning metadata**
  - Assert device/group/security/cap/env warnings expose stable codes.
  - Assert each warning exposes severity and remediation.

- [x] **Task 2: RED tests for governance severity summary**
  - Assert report JSON includes `warning_summary`.
  - Assert text output includes a compact severity breakdown.

- [x] **Task 3: Implement structured warnings**
  - Add fields to `KubernetesRenderWarning`.
  - Update all warning emission sites.
  - Preserve existing `# WARNING <service>:` comments.

- [x] **Task 4: Implement severity aggregation**
  - Add warning summary fields to target and aggregate reports.
  - Keep default render check non-fatal while strict still fails.

- [x] **Task 5: Documentation and verification**
  - Update planning state/backlog.
  - Run focused tests, governance, diff check, and full pre-commit.

## Observed Verification

Local results, 2026-05-24:

- RED test set failed first because `KubernetesRenderWarning` had no
  `code/severity/remediation`, YAML comments did not include policy markers,
  and render reports had no `warning_summary`.
- Focused Kubernetes renderer/check tests passed: 17 tests.
- Focused ruff, format, and mypy passed for the renderer/check modules.
- `scripts/kubernetes_render_check.py --json` now reports
  `warning_summary: {info: 0, warning: 22, blocker: 5}` for the current `k3s`
  research target.
- `scripts/kubernetes_render_check.py` passed with text output showing the same
  severity split.
- `scripts/governance_check.py` passed with five checks and the structured
  Kubernetes render warning summary.
- Expanded M7-focused pytest passed: 151 tests.
- `git diff --check` passed.
- Full `pre-commit run --all-files --show-diff-on-failure` passed.

Remaining external proof:

- real `kubectl apply --dry-run=server` against k3s;
- deciding which blocker codes are acceptable only for research and which must
  fail experimental/support targets;
- mapping device, Docker socket, storage, secrets, and ingress policies into
  Kubernetes-native resources.
