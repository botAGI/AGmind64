# Kubernetes Secret Env Refs Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Render unresolved secret-like descriptor env placeholders as Kubernetes `secretKeyRef` entries instead of raw `${...}` values.

**Architecture:** Keep Docker Compose and service descriptors unchanged. Add a Kubernetes-only env rendering helper in `agmind.services.kubernetes_renderer` that resolves safe defaults first, then maps secret-like unresolved env values to deterministic operator-managed Secret references. Non-secret unresolved values keep the existing warning behavior.

**Tech Stack:** Python 3.12, existing `ServiceDescriptor` Kubernetes renderer, pytest, ruff, mypy, pre-commit.

---

## Scope

This is local M7.D.13 warning-debt remediation for Kubernetes renders. It does
not create Kubernetes Secret objects and does not introduce External Secrets
manifests yet. It only changes Deployment env entries from raw unresolved
secret placeholders to references such as:

```yaml
env:
- name: POSTGRES_PASSWORD
  valueFrom:
    secretKeyRef:
      name: agmind-postgres-env
      key: POSTGRES_PASSWORD
```

Rules:

- Safe descriptor defaults from M7.D.12 still render as literal `value`.
- Pure secret placeholders such as `${POSTGRES_PASSWORD}` render to
  `secretKeyRef` with the placeholder token as the key.
- Embedded strings containing a secret token, such as
  `postgresql://dify:${POSTGRES_PASSWORD}@postgres:5432/dify?sslmode=disable`,
  render to `secretKeyRef` with the env var name as the key because Kubernetes
  cannot concatenate literal strings and secret keys in one env value.
- Non-secret placeholders such as `${AGMIND_RERANK_FILE:-}` and
  `${AGMIND_ROPE_SCALING:-}` remain unresolved and keep warning metadata.
- Command args are not secret-templated in this step; unresolved command
  placeholders still warn.

Expected warning movement:

- Grafana, Postgres, Redis, and Postgres exporter secret env warnings disappear.
- Current k3s baseline moves from 15 warnings / 0 blockers to 11 warnings / 0 blockers.

## Files

- Modify: `agmind/services/kubernetes_renderer.py`
- Modify: `tests/test_kubernetes_renderer.py`
- Modify: `tests/test_kubernetes_render_check.py`
- Modify: `.planning/STATE.md`, `.planning/ROADMAP.md`, `.planning/BACKLOG.md`,
  `.planning/codebase/ARCHITECTURE.md`, `.planning/codebase/INDEX.md`

## Tasks

- [x] **Task 1: RED renderer tests for secret env refs**
  - Add a renderer test with `POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}`,
    `DATA_SOURCE_NAME: postgresql://dify:${POSTGRES_PASSWORD}@postgres:5432/dify?sslmode=disable`,
    and `AGMIND_RERANK_FILE: ${AGMIND_RERANK_FILE:-}`.
  - Assert `POSTGRES_PASSWORD` renders as `secretKeyRef` key
    `POSTGRES_PASSWORD` in secret `agmind-postgres-env`.
  - Assert `DATA_SOURCE_NAME` renders as `secretKeyRef` key
    `DATA_SOURCE_NAME` in secret `agmind-postgres-env`.
  - Assert `AGMIND_RERANK_FILE` remains a literal unresolved value and still
    emits `env-interpolation`.

- [x] **Task 2: RED render-check baseline tests**
  - Update the Kubernetes render-check baseline to 11 warning-level items and
    0 blockers.
  - Assert JSON output no longer contains `env-interpolation` warnings for
    `grafana`, `postgres`, `redis`, or `postgres-exporter`.
  - Keep assertions for remaining non-secret env/command warnings.

- [x] **Task 3: Implement Kubernetes-only secret env mapping**
  - Add helper functions for secret-like placeholder detection.
  - Render env entries through one helper that returns either `value` or
    `valueFrom.secretKeyRef`.
  - Keep `collect_portability_warnings` aligned with that helper so mapped
    secret refs do not emit `env-interpolation`.

- [x] **Task 4: Planning and verification**
  - Update `.planning` with the M7.D.13 checkpoint and warning baseline.
  - Run focused renderer/check tests, focused ruff/format/mypy, Kubernetes
    render check, aggregate governance, expanded M7 pytest, `git diff --check`,
    and full pre-commit.

## Progress

- RED observed: focused renderer/check tests failed because
  `POSTGRES_PASSWORD` rendered as raw `${POSTGRES_PASSWORD}` and the k3s
  warning baseline remained 15 instead of 11.
- GREEN observed: `.venv/bin/pytest tests/test_kubernetes_renderer.py tests/test_kubernetes_render_check.py -q`
  passed with `26 passed`; `scripts/kubernetes_render_check.py` reports
  `36 objects`, `11 warnings`, and `0 blockers`.
- Final local verification observed: expanded M7-focused pytest passed
  `172 passed`; aggregate governance passed 5 checks; `git diff --check`
  passed; full `pre-commit run --all-files --show-diff-on-failure` passed.
