# Kubernetes Rerank Omission Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the final unexpected Kubernetes strict warning by omitting `llama-rerank` from k3s renders when no rerank model file is configured.

**Architecture:** Keep Docker Compose descriptors and installer semantics unchanged. Extend the Kubernetes renderer's existing omission policy so the known optional rerank service is omitted when `AGMIND_RERANK_FILE` resolves to an explicit empty default. This preserves operator visibility through a `kubernetes-omitted` warning and allows strict render validation to pass because `k3s` already declares that warning code as expected research debt.

**Tech Stack:** Python 3.12, ServiceDescriptor Kubernetes renderer, Kubernetes render check, pytest, ruff, mypy, pre-commit.

---

## Scope

This is local M7.D.18 work before real k3s server-side dry-run evidence.

Rules:

- `llama-rerank` with `AGMIND_RERANK_FILE: ${AGMIND_RERANK_FILE:-}` is omitted
  from Kubernetes output.
- The omission emits `kubernetes-omitted` with a message that mentions the
  missing rerank model file.
- No `command-interpolation` warning remains for the default k3s render.
- Default k3s render moves from 36 objects / 5 warnings / 0 blockers to
  34 objects / 4 warnings / 0 blockers.
- Strict k3s render check passes because remaining warnings are target-declared:
  `amd-gpu-device-plugin` and `kubernetes-omitted`.
- Docker Compose behavior and service descriptors remain unchanged.

## Files

- Modify: `agmind/services/kubernetes_renderer.py`
- Modify: `tests/test_kubernetes_renderer.py`
- Modify: `tests/test_kubernetes_render_check.py`
- Modify: `.planning/STATE.md`, `.planning/ROADMAP.md`, `.planning/BACKLOG.md`,
  `.planning/codebase/ARCHITECTURE.md`, `.planning/codebase/INDEX.md`

## Tasks

- [x] **Task 1: RED renderer omission tests**
  - Add a renderer test proving default `llama-rerank` with empty
    `AGMIND_RERANK_FILE` produces no Deployment/Service and emits
    `kubernetes-omitted`.
  - Keep the generic command-interpolation test for non-rerank services, so the
    policy does not silently hide arbitrary unresolved commands.

- [x] **Task 2: RED render-check baseline tests**
  - Update current k3s render-check expectations to 34 objects, 21 deployments,
    12 services, 4 warnings, and 0 blockers.
  - Assert no default k3s warning has code `command-interpolation`.
  - Update strict render-check tests so `--strict` passes.

- [x] **Task 3: Implement rerank omission policy**
  - Add a focused helper that recognizes `llama-rerank` with an explicitly
    empty `AGMIND_RERANK_FILE`.
  - Reuse the existing Kubernetes omission path and warning code.
  - Use a rerank-specific omission message/remediation.

- [x] **Task 4: Planning and verification**
  - Update `.planning` and this plan with the M7.D.18 checkpoint.
  - Run focused renderer/check tests, focused ruff/format/mypy, Kubernetes
    render check in normal and strict modes, aggregate governance, expanded M7
    pytest, `git diff --check`, and full pre-commit.

## Progress

- Selected this after M7.D.17 because strict mode had exactly one unexpected
  warning left: `command-interpolation=1` for `/models/${AGMIND_RERANK_FILE}`.
  The installer and wizard already define an empty rerank file as "skip rerank
  service", so Kubernetes should mirror that policy instead of inventing a
  default model.
- RED observed: focused renderer/check tests failed because `llama-rerank` was
  still rendered with unresolved `/models/${AGMIND_RERANK_FILE}`, the k3s
  baseline still had 36 objects and 5 warnings, and strict mode still failed
  on `command-interpolation=1`.
- GREEN observed: focused renderer/check tests passed 32 tests; focused ruff
  format check, ruff check, and mypy passed; normal and strict Kubernetes
  render checks both report 34 objects, 4 warnings, and 0 blockers.
- Final verification observed: aggregate governance passed 5 checks; expanded
  M7-focused pytest passed 179 tests; `git diff --check` and full
  `pre-commit --all-files` passed.
