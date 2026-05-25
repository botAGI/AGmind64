# Traefik Kubernetes Provider Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the Traefik Docker socket blocker from Kubernetes renders by switching Traefik to Kubernetes-native provider args.

**Architecture:** Keep Docker Compose descriptors unchanged. Add a narrow Kubernetes-renderer adaptation for the `traefik` service: omit the Docker socket hostPath mount from rendered Kubernetes manifests and replace Docker provider CLI args with Kubernetes provider CLI args. Leave non-Traefik Docker socket mounts, such as Portainer, as blockers.

**Tech Stack:** Python 3.12, existing `agmind.services.kubernetes_renderer`, pytest, ruff, mypy, pre-commit.

---

## Scope

This is the first local blocker remediation after M7.D.7. It does not make
Traefik fully production-ready on Kubernetes and does not add Ingress/CRD
objects yet. It only removes an unsafe Docker socket dependency where there is
a clear Kubernetes-native provider replacement.

Expected current warning movement:

- blocker count decreases from 5 to 4;
- `docker-socket` blockers decrease from 2 to 1;
- Portainer remains a `docker-socket` blocker until it is removed or replaced
  for Kubernetes lanes.

## Files

- Modify: `agmind/services/kubernetes_renderer.py`
- Modify: `tests/test_kubernetes_renderer.py`
- Modify: `tests/test_kubernetes_render_check.py`
- Modify: `.planning/STATE.md`, `.planning/ROADMAP.md`, `.planning/BACKLOG.md`

## Tasks

- [x] **Task 1: RED tests for Traefik Kubernetes provider mapping**
  - Add a test that renders a Traefik descriptor with `/var/run/docker.sock`.
  - Assert the rendered Deployment does not mount the Docker socket.
  - Assert container args include `--providers.kubernetesingress=true`.
  - Assert no `docker-socket` warning is emitted for Traefik.
  - Observed RED: Traefik still mounted `/var/run/docker.sock`.

- [x] **Task 2: RED tests for blocker count movement**
  - Update render-check tests to expect 4 blocker warnings.
  - Assert text output includes `docker-socket=1`, not `docker-socket=2`.
  - Keep generic Docker socket warnings covered by the existing non-Traefik
    metadata test.
  - Observed RED: k3s render check still reported 5 blockers.

- [x] **Task 3: Implement the renderer adaptation**
  - Add a tiny service-specific predicate for Traefik Docker socket replacement.
  - Filter Traefik Docker socket mounts out of rendered Kubernetes volumes.
  - Rewrite Traefik Docker provider command args to Kubernetes provider args.
  - Keep default renderer behavior unchanged for all other services.
  - Implemented in `agmind.services.kubernetes_renderer`.

- [x] **Task 4: Planning and verification**
  - Update `.planning` with the M7.D.8 checkpoint.
  - Run focused renderer/check tests, lint/type checks, render governance,
    expanded M7 pytest, `git diff --check`, and full pre-commit.
  - Verification passed:
    - `.venv/bin/pytest tests/test_kubernetes_renderer.py tests/test_kubernetes_render_check.py -q` — 20 passed.
    - `.venv/bin/python scripts/kubernetes_render_check.py` — 26 warnings, 4 blockers, `docker-socket=1`.
    - `.venv/bin/python scripts/governance_check.py` — governance OK, 5 checks.
    - Expanded M7 pytest slice — 162 passed.
    - Focused ruff, format check, and mypy passed.
    - `git diff --check` — clean.
    - `.venv/bin/pre-commit run --all-files --show-diff-on-failure` — passed.
