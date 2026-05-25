# Kubernetes AMD GPU Group Policy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop reporting Docker `video`/`render` group warnings when Kubernetes already maps `/dev/dri` inference access to the AMD GPU device-plugin resource.

**Architecture:** Keep Docker Compose and service descriptors unchanged. Extend the Kubernetes renderer's group warning helper so named GPU groups remain warning debt in general, but are considered covered when the same descriptor maps `/dev/dri` to `amd.com/gpu`. The existing `amd-gpu-device-plugin` warning remains the single cluster prerequisite for those services.

**Tech Stack:** Python 3.12, existing `ServiceDescriptor` Kubernetes renderer, pytest, ruff, mypy, pre-commit.

---

## Scope

This is local M7.D.15 warning-debt remediation for Kubernetes renders. It does
not change Docker Compose behavior and does not add new descriptor fields.

Rules:

- If a descriptor has `devices: ["/dev/dri"]`, Kubernetes renders
  `amd.com/gpu: "1"` and the `amd-gpu-device-plugin` warning remains.
- For that same descriptor, `group_add: ["video", "render"]` no longer emits
  a separate `docker-group-add` warning because host group access is a Docker
  implementation detail replaced by the Kubernetes device plugin contract.
- Named non-GPU groups still emit `docker-group-add`.
- `video`/`render` without `/dev/dri` still emit `docker-group-add`.
- Numeric group IDs still render as pod `securityContext.supplementalGroups`.

Expected warning movement:

- `llama-llm`, `llama-embed`, and `llama-rerank` lose their
  `docker-group-add` warnings.
- Current k3s baseline moves from 10 warnings / 0 blockers to 7 warnings /
  0 blockers.

## Files

- Modify: `agmind/services/kubernetes_renderer.py`
- Modify: `tests/test_kubernetes_renderer.py`
- Modify: `tests/test_kubernetes_render_check.py`
- Modify: `.planning/STATE.md`, `.planning/ROADMAP.md`, `.planning/BACKLOG.md`,
  `.planning/codebase/ARCHITECTURE.md`, `.planning/codebase/INDEX.md`

## Tasks

- [x] **Task 1: RED renderer test for AMD GPU group coverage**
  - Extend the existing `/dev/dri` renderer test to assert no
    `docker-group-add` warning is emitted when `group_add` contains
    `video`/`render`.
  - Add a small negative assertion that a descriptor with `group_add=["render"]`
    and no `/dev/dri` still emits `docker-group-add`.

- [x] **Task 2: RED render-check baseline tests**
  - Update the Kubernetes render-check baseline to 7 warning-level items and
    0 blockers.
  - Assert JSON output no longer contains `docker-group-add` for
    `llama-llm`, `llama-embed`, or `llama-rerank`.

- [x] **Task 3: Implement Kubernetes-only AMD group suppression**
  - Add a constant for GPU Docker groups: `video` and `render`.
  - Update `_unmapped_group_add` so those groups are considered mapped when
    `_requires_amd_gpu_resource(descriptor)` is true.
  - Keep numeric `group_add` and unrelated named group behavior unchanged.

- [x] **Task 4: Planning and verification**
  - Update `.planning` with the M7.D.15 checkpoint and warning baseline.
  - Run focused renderer/check tests, focused ruff/format/mypy, Kubernetes
    render check, aggregate governance, expanded M7 pytest, `git diff --check`,
    and full pre-commit.

## Progress

- RED observed: focused renderer/check tests failed because `/dev/dri`
  descriptors still emitted `docker-group-add` for `video`/`render`, and the
  k3s warning baseline remained 10 instead of 7.
- GREEN observed: `.venv/bin/pytest tests/test_kubernetes_renderer.py tests/test_kubernetes_render_check.py -q`
  passed with `29 passed`; `scripts/kubernetes_render_check.py` reports
  `36 objects`, `7 warnings`, and `0 blockers`.
- Final local verification observed: expanded M7-focused pytest passed
  `175 passed`; aggregate governance passed 5 checks; `git diff --check`
  passed; full `pre-commit run --all-files --show-diff-on-failure` passed.
