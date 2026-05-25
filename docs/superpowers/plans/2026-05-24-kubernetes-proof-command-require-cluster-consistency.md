# Kubernetes Proof Command Require Cluster Consistency Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Kubernetes proof artifact verifier reject bundles where `summary.json::require_cluster` is true but `summary.json::proof_command` omits `--require-cluster`.

**Architecture:** Extend the existing summary self-consistency verifier. The verifier already checks `target_ids` against target records and `proof_command --target` against `target_ids`; this slice checks the high-stakes live-cluster proof flag so a real proof bundle cannot silently downgrade to a skip-tolerant command while keeping checksums internally consistent.

**Tech Stack:** Python list validation, existing Kubernetes dry-run artifact verifier, pytest, GSD planning docs.

---

### Task 1: Reject Missing Require Cluster Flag

**Files:**
- Modify: `tests/test_kubernetes_dry_run.py`
- Modify: `agmind/services/kubernetes_dry_run.py`

- [x] **Step 1: Write the failing require_cluster proof-command test**

Add this test near the other artifact verifier tests:

```python
def test_kubernetes_dry_run_artifact_verifier_rejects_missing_require_cluster_flag(
    tmp_path: Path,
) -> None:
    from agmind.services.kubernetes_dry_run import (
        CommandResult,
        run_kubernetes_server_dry_run,
        verify_kubernetes_dry_run_artifacts,
    )

    def fake_runner(command: tuple[str, ...], manifest: Path) -> CommandResult:
        return CommandResult(returncode=0, stdout="server dry-run ok", stderr="")

    run_kubernetes_server_dry_run(
        {"k3s-research": _kubernetes_target()},
        kubectl="kubectl",
        require_cluster=True,
        runner=fake_runner,
        artifact_dir=tmp_path,
    )
    summary = tmp_path / "summary.json"
    summary_payload = json.loads(summary.read_text(encoding="utf-8"))
    proof_command = [
        part for part in summary_payload["proof_command"] if part != "--require-cluster"
    ]
    summary_payload["proof_command"] = proof_command
    summary.write_text(json.dumps(summary_payload, indent=2) + "\n", encoding="utf-8")
    proof_command_artifact = tmp_path / "proof-command.txt"
    proof_command_artifact.write_text(shlex.join(proof_command) + "\n", encoding="utf-8")

    summary_digest = hashlib.sha256(summary.read_bytes()).hexdigest()
    proof_digest = hashlib.sha256(proof_command_artifact.read_bytes()).hexdigest()
    checksums = tmp_path / "checksums.txt"
    checksums.write_text(
        "\n".join(
            f"{summary_digest}  summary.json"
            if line.endswith("  summary.json")
            else f"{proof_digest}  proof-command.txt"
            if line.endswith("  proof-command.txt")
            else line
            for line in checksums.read_text(encoding="utf-8").splitlines()
        )
        + "\n",
        encoding="utf-8",
    )

    report = verify_kubernetes_dry_run_artifacts(tmp_path)

    assert report.ok is False
    assert "summary.json proof_command require_cluster flag does not match summary" in report.errors
```

- [x] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_kubernetes_dry_run.py::test_kubernetes_dry_run_artifact_verifier_rejects_missing_require_cluster_flag -q`

Expected: FAIL because the current verifier accepts regenerated-checksum bundles where the live-cluster proof flag was removed from `proof_command`.

- [x] **Step 3: Implement the require_cluster flag check**

Extend `_verify_summary_consistency()` inside the valid proof-command block:

```python
        if ("--require-cluster" in proof_command) != require_cluster:
            errors.append("summary.json proof_command require_cluster flag does not match summary")
