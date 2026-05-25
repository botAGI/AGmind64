# Kubernetes Default Interpolation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Kubernetes manifests safer by resolving explicit non-empty `${VAR:-default}` descriptor defaults and warning on unresolved command placeholders.

**Architecture:** Keep service descriptors and Docker Compose rendering unchanged. Add a small Kubernetes-only interpolation resolver inside `agmind.services.kubernetes_renderer`: it resolves descriptor env defaults without reading host environment, uses resolved env values when rendering command args, and leaves secrets or empty defaults unresolved with warning metadata. This turns known model defaults into concrete Kubernetes manifest values while keeping sensitive or intentionally unset values visible.

**Tech Stack:** Python 3.12, existing `ServiceDescriptor` renderer, pytest, ruff, mypy, pre-commit.

---

## Scope

This is local M7.D.12 warning-debt remediation. It does not introduce Secrets,
ConfigMaps, External Secrets, or an env-file loader. It only resolves defaults
already encoded in service descriptors. Host environment values are never read,
so rendered artifacts remain deterministic and safe for review.

Expected warning movement:

- defaulted llama/embed env values stop emitting `env-interpolation` warnings;
- matching command args such as `/models/${AGMIND_MODEL_FILE}` render with the
  resolved default;
- secrets such as `${POSTGRES_PASSWORD}` stay unresolved and keep
  `env-interpolation` warnings;
- empty defaults such as `${AGMIND_RERANK_FILE:-}` stay unresolved because they
  need an operator value;
- unresolved command placeholders emit `command-interpolation` warnings.

## Files

- Modify: `agmind/services/kubernetes_renderer.py`
- Modify: `tests/test_kubernetes_renderer.py`
- Modify: `tests/test_kubernetes_render_check.py`
- Modify: `.planning/STATE.md`, `.planning/ROADMAP.md`, `.planning/BACKLOG.md`,
  `.planning/codebase/ARCHITECTURE.md`, `.planning/codebase/INDEX.md`

## Tasks

- [x] **Task 1: RED renderer tests for default env interpolation**
  - Add a renderer test with env values:
    `MODEL: ${MODEL:-model.gguf}`, `CTX: ${CTX:-${BASE_CTX:-8192}}`, and
    `SECRET: ${SECRET}`.
  - Assert Kubernetes env renders `MODEL=model.gguf` and `CTX=8192`.
  - Assert `SECRET` remains `${SECRET}` and emits one `env-interpolation`
    warning.
  - Assert no host environment lookup is needed.

- [x] **Task 2: RED renderer tests for command interpolation**
  - Add a renderer test with command args `["/models/${MODEL}", "${CTX}",
    "${MISSING}"]` and the defaulted env from Task 1.
  - Assert command args render to `["/models/model.gguf", "8192",
    "${MISSING}"]`.
  - Assert unresolved `${MISSING}` emits `command-interpolation`.

- [x] **Task 3: RED render-check baseline tests**
  - Update Kubernetes render-check baseline to the new warning count.
  - Assert JSON warnings include `command-interpolation` for
    `llama-rerank`.
  - Assert defaulted llama/embed env warnings no longer appear.

- [x] **Task 4: Implement Kubernetes-only resolver**
  - Add a placeholder regex and bounded iterative resolver for `${VAR}` and
    `${VAR:-default}`.
  - Resolve only non-empty defaults or values already resolved from descriptor
    env.
  - Use the resolver in container env rendering and command rendering.
  - Update warning collection to warn only for unresolved env/command
    placeholders.

- [x] **Task 5: Planning and verification**
  - Update `.planning` with the M7.D.12 checkpoint and warning baseline.
  - Run focused renderer/check tests, focused ruff/format/mypy, render
    governance, expanded M7 pytest, `git diff --check`, and full pre-commit.

## Progress

- RED observed: focused renderer and render-check tests failed while defaulted
  env/command placeholders still rendered unresolved and the k3s warning
  baseline remained 26.
- GREEN observed: `.venv/bin/pytest tests/test_kubernetes_renderer.py tests/test_kubernetes_render_check.py -q`
  passed with `25 passed`; `scripts/kubernetes_render_check.py` now reports
  `36 objects`, `15 warnings`, and `0 blockers`.
- Final local verification observed: expanded M7-focused pytest passed
  `171 passed`; aggregate governance passed 5 checks; `git diff --check`
  passed; full `pre-commit run --all-files --show-diff-on-failure` passed.
