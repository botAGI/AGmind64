# Kubernetes Remediation Report Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Kubernetes render governance produce a service-level remediation checklist for blocker/warning portability findings.

**Architecture:** Preserve the existing renderer and promotion policy. Extend the Kubernetes render check report to carry structured warning findings from the renderer, including service, code, severity, message, and remediation. The text report will remain compact but include blocker-code breakdowns for operator triage.

**Tech Stack:** Python 3.12, dataclasses, existing Kubernetes renderer/check modules, pytest, mypy, pre-commit.

---

## Scope

This is M7.D.5 local reporting work. It does not change manifests, target
status, or warning policy. It exposes the current policy data more clearly:

- full warning list in JSON output;
- compact blocker breakdown in text output;
- remediation strings preserved from the renderer policy.

## Files

- Modify: `agmind/services/kubernetes_checks.py`
- Modify: `tests/test_kubernetes_render_check.py`
- Modify: `.planning/BACKLOG.md`, `.planning/STATE.md`

## Tasks

- [x] **Task 1: RED tests for warning details**
  - Assert JSON includes `warnings` per target.
  - Assert warning records include service/code/severity/message/remediation.
  - Observed RED: focused pytest failed because target JSON had no
    `warnings` key.

- [x] **Task 2: RED tests for blocker breakdown**
  - Assert text output includes a compact blocker-code breakdown.
  - Observed RED: focused pytest failed because text output had no
    `blockers:` line.

- [x] **Task 3: Preserve renderer warning metadata in checks**
  - Use renderer result data instead of reparsing warning comments for policy
    metadata.
  - Add warning serialization and blocker-code aggregation.
  - Implemented in `agmind.services.kubernetes_checks`.

- [x] **Task 4: Documentation and verification**
  - Update planning state/backlog.
  - Run focused tests, governance, diff check, and full pre-commit.
  - Verification passed:
    - `.venv/bin/pytest tests/test_kubernetes_render_check.py -q` — 10 passed.
    - `.venv/bin/pytest ... M7 focused slice ... -q` — 153 passed.
    - `.venv/bin/python scripts/kubernetes_render_check.py` — reports
      `blockers: docker-device=3, docker-socket=2`.
    - `.venv/bin/python scripts/governance_check.py` — governance OK, 5 checks.
    - `git diff --check` — clean.
    - `.venv/bin/pre-commit run --all-files --show-diff-on-failure` — passed.