```

- [x] **Step 4: Run focused verifier tests**

Run: `.venv/bin/python -m pytest tests/test_kubernetes_dry_run.py::test_kubernetes_dry_run_artifact_verifier_rejects_missing_require_cluster_flag tests/test_kubernetes_dry_run.py::test_kubernetes_dry_run_artifact_verifier_rejects_proof_command_target_mismatch tests/test_kubernetes_dry_run.py::test_kubernetes_dry_run_artifact_verifier_accepts_valid_bundle -q`

Expected: PASS.

### Task 2: Planning And Verification

**Files:**
- Modify: `.planning/STATE.md`
- Modify: `.planning/BACKLOG.md`
- Modify: `.planning/ROADMAP.md`
- Modify: `.planning/codebase/ARCHITECTURE.md`
- Modify: `.planning/codebase/INDEX.md`
- Modify: `docs/superpowers/plans/2026-05-24-kubernetes-proof-command-require-cluster-consistency.md`

- [x] **Step 1: Record M7.D.24.S-prep checkpoint**

Update planning docs to say verifier rejects proof command `--require-cluster` drift from `summary.json::require_cluster`.

- [x] **Step 2: Run verification**

Run focused pytest, full Kubernetes dry-run pytest, workflow/deploy-target/governance scripts, expanded M7 pytest, CLI artifact smoke, `git diff --check`, and full pre-commit.

Expected: all pass. Full pre-commit may skip deploy-target/governance hooks by file globs; run those scripts explicitly and record that fact.

## Verification Log

- RED require_cluster proof-command test:
  `.venv/bin/python -m pytest tests/test_kubernetes_dry_run.py::test_kubernetes_dry_run_artifact_verifier_rejects_missing_require_cluster_flag -q`
  failed because the old verifier returned `ok=True` when
  `summary.json::require_cluster` stayed true but `summary.json::proof_command`
  and checksum-covered `proof-command.txt` omitted `--require-cluster`.
- GREEN focused verifier slice:
  `.venv/bin/python -m pytest tests/test_kubernetes_dry_run.py::test_kubernetes_dry_run_artifact_verifier_rejects_missing_require_cluster_flag tests/test_kubernetes_dry_run.py::test_kubernetes_dry_run_artifact_verifier_rejects_proof_command_target_mismatch tests/test_kubernetes_dry_run.py::test_kubernetes_dry_run_artifact_verifier_accepts_valid_bundle -q`
  passed 3 tests.
- Full dry-run test module:
  `.venv/bin/python -m pytest tests/test_kubernetes_dry_run.py -q`
  passed 37 tests.
- Static checks:
  `.venv/bin/ruff format --check agmind/services/kubernetes_dry_run.py tests/test_kubernetes_dry_run.py`
  initially reported two files needing format; `.venv/bin/ruff format agmind/services/kubernetes_dry_run.py tests/test_kubernetes_dry_run.py`
  reformatted them. The follow-up format check, ruff check, and
  `.venv/bin/mypy agmind/services/kubernetes_dry_run.py` passed. Mypy kept the
  existing unused pyproject section note.
- Focused script and governance checks:
  `.venv/bin/python scripts/kubernetes_proof_workflow_check.py`,
  `.venv/bin/python scripts/deploy_target_check.py`, and
  `.venv/bin/python scripts/governance_check.py` passed; governance reported
  6 checks.
- Focused proof/governance pytest:
  `.venv/bin/python -m pytest tests/test_kubernetes_dry_run.py tests/test_governance_cmd.py::test_governance_check_script_runs -q`
  passed 38 tests.
- Expanded M7 regression:
  `.venv/bin/python -m pytest tests/test_component_contracts.py tests/test_component_update_report.py tests/test_dependency_constraints.py tests/test_deploy_conflicts.py tests/test_deploy_targets.py tests/test_governance_cmd.py tests/test_kubernetes_dry_run.py tests/test_kubernetes_render_check.py tests/test_kubernetes_renderer.py tests/test_model_catalog_unification.py tests/test_proxmox_exporter_ansible.py tests/test_proxmox_exporter_config.py tests/test_proxmox_exporter_service.py tests/test_proxmox_inventory.py tests/test_proxmox_module.py tests/test_targets_cmd.py tests/test_tool_candidates.py tests/test_tools_cmd.py tests/test_cli.py::test_governance_validate_command -q`
  passed 185 tests.
- CLI artifact smoke:
  `.venv/bin/python scripts/kubernetes_dry_run.py --json --target k3s --artifact-dir /tmp/agmind-k8s-require-cluster-smoke.XtQDum`
  wrote a skipped local proof bundle with 4 expected warnings, and
  `.venv/bin/python scripts/kubernetes_dry_run.py --json --verify-artifact-dir /tmp/agmind-k8s-require-cluster-smoke.XtQDum`
  accepted the bundle.
- Final workspace gates:
  `git diff --check` passed, and
  `.venv/bin/pre-commit run --all-files --show-diff-on-failure` passed. The
  full pre-commit run skipped deploy-target/governance hooks because their
  file globs were not selected, so those scripts were run explicitly above.
