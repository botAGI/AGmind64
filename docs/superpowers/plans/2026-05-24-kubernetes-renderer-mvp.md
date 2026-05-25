# Kubernetes Renderer MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the first typed Kubernetes/k3s renderer so AGmind can inspect how existing service descriptors translate beyond Compose.

**Architecture:** Keep the existing Compose renderer untouched. Add a separate `agmind.services.kubernetes_renderer` module that consumes `ServiceDescriptor` objects and returns Kubernetes YAML plus explicit portability warnings for Docker-only fields. Wire it through `agmind render kubernetes` and point the research `k3s` target at the new renderer while keeping real k3s deployment gated.

**Tech Stack:** Python 3.12, Pydantic-backed `ServiceDescriptor`, PyYAML, Typer, pytest, existing profile selection helpers from `agmind.services.renderer`.

---

## Scope

This is M7.D.1, not full Kubernetes GA. It deliberately avoids Helm, CRDs,
Ingress, External Secrets, Longhorn manifests, GPU device plugins, and kubectl
execution. The renderer produces a deterministic plain-manifest MVP:

- optional `Namespace`;
- one `Deployment` per selected service;
- one `Service` per selected descriptor with container ports;
- hostPath volume mounts for existing absolute descriptor mounts;
- container env/command/ports/resources/health probes where safely mappable;
- warnings for Docker-only fields such as devices, Unix groups, security
  options, capabilities, Docker socket mounts, and env interpolation.

## Files

- Create: `agmind/services/kubernetes_renderer.py`
- Modify: `agmind/cli/render_cmd.py`
- Modify: `agmind/cli/__init__.py`
- Modify: `templates/deploy-targets/k3s.yaml`
- Create: `tests/test_kubernetes_renderer.py`
- Modify: `tests/test_cli.py`
- Modify: `.planning/BACKLOG.md`, `.planning/ROADMAP.md`, `.planning/STATE.md`

## Tasks

- [x] **Task 1: RED tests for Kubernetes object rendering**
  - Add tests for Namespace/Deployment/Service output from a minimal descriptor.
  - Add tests for hostPath volume parsing and readOnly mounts.
  - Add tests for CPU/memory limit conversion.
  - Expected first run: import failure for `agmind.services.kubernetes_renderer`.

- [x] **Task 2: RED tests for portability warnings**
  - Add tests that descriptors with `devices`, `group_add`, `security_opt`, or
    env interpolation produce warnings.
  - Add tests that `strict=True` raises a `ValueError` with warning details.

- [x] **Task 3: Implement renderer MVP**
  - Implement deterministic Kubernetes manifest generation.
  - Preserve image digests through `ServiceDescriptor.fq_image()`.
  - Convert `[ip:]host:container` ports to `containerPort`/ClusterIP service
    ports without exposing host ports by default.
  - Convert Docker memory units `k/m/g` to Kubernetes `Ki/Mi/Gi`.

- [x] **Task 4: CLI wiring**
  - Add `cmd_render_kubernetes()` to `agmind.cli.render_cmd`.
  - Add `agmind render kubernetes --profile ... --namespace ... --strict`.
  - Add CLI smoke coverage.

- [x] **Task 5: k3s target and planning docs**
  - Change `templates/deploy-targets/k3s.yaml` renderer from
    `future-kubernetes-renderer` to `agmind render kubernetes`.
  - Keep `k3s` status as `research`.
  - Record that real k3s apply/dry-run remains external proof.

- [x] **Task 6: Verification**
  - Run focused renderer/CLI/deploy-target tests.
  - Run governance, diff check, and pre-commit before completion.

## Observed Verification

Local results, 2026-05-24:

- RED import test failed first with
  `ModuleNotFoundError: No module named 'agmind.services.kubernetes_renderer'`.
- RED k3s target test failed while `templates/deploy-targets/k3s.yaml` still
  used `future-kubernetes-renderer`.
- Focused renderer/CLI/target tests passed: 10 tests.
- Target/governance slice passed: 33 tests.
- Expanded M7-focused pytest passed: 142 tests.
- `mypy agmind/services/kubernetes_renderer.py agmind/cli/render_cmd.py` passed.
- `scripts/deploy_target_check.py` passed: 3 targets.
- `scripts/governance_check.py` passed: 4 aggregate checks.
- `git diff --check` passed.
- Full `pre-commit run --all-files --show-diff-on-failure` passed.

External proof still required:

- real `kubectl apply --dry-run=server` against a k3s cluster;
- storage/secrets/Ingress design for Longhorn, External Secrets, and cluster
  ingress controller;
- GPU device-plugin mapping for inference workloads before Kubernetes promotion.
