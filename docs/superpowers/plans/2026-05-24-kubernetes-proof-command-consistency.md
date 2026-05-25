# Kubernetes Proof Command Consistency Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Kubernetes proof artifact verifier reject bundles where `proof-command.txt` does not match `summary.json::proof_command`.

**Architecture:** Keep the existing bundle shape and checksum format. Add a proof-command consistency verifier next to the run-metadata verifier: it reads `proof_command_path` and `proof_command` from `summary.json`, resolves the artifact by basename inside `artifact_dir`, and compares the file contents to `shlex.join(summary_proof_command) + "\n"`. This catches internally inconsistent copied/uploaded proof bundles even when `checksums.txt` has been regenerated.

**Tech Stack:** Python `shlex`, existing Kubernetes dry-run artifact verifier, pytest, GSD planning docs.

---

### Task 1: Reject Proof Command Drift

**Files:**
- Modify: `tests/test_kubernetes_dry_run.py`
- Modify: `agmind/services/kubernetes_dry_run.py`

- [x] **Step 1: Write the failing proof-command consistency test**

Add this test near the other artifact verifier tests:

```python
def test_kubernetes_dry_run_artifact_verifier_rejects_proof_command_summary_mismatch(
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
        runner=fake_runner,
        artifact_dir=tmp_path,
    )
    proof_command = tmp_path / "proof-command.txt"
    proof_command.write_text("python scripts/kubernetes_dry_run.py --target other\n", encoding="utf-8")
    proof_digest = hashlib.sha256(proof_command.read_bytes()).hexdigest()
    checksums = tmp_path / "checksums.txt"
    checksums.write_text(
        "\n".join(
            f"{proof_digest}  proof-command.txt"
            if line.endswith("  proof-command.txt")
            else line
            for line in checksums.read_text(encoding="utf-8").splitlines()
        )
        + "\n",
        encoding="utf-8",
    )

    report = verify_kubernetes_dry_run_artifacts(tmp_path)

    assert report.ok is False
    assert "proof-command.txt does not match summary.json proof_command" in report.errors
```

- [x] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_kubernetes_dry_run.py::test_kubernetes_dry_run_artifact_verifier_rejects_proof_command_summary_mismatch -q`

Expected: FAIL because the current verifier accepts the drift when the checksum line is regenerated for the changed `proof-command.txt`.

- [x] **Step 3: Implement the consistency verifier**

Add a helper and call it from `verify_kubernetes_dry_run_artifacts()` after required checksum checks:

```python
def _verify_proof_command_artifact(
    artifact_dir: Path,
    summary_payload: dict[str, Any],
) -> list[str]:
    errors: list[str] = []
    proof_command_path_text = summary_payload.get("proof_command_path", "")
    proof_command = summary_payload.get("proof_command")
    if not isinstance(proof_command_path_text, str) or not proof_command_path_text:
        return ["invalid summary.json: missing proof_command_path"]
    if not isinstance(proof_command, list) or not all(isinstance(part, str) for part in proof_command):
        errors.append("invalid summary.json: expected proof_command string list")

    proof_command_path = artifact_dir / Path(proof_command_path_text).name
    if not proof_command_path.exists():
        errors.append(f"missing proof command artifact: {proof_command_path.name}")
        return errors

    if isinstance(proof_command, list) and all(isinstance(part, str) for part in proof_command):
        expected = shlex.join(proof_command) + "\n"
        actual = proof_command_path.read_text(encoding="utf-8")
        if actual != expected:
            errors.append("proof-command.txt does not match summary.json proof_command")
    return errors
