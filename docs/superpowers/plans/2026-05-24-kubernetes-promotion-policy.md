# Kubernetes Promotion Policy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent Kubernetes deployment targets from being promoted beyond research while blocker warnings remain.

**Architecture:** Keep render warnings non-fatal for `research` targets so the k3s lane can be inspected locally. Make the Kubernetes render check fail by default for `experimental` and `supported` targets when `blocker` warnings are present. Preserve `--strict` as the stronger mode that fails on any warning for any status.

**Tech Stack:** Python 3.12, existing `DeploymentTarget` contracts, existing Kubernetes renderer/check modules, pytest, mypy, pre-commit.

---

## Scope

This is M7.D.4 local promotion policy. It does not remove any current warning
or call `kubectl`. It only defines status semantics:

- `research`: blocker warnings are allowed but reported;
- `experimental` / `supported`: blocker warnings fail local governance;
- `--strict`: any warning fails regardless of status.

## Files

- Modify: `agmind/services/kubernetes_checks.py`
- Modify: `tests/test_kubernetes_render_check.py`
- Modify: `.planning/BACKLOG.md`, `.planning/STATE.md`

## Tasks

- [x] **Task 1: RED tests for status-aware blocker policy**
  - Assert current `research` k3s target remains OK with blockers.
  - Assert an equivalent `experimental` Kubernetes target fails if blocker
    warnings remain.
  - Assert strict mode still fails on all warnings.

- [x] **Task 2: Implement target status policy**
  - Add blocker enforcement for non-research targets.
  - Keep text/JSON output stable enough for operators.

- [x] **Task 3: Documentation and verification**
  - Update planning state/backlog.
  - Run focused tests, governance, diff check, and full pre-commit.

## Observed Verification

Local results, 2026-05-24:

- RED test failed first because an equivalent `experimental` Kubernetes target
  still passed with 5 blocker warnings.
- Focused Kubernetes render check tests passed: 10 tests.
- Focused ruff/format passed for `agmind/services/kubernetes_checks.py` and
  `tests/test_kubernetes_render_check.py`.
- Focused mypy passed for `agmind/services/kubernetes_checks.py`.
- `scripts/kubernetes_render_check.py` still passes for the current `research`
  `k3s` target and reports `warnings: 27 (info=0, warning=22, blocker=5)`.
- `scripts/kubernetes_render_check.py --strict` correctly fails while warnings
  remain.
- `scripts/governance_check.py` passed with five checks.
- Expanded M7-focused pytest passed: 153 tests.
- `git diff --check` passed.
- Full `pre-commit run --all-files --show-diff-on-failure` passed.

Remaining external proof:

- real `kubectl apply --dry-run=server` against k3s;
- remediation for current blocker codes before `k3s` can move to
  `experimental`;
- deciding whether warning-level items become blockers for `supported`.
