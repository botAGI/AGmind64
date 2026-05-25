# AMD GPU Kubernetes Device Mapping Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the remaining `/dev/dri` Kubernetes render blockers with an explicit AMD GPU device-plugin resource mapping.

**Architecture:** Keep Docker Compose descriptors unchanged. In the Kubernetes renderer, recognize the existing llama `/dev/dri` device mapping and render it as the Kubernetes extended resource `amd.com/gpu: "1"` on the container requests/limits. Emit a warning that the target cluster must run an AMD GPU device plugin, while preserving blocker severity for any unknown Docker device mappings.

**Tech Stack:** Python 3.12, `agmind.services.kubernetes_renderer`, Kubernetes extended resources, pytest, ruff, mypy, pre-commit.

---

## Scope

This is local Kubernetes blocker remediation after M7.D.9. It does not install
the AMD GPU Operator, Helm charts, or a device plugin. It only makes the
plain-manifest renderer produce a Kubernetes-native resource request for the
known AGmind `/dev/dri` inference services and keeps real cluster validation as
external evidence.

Expected current warning movement:

- blocker count decreases from 3 to 0;
- warning count increases from 23 to 26 because the three llama device
  mappings become `amd-gpu-device-plugin` warnings;
- total warning count remains 26;
- default render governance can pass for the research k3s target with no
  blockers, while `--strict` still fails because warning-level debt remains.

## Files

- Modify: `agmind/services/kubernetes_renderer.py`
- Modify: `tests/test_kubernetes_renderer.py`
- Modify: `tests/test_kubernetes_render_check.py`
- Modify: `.planning/STATE.md`, `.planning/ROADMAP.md`, `.planning/BACKLOG.md`,
  `.planning/codebase/ARCHITECTURE.md`, `.planning/codebase/INDEX.md`

## Tasks

- [x] **Task 1: RED renderer tests for `/dev/dri` mapping**
  - Add a renderer test that uses a `llama-llm` descriptor with `devices:
    ["/dev/dri"]`.
  - Assert the rendered container has both `requests` and `limits` for
    `amd.com/gpu: "1"`.
  - Assert the warning code is `amd-gpu-device-plugin` with severity `warning`.
  - Assert no `docker-device` warning is emitted for that descriptor.
  - Keep or add a separate unknown-device test asserting `/dev/custom0` still
    emits a `docker-device` blocker and does not render `amd.com/gpu`.
  - Observed RED: focused Kubernetes suite failed because `amd.com/gpu` was
    missing, unknown-device messages were generic, and the k3s baseline still
    reported 3 blockers.

- [x] **Task 2: RED render-check baseline tests**
  - Update Kubernetes render-check expectations to 0 blockers and 26 warnings.
  - Assert text output no longer contains `blockers:`.
  - Assert JSON output contains an `amd-gpu-device-plugin` warning and no
    blocker summary.
  - Run the focused Kubernetes tests and record the expected RED failures.
  - Observed RED in `tests/test_kubernetes_render_check.py`: blocker summary
    remained 3 and text output still showed `docker-device=3`.

- [x] **Task 3: Implement AMD GPU device-plugin mapping**
  - Add constants for the known Docker device path `/dev/dri`, Kubernetes
    resource name `amd.com/gpu`, and default request quantity `"1"`.
  - Render `amd.com/gpu: "1"` into container `resources.limits` and
    `resources.requests` when a descriptor contains `/dev/dri`.
  - Emit `amd-gpu-device-plugin` warning before generic unknown-device blocker
    handling.
  - Leave unknown devices as `docker-device` blockers.
  - Implemented in `agmind.services.kubernetes_renderer`; focused Kubernetes
    tests now pass 23 tests.

- [x] **Task 4: Planning and verification**
  - Update `.planning` with the M7.D.10 checkpoint and next real-cluster proof
    focus.
  - Run focused renderer/check tests, focused ruff/format/mypy, render
    governance, expanded M7 pytest, `git diff --check`, and full pre-commit.
  - Verified focused Kubernetes renderer/check tests: 23 passed.
  - Verified focused ruff format check, ruff check, and mypy.
  - Verified render governance: k3s renders 36 objects, 26 warnings, and 0
    blockers.
  - Verified aggregate governance: 5 checks passed.
  - Verified expanded M7 pytest slice: 165 passed.
  - Verified `git diff --check` and full
    `pre-commit run --all-files --show-diff-on-failure`.
