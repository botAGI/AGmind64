# Kubernetes Empty Env Defaults Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Render explicit empty descriptor env defaults as Kubernetes empty-string env values while keeping unsafe command placeholders unresolved.

**Architecture:** Keep Docker Compose and service descriptors unchanged. Extend only the Kubernetes renderer's env resolution path so `${VAR:-}` becomes `value: ""` for environment variables. Command rendering continues to require non-empty values, so model paths such as `/models/${AGMIND_RERANK_FILE}` remain warning debt until an operator supplies a model file or the service is omitted.

**Tech Stack:** Python 3.12, existing `ServiceDescriptor` Kubernetes renderer, pytest, ruff, mypy, pre-commit.

---

## Scope

This is local M7.D.16 warning-debt remediation for Kubernetes renders.

Rules:

- Env values with explicit empty defaults, such as `${AGMIND_ROPE_SCALING:-}`,
  render as `value: ""`.
- Nested env defaults still resolve as before.
- Secret refs still win for secret-like unresolved values.
- Command args do not resolve empty defaults or empty env-derived values.
- `/models/${AGMIND_RERANK_FILE}` remains a `command-interpolation` warning
  while `AGMIND_RERANK_FILE` itself renders as empty env.

Expected warning movement:

- `llama-llm` no longer emits `env-interpolation` for `AGMIND_ROPE_SCALING`.
- `llama-rerank` no longer emits `env-interpolation` for `AGMIND_RERANK_FILE`.
- `llama-rerank` still emits `command-interpolation` for the model path.
- Current k3s baseline moves from 7 warnings / 0 blockers to 5 warnings /
  0 blockers.

## Files

- Modify: `agmind/services/kubernetes_renderer.py`
- Modify: `tests/test_kubernetes_renderer.py`
- Modify: `tests/test_kubernetes_render_check.py`
- Modify: `.planning/STATE.md`, `.planning/ROADMAP.md`, `.planning/BACKLOG.md`,
  `.planning/codebase/ARCHITECTURE.md`, `.planning/codebase/INDEX.md`

## Tasks

- [x] **Task 1: RED renderer tests for empty env defaults**
  - Update the secret env renderer test so `AGMIND_RERANK_FILE:
    ${AGMIND_RERANK_FILE:-}` is expected to render as `value: ""`.
  - Assert no `env-interpolation` warning remains for that env var.

- [x] **Task 2: RED renderer test for command safety**
  - Add a renderer test with env `MODEL_FILE: ${MODEL_FILE:-}` and command
    `/models/${MODEL_FILE}`.
  - Assert env renders as empty string.
  - Assert command remains `/models/${MODEL_FILE}` and emits
    `command-interpolation`.

- [x] **Task 3: RED render-check baseline tests**
  - Update the Kubernetes render-check baseline to 5 warning-level items and
    0 blockers.
  - Assert JSON output no longer contains env warnings for
    `AGMIND_ROPE_SCALING` or `AGMIND_RERANK_FILE`.
  - Keep the `llama-rerank` `command-interpolation` assertion.

- [x] **Task 4: Implement env-only empty default resolution**
  - Add an `allow_empty_default` parameter to the interpolation resolver.
  - Use it only from `_resolved_env_for_descriptor`.
  - Keep command resolution on non-empty values only.

- [x] **Task 5: Planning and verification**
  - Update `.planning` with the M7.D.16 checkpoint and warning baseline.
  - Run focused renderer/check tests, focused ruff/format/mypy, Kubernetes
    render check, aggregate governance, expanded M7 pytest, `git diff --check`,
    and full pre-commit.

## Progress

- RED observed: focused renderer/check tests failed because `${VAR:-}` env
  values still rendered as raw placeholders and the k3s warning baseline
  remained 7 instead of 5.
- GREEN observed: `.venv/bin/pytest tests/test_kubernetes_renderer.py tests/test_kubernetes_render_check.py -q`
  passed with `30 passed`; `scripts/kubernetes_render_check.py --json` reports
  `36 objects`, `5 warnings`, and `0 blockers`.
- Final verification observed: focused ruff format check, ruff check, and mypy
  passed; focused Kubernetes renderer/check tests passed 30 tests; text
  Kubernetes render check reported 36 objects, 5 warnings, and 0 blockers;
  expanded M7-focused pytest passed 176 tests; aggregate governance passed 5
  checks; `git diff --check` and full `pre-commit --all-files` passed.
