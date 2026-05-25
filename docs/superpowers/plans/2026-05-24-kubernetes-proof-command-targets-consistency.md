# Kubernetes Proof Command Targets Consistency Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Kubernetes proof artifact verifier reject bundles where `summary.json::proof_command` declares `--target` flags that do not match `summary.json::target_ids`.

**Architecture:** Extend summary self-consistency checks without changing artifact formats. The verifier already checks that `proof-command.txt` matches `summary.json::proof_command` and that `target_ids` matches the target records; this slice parses `--target` values from the proof command argument list and compares them to `target_ids`. It intentionally ignores portable path-sensitive flags such as `--artifact-dir`.

**Tech Stack:** Python list validation, existing Kubernetes dry-run artifact verifier, pytest, GSD planning docs.

---

### Task 1: Reject Proof Command Target Drift

**Files:**
- Modify: `tests/test_kubernetes_dry_run.py`
- Modify: `agmind/services/kubernetes_dry_run.py`

- [x] **Step 1: Write the failing proof-command target consistency test**

Add this test near the other artifact verifier tests:

```python
def test_kubernetes_dry_run_artifact_verifier_rejects_proof_command_target_mismatch(
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
    summary = tmp_path / "summary.json"
    summary_payload = json.loads(summary.read_text(encoding="utf-8"))
    proof_command = list(summary_payload["proof_command"])
    proof_command[proof_command.index("--target") + 1] = "other"
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
    assert "summary.json proof_command targets do not match target_ids" in report.errors
```

- [x] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_kubernetes_dry_run.py::test_kubernetes_dry_run_artifact_verifier_rejects_proof_command_target_mismatch -q`

Expected: FAIL because the current verifier accepts regenerated-checksum bundles where `proof_command` points at a different target than `target_ids`.

- [x] **Step 3: Implement the proof_command target check**

Extend `_verify_summary_consistency()` after deriving `target_ids`:

```python
    proof_command = summary_payload.get("proof_command")
    if isinstance(proof_command, list) and all(isinstance(part, str) for part in proof_command):
        proof_target_ids = _proof_command_target_ids(proof_command)
        if (
            isinstance(target_ids, list)
            and all(isinstance(target_id, str) for target_id in target_ids)
            and proof_target_ids != target_ids
        ):
            errors.append("summary.json proof_command targets do not match target_ids")
```

Add helper:

```python
def _proof_command_target_ids(proof_command: Sequence[str]) -> list[str]:
    target_ids: list[str] = []
    for index, part in enumerate(proof_command):
        if part == "--target" and index + 1 < len(proof_command):
            target_ids.append(proof_command[index + 1])
    return target_ids
```

- [x] **Step 4: Run focused verifier tests**

Run: `.venv/bin/python -m pytest tests/test_kubernetes_dry_run.py::test_kubernetes_dry_run_artifact_verifier_rejects_proof_command_target_mismatch tests/test_kubernetes_dry_run.py::test_kubernetes_dry_run_artifact_verifier_rejects_target_ids_mismatch tests/test_kubernetes_dry_run.py::test_kubernetes_dry_run_artifact_verifier_accepts_valid_bundle -q`

Expected: PASS.

### Task 2: Planning And Verification

**Files:**
- Modify: `.planning/STATE.md`
- Modify: `.planning/BACKLOG.md`
- Modify: `.planning/ROADMAP.md`
- Modify: `.planning/codebase/ARCHITECTURE.md`
- Modify: `.planning/codebase/INDEX.md`
- Modify: `docs/superpowers/plans/2026-05-24-kubernetes-proof-command-targets-consistency.md`

- [x] **Step 1: Record M7.D.24.R-prep checkpoint**

Update planning docs to say verifier rejects proof command `--target` drift from `target_ids`.

- [x] **Step 2: Run verification**

Run focused pytest, full Kubernetes dry-run pytest, workflow/deploy-target/governance scripts, expanded M7 pytest, CLI artifact smoke, `git diff --check`, and full pre-commit.

Expected: all pass. Full pre-commit may skip deploy-target/governance hooks by file globs; run those scripts explicitly and record that fact.

## Verification Log

- RED proof_command target consistency test:
  `.venv/bin/python -m pytest tests/test_kubernetes_dry_run.py::test_kubernetes_dry_run_artifact_verifier_rejects_proof_command_target_mismatch -q`
  failed because the old verifier returned `ok=True` when
  `summary.json::proof_command` and `proof-command.txt` were regenerated with
  `--target other` while `summary.json::target_ids` stayed unchanged.
- GREEN focused verifier slice:
  `.venv/bin/python -m pytest tests/test_kubernetes_dry_run.py::test_kubernetes_dry_run_artifact_verifier_rejects_proof_command_target_mismatch tests/test_kubernetes_dry_run.py::test_kubernetes_dry_run_artifact_verifier_rejects_target_ids_mismatch tests/test_kubernetes_dry_run.py::test_kubernetes_dry_run_artifact_verifier_accepts_valid_bundle -q`
  passed 3 tests.
- Full dry-run test module:
  `.venv/bin/python -m pytest tests/test_kubernetes_dry_run.py -q`
  passed 36 tests.
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
  passed 37 tests.
- Expanded M7 regression:
  `.venv/bin/python -m pytest tests/test_component_contracts.py tests/test_component_update_report.py tests/test_dependency_constraints.py tests/test_deploy_conflicts.py tests/test_deploy_targets.py tests/test_governance_cmd.py tests/test_kubernetes_dry_run.py tests/test_kubernetes_render_check.py tests/test_kubernetes_renderer.py tests/test_model_catalog_unification.py tests/test_proxmox_exporter_ansible.py tests/test_proxmox_exporter_config.py tests/test_proxmox_exporter_service.py tests/test_proxmox_inventory.py tests/test_proxmox_module.py tests/test_targets_cmd.py tests/test_tool_candidates.py tests/test_tools_cmd.py tests/test_cli.py::test_governance_validate_command -q`
  passed 184 tests.
- CLI artifact smoke:
  `.venv/bin/python scripts/kubernetes_dry_run.py --json --target k3s --artifact-dir /tmp/agmind-k8s-proof-command-targets-smoke.FJ1QVk`
  wrote a skipped local proof bundle with 4 expected warnings, and
  `.venv/bin/python scripts/kubernetes_dry_run.py --json --verify-artifact-dir /tmp/agmind-k8s-proof-command-targets-smoke.FJ1QVk`
  accepted the bundle.
- Final workspace gates:
  `git diff --check` passed, and
  `.venv/bin/pre-commit run --all-files --show-diff-on-failure` passed. The
  full pre-commit run skipped deploy-target/governance hooks because their
  file globs were not selected, so those scripts were run explicitly above.
