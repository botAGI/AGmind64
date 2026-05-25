# Kubernetes Security Context Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Translate Docker security fields that have safe Kubernetes equivalents into explicit pod/container `securityContext` output.

**Architecture:** Keep Docker Compose and service descriptors unchanged. Extend the Kubernetes renderer with narrow mapping helpers: supported `security_opt` entries become container `securityContext.seccompProfile`, Linux capabilities become container `securityContext.capabilities.add`, and numeric `group_add` entries become pod `securityContext.supplementalGroups`. Named groups such as `video` and `render` remain warning debt because Kubernetes requires numeric group IDs.

**Tech Stack:** Python 3.12, existing `ServiceDescriptor` Kubernetes renderer, pytest, ruff, mypy, pre-commit.

---

## Scope

This is local M7.D.14 warning-debt remediation for Kubernetes renders. It does
not change Docker Compose behavior and does not add new descriptor fields.

Mappings:

- `security_opt: ["seccomp=unconfined"]` renders container:

```yaml
securityContext:
  seccompProfile:
    type: Unconfined
```

- `cap_add: ["SYS_PTRACE"]` renders container:

```yaml
securityContext:
  capabilities:
    add:
    - SYS_PTRACE
```

- `group_add: ["44", "107"]` renders pod:

```yaml
securityContext:
  supplementalGroups:
  - 44
  - 107
```

Non-mappings:

- Named `group_add` values such as `video` and `render` stay warnings until a
  deploy target provides numeric GID policy.
- Unknown `security_opt` values stay warnings.
- No pod security admission, SCC, PSP, or privileged-mode policy is introduced.

Expected warning movement:

- The baseline `llama-llm` `seccomp=unconfined` warning disappears.
- Current k3s baseline moves from 11 warnings / 0 blockers to 10 warnings / 0 blockers.

## References

- Kubernetes security context docs: `securityContext` supports UID/GID,
  supplemental groups, Linux capabilities, and seccomp profiles.
- Kubernetes docs note `supplementalGroups` is a list of group IDs, so group
  names cannot be translated safely without target-specific GID policy.

## Files

- Modify: `agmind/services/kubernetes_renderer.py`
- Modify: `tests/test_kubernetes_renderer.py`
- Modify: `tests/test_kubernetes_render_check.py`
- Modify: `.planning/STATE.md`, `.planning/ROADMAP.md`, `.planning/BACKLOG.md`,
  `.planning/codebase/ARCHITECTURE.md`, `.planning/codebase/INDEX.md`

## Tasks

- [x] **Task 1: RED renderer tests for container securityContext**
  - Add a renderer test with `security_opt=["seccomp=unconfined"]` and
    `cap_add=["SYS_PTRACE"]`.
  - Assert the container has `securityContext.seccompProfile.type` set to
    `Unconfined`.
  - Assert the container has `securityContext.capabilities.add` set to
    `["SYS_PTRACE"]`.
  - Assert there are no `docker-security-opt` or `linux-capability` warnings
    for mapped values.

- [x] **Task 2: RED renderer tests for numeric and named group_add**
  - Add a renderer test with `group_add=["44", "107", "render"]`.
  - Assert the pod spec has `securityContext.supplementalGroups` set to
    `[44, 107]`.
  - Assert the named `render` value still emits `docker-group-add`.

- [x] **Task 3: RED render-check baseline tests**
  - Update the Kubernetes render-check baseline to 10 warning-level items and
    0 blockers.
  - Assert JSON output no longer contains `docker-security-opt` for
    `llama-llm`.
  - Keep assertions for named group warnings, AMD GPU device plugin warnings,
    unresolved rerank file warnings, and Portainer omission.

- [x] **Task 4: Implement Kubernetes-only securityContext mapping**
  - Add pod securityContext helper for numeric supplemental groups.
  - Add container securityContext helper for seccomp and capabilities.
  - Update warning collection so mapped options no longer emit portability
    warnings while unmapped options still do.

- [x] **Task 5: Planning and verification**
  - Update `.planning` with the M7.D.14 checkpoint and warning baseline.
  - Run focused renderer/check tests, focused ruff/format/mypy, Kubernetes
    render check, aggregate governance, expanded M7 pytest, `git diff --check`,
    and full pre-commit.

## Progress

- RED observed: focused renderer/check tests failed because supported
  `security_opt` and numeric `group_add` values did not render
  `securityContext`, and the k3s warning baseline remained 11 instead of 10.
- GREEN observed: `.venv/bin/pytest tests/test_kubernetes_renderer.py tests/test_kubernetes_render_check.py -q`
  passed with `28 passed`; `scripts/kubernetes_render_check.py` reports
  `36 objects`, `10 warnings`, and `0 blockers`.
- Final local verification observed: expanded M7-focused pytest passed
  `174 passed`; aggregate governance passed 5 checks; `git diff --check`
  passed; full `pre-commit run --all-files --show-diff-on-failure` passed.
