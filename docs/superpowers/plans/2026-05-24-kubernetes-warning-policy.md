# Kubernetes Warning Policy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Kubernetes render strict mode reject unexpected warnings while allowing target-declared research debts.

**Architecture:** Keep warnings emitted by the renderer unchanged. Extend the deployment target verification contract with expected Kubernetes warning codes. The Kubernetes render check will compare emitted warnings against that target policy so `--strict` can distinguish known cluster prerequisites and Compose-only omissions from unresolved service configuration debt.

**Tech Stack:** Python 3.12, Pydantic deployment target models, Kubernetes render check, pytest, ruff, mypy, pre-commit.

---

## Scope

This is local M7.D.17 policy work before real cluster evidence.

Expected policy:

- `k3s` declares expected warning codes:
  - `amd-gpu-device-plugin`
  - `kubernetes-omitted`
- `command-interpolation` is not expected and must still fail strict mode.
- Default non-strict render governance remains unchanged.
- Targets with no expected warning policy keep the old strict behavior: any warning fails.

## Files

- Modify: `agmind/deploy/targets.py`
- Modify: `agmind/services/kubernetes_checks.py`
- Modify: `templates/deploy-targets/k3s.yaml`
- Regenerate: `templates/schemas/deploy-target.json`
- Modify: `tests/test_deploy_targets.py`
- Modify: `tests/test_kubernetes_render_check.py`
- Modify: `.planning/STATE.md`, `.planning/ROADMAP.md`, `.planning/BACKLOG.md`,
  `.planning/codebase/ARCHITECTURE.md`, `.planning/codebase/INDEX.md`

## Tasks

- [x] **Task 1: RED deployment target contract tests**
  - Add a test that `DeploymentVerification` parses `expected_warning_codes`.
  - Add a repository assertion that `k3s` declares
    `("amd-gpu-device-plugin", "kubernetes-omitted")`.
  - Run `tests/test_deploy_targets.py` and observe failure before the model is updated.

- [x] **Task 2: RED Kubernetes strict policy tests**
  - Update the strict render-check test so current `k3s` strict output fails
    on exactly one unexpected warning: `command-interpolation=1`.
  - Add a test where a synthetic research target allows its only warning code
    and strict mode passes.
  - Run `tests/test_kubernetes_render_check.py` and observe failure before
    policy logic exists.

- [x] **Task 3: Implement target warning policy**
  - Add `expected_warning_codes` to `DeploymentVerification`.
  - Validate codes with the existing lowercase token rule.
  - Add the k3s policy to `templates/deploy-targets/k3s.yaml`.
  - Regenerate the deploy target JSON schema.

- [x] **Task 4: Implement strict unexpected-warning filtering**
  - Compute unexpected warnings as emitted warnings whose code is not declared
    in `target.verification.expected_warning_codes`.
  - In `strict=True`, reject only unexpected warnings.
  - Keep blocker status policy unchanged.
  - Include a compact code breakdown in the strict error message.

- [x] **Task 5: Planning and verification**
  - Update `.planning` and the plan with the M7.D.17 checkpoint.
  - Run focused deploy-target/render-check tests, focused ruff/format/mypy,
    Kubernetes render check in normal and strict modes, aggregate governance,
    expanded M7 pytest, `git diff --check`, and full pre-commit.

## Progress

- Selected this after M7.D.16 because real k3s proof still needs external
  kubeconfig/cluster access, while strict warning policy is locally testable
  and directly prepares the real proof gate.
- RED observed: focused deploy-target/render-check tests failed because the
  deployment target contract rejected `expected_warning_codes`, repository k3s
  had no expected warning policy, and strict render-check still rejected all
  5 warnings.
- GREEN observed: focused deploy-target/render-check tests passed 22 tests;
  focused ruff format check, ruff check, and mypy passed; normal Kubernetes
  render check passed; strict Kubernetes render check now fails only on
  `command-interpolation=1`.
- Final verification observed: expanded M7-focused pytest passed 178 tests;
  aggregate governance passed 5 checks; `git diff --check` and full
  `pre-commit --all-files` passed.
