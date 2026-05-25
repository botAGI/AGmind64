# Kubernetes Proof Artifact Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make real Kubernetes proof commands declare and verify the exact evidence bundle they produce.

**Architecture:** Keep the existing dry-run harness unchanged for cluster execution. Strengthen deployment target governance so any Kubernetes `scripts/kubernetes_dry_run.py --require-cluster` proof command must name its target, write an artifact directory, declare the expected bundle files, and include a matching `--verify-artifact-dir` command. Update the `k3s` target contract to follow that shape.

**Tech Stack:** Python 3.12, Pydantic deployment target contracts, `shlex`, pytest, ruff, mypy, pre-commit.

---

## Scope

This is M7.D.24.A readiness work. It does not pretend to run a real cluster in
this local environment. It makes the real k3s proof command harder to run
without reviewable evidence.

Rules:

- Kubernetes proof commands using `scripts/kubernetes_dry_run.py
  --require-cluster` must include `--target <target-id>`.
- The same proof command must include `--artifact-dir <dir>`.
- The target contract must include a matching
  `scripts/kubernetes_dry_run.py --verify-artifact-dir <dir>` command.
- `verification.artifacts` must list `<dir>/<target>.yaml`,
  `<dir>/<target>.dry-run.json`, `<dir>/summary.json`, and
  `<dir>/checksums.txt`.
- The default `k3s` target uses `local-kubernetes-proof/k3s`, which is ignored
  by the existing `local-*` gitignore rule.

## Files

- Modify: `templates/deploy-targets/k3s.yaml`
- Modify: `agmind/deploy/target_checks.py`
- Modify: `tests/test_deploy_targets.py`
- Modify: `.planning/STATE.md`, `.planning/ROADMAP.md`, `.planning/BACKLOG.md`,
  `.planning/codebase/ARCHITECTURE.md`, `.planning/codebase/INDEX.md`

## Tasks

- [x] **Task 1: RED deploy-target tests**
  - Add a repository test proving `k3s` declares the proof artifact directory,
    matching verifier command, and expected bundle files.
  - Add validator tests proving Kubernetes proof commands without
    `--artifact-dir` or without the matching verifier fail validation.
  - Run focused deploy-target tests and observe failures against the current
    target/check implementation.

- [x] **Task 2: Implement proof artifact governance**
  - Parse verification commands with `shlex.split`.
  - Detect Kubernetes dry-run proof commands that include `--require-cluster`.
  - Validate `--target`, `--artifact-dir`, matching verifier command, and
    expected bundle file declarations.
  - Update `templates/deploy-targets/k3s.yaml` with the real proof bundle
    contract.

- [x] **Task 3: Planning and verification**
  - Update `.planning` and this plan with the M7.D.24.A checkpoint.
  - Run focused deploy-target tests, focused ruff/mypy, deploy target check,
    governance check, expanded M7 pytest, `git diff --check`, and full
    pre-commit.

## Progress

- Selected this step because M7.D.23 can verify copied bundles, but the k3s
  target contract still let an operator run `--require-cluster` without
  declaring where evidence is written or how reviewers should verify it.
- RED observed: focused deploy-target tests failed because the repository
  `k3s` command did not include `--artifact-dir`, the verifier command and
  artifact list were missing, and the validator did not reject incomplete
  Kubernetes proof commands.
- GREEN observed: focused deploy-target tests passed 15 tests after adding the
  proof artifact validator and updating `templates/deploy-targets/k3s.yaml`.
- Final verification observed on 2026-05-24: focused deploy-target tests passed
  15 tests; focused ruff format check, ruff check, and mypy passed; deploy
  target and aggregate governance checks passed; the expanded M7 pytest set
  passed 194 tests; `git diff --check` and full
  `pre-commit --all-files --show-diff-on-failure` passed.
