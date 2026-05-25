# Portainer Kubernetes Omission Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the remaining Docker socket blocker by omitting the current Portainer descriptor from Kubernetes renders with an explicit warning.

**Architecture:** Keep the Docker Compose service descriptor unchanged. Add a narrow Kubernetes renderer omission policy for the current Docker-socket-based Portainer descriptor: do not emit Deployment/Service objects for it, and emit a `kubernetes-omitted` warning instead of a `docker-socket` blocker. This keeps Kubernetes evidence honest while preserving the Compose lane.

**Tech Stack:** Python 3.12, existing `agmind.services.kubernetes_renderer`, pytest, ruff, mypy, pre-commit.

---

## Scope

This is local blocker remediation after M7.D.8. It does not add a Kubernetes
Portainer chart or agent and does not remove Portainer from Compose. It only
prevents a Docker-management UI from being rendered as if it were a valid k3s
workload.

Expected current warning movement:

- blocker count decreases from 4 to 3;
- `docker-socket` blockers decrease from 1 to 0;
- warning count increases from 22 to 23 due to explicit `kubernetes-omitted`;
- Portainer Deployment/Service are omitted from Kubernetes render output.

## Files

- Modify: `agmind/services/kubernetes_renderer.py`
- Modify: `tests/test_kubernetes_renderer.py`
- Modify: `tests/test_kubernetes_render_check.py`
- Modify: `.planning/STATE.md`, `.planning/ROADMAP.md`, `.planning/BACKLOG.md`

## Tasks

- [x] **Task 1: RED tests for Portainer omission**
  - Add a renderer test for a Portainer descriptor with `/var/run/docker.sock`.
  - Assert no Deployment/Service object named `portainer` is emitted.
  - Assert the warning is `kubernetes-omitted` with severity `warning`.
  - Assert no Portainer `docker-socket` blocker is emitted.
  - Observed RED: Portainer still rendered a Deployment/Service and emitted
    `docker-socket`.

- [x] **Task 2: RED tests for blocker baseline**
  - Update render-check tests to expect 3 blockers and 23 warnings.
  - Assert blocker breakdown contains `docker-device=3` and no `docker-socket=`.
  - Observed RED: render check still reported 4 blockers.

- [x] **Task 3: Implement omission policy**
  - Add a small renderer predicate for compose-only Portainer.
  - Return an omission warning before generic Docker socket warnings.
  - Skip omitted descriptors when producing Kubernetes objects.
  - Leave all non-Portainer Docker socket descriptors as blockers.
  - Implemented in `agmind.services.kubernetes_renderer`.

- [x] **Task 4: Planning and verification**
  - Update `.planning` with the M7.D.9 checkpoint.
  - Run focused renderer/check tests, lint/type checks, render governance,
    expanded M7 pytest, `git diff --check`, and full pre-commit.
  - Verified focused renderer/check tests: 21 passed.
  - Verified focused format/lint/type checks: ruff format check, ruff check,
    and mypy all passed for the changed Kubernetes renderer/test files.
  - Verified render governance: k3s renders 36 objects, 26 warnings, and 3
    blockers, all `docker-device`.
  - Verified expanded M7 pytest slice: 163 passed.
  - Verified aggregate governance: 5 checks passed.
  - Verified `git diff --check` and full
    `pre-commit run --all-files --show-diff-on-failure`.