```

- [x] **Step 4: Run focused verifier tests**

Run: `.venv/bin/python -m pytest tests/test_kubernetes_dry_run.py::test_kubernetes_dry_run_artifact_verifier_rejects_proof_command_summary_mismatch tests/test_kubernetes_dry_run.py::test_kubernetes_dry_run_artifact_verifier_rejects_corrupt_proof_command tests/test_kubernetes_dry_run.py::test_kubernetes_dry_run_artifact_verifier_accepts_valid_bundle -q`

Expected: PASS.

### Task 2: Planning And Verification

**Files:**
- Modify: `.planning/STATE.md`
- Modify: `.planning/BACKLOG.md`
- Modify: `.planning/ROADMAP.md`
- Modify: `.planning/codebase/ARCHITECTURE.md`
- Modify: `.planning/codebase/INDEX.md`
- Modify: `docs/superpowers/plans/2026-05-24-kubernetes-proof-command-consistency.md`

- [x] **Step 1: Record M7.D.24.N-prep checkpoint**

Update planning docs to say verifier rejects proof-command artifact drift from `summary.json`.

- [x] **Step 2: Run verification**

Run focused pytest, full Kubernetes dry-run pytest, workflow/deploy-target/governance scripts, expanded M7 pytest, CLI artifact smoke, `git diff --check`, and full pre-commit.

Expected: all pass. Full pre-commit may skip deploy-target/governance hooks by file globs; run those scripts explicitly and record that fact.

## Verification Log

- RED proof-command consistency test:
  `.venv/bin/python -m pytest tests/test_kubernetes_dry_run.py::test_kubernetes_dry_run_artifact_verifier_rejects_proof_command_summary_mismatch -q`
  failed because the old verifier returned `ok=True` when `proof-command.txt`
  changed and its checksum line was regenerated while `summary.json::proof_command`
  stayed unchanged.
- GREEN focused verifier slice:
  `.venv/bin/python -m pytest tests/test_kubernetes_dry_run.py::test_kubernetes_dry_run_artifact_verifier_rejects_proof_command_summary_mismatch tests/test_kubernetes_dry_run.py::test_kubernetes_dry_run_artifact_verifier_rejects_corrupt_proof_command tests/test_kubernetes_dry_run.py::test_kubernetes_dry_run_artifact_verifier_accepts_valid_bundle -q`
  passed 3 tests.
- Full dry-run test module:
  `.venv/bin/python -m pytest tests/test_kubernetes_dry_run.py -q`
  passed 32 tests.
- Static checks:
  `.venv/bin/ruff format --check agmind/services/kubernetes_dry_run.py tests/test_kubernetes_dry_run.py`,
  `.venv/bin/ruff check agmind/services/kubernetes_dry_run.py tests/test_kubernetes_dry_run.py`,
  and `.venv/bin/mypy agmind/services/kubernetes_dry_run.py` passed. Mypy kept
  the existing unused pyproject section note.
- Focused script and governance checks:
  `.venv/bin/python scripts/kubernetes_proof_workflow_check.py`,
  `.venv/bin/python scripts/deploy_target_check.py`, and
  `.venv/bin/python scripts/governance_check.py` passed; governance reported
  6 checks.
- Focused proof/governance pytest:
  `.venv/bin/python -m pytest tests/test_kubernetes_dry_run.py tests/test_governance_cmd.py::test_governance_check_script_runs -q`
  passed 33 tests.
- Expanded M7 regression:
  `.venv/bin/python -m pytest tests/test_component_contracts.py tests/test_component_update_report.py tests/test_dependency_constraints.py tests/test_deploy_conflicts.py tests/test_deploy_targets.py tests/test_governance_cmd.py tests/test_kubernetes_dry_run.py tests/test_kubernetes_render_check.py tests/test_kubernetes_renderer.py tests/test_model_catalog_unification.py tests/test_proxmox_exporter_ansible.py tests/test_proxmox_exporter_config.py tests/test_proxmox_exporter_service.py tests/test_proxmox_inventory.py tests/test_proxmox_module.py tests/test_targets_cmd.py tests/test_tool_candidates.py tests/test_tools_cmd.py tests/test_cli.py::test_governance_validate_command -q`
  passed 180 tests.
- CLI artifact smoke:
  `.venv/bin/python scripts/kubernetes_dry_run.py --json --target k3s --artifact-dir /tmp/agmind-k8s-proof-command-smoke.GYbIwJ`
  wrote a skipped local proof bundle with 4 expected warnings, and
  `.venv/bin/python scripts/kubernetes_dry_run.py --json --verify-artifact-dir /tmp/agmind-k8s-proof-command-smoke.GYbIwJ`
  accepted the bundle.
- Final workspace gates:
  `git diff --check` passed, and
  `.venv/bin/pre-commit run --all-files --show-diff-on-failure` passed. The
  full pre-commit run skipped deploy-target/governance hooks because their
  file globs were not selected, so those scripts were run explicitly above.
