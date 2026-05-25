# Kubernetes Proof Checksum Path Containment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Kubernetes proof artifact verifier reject `checksums.txt` entries that point outside the artifact directory.

**Architecture:** Keep checksum file format unchanged for valid bundles. Add a path containment guard inside `_verify_checksum_file()` before reading a checksummed member: relative paths must stay within `artifact_dir`, must not be absolute, and must not contain parent-directory traversal. This prevents uploaded or copied bundles from causing verifier reads outside the proof bundle.

**Tech Stack:** Python `pathlib` validation, pytest, existing Kubernetes dry-run artifact verifier.

---

### Task 1: Reject Escaping Checksum Paths

**Files:**
- Modify: `tests/test_kubernetes_dry_run.py`
- Modify: `agmind/services/kubernetes_dry_run.py`

- [x] **Step 1: Write the failing path-escape test**

```python
def test_kubernetes_dry_run_artifact_verifier_rejects_checksum_path_escape(
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
    outside = tmp_path.parent / "outside-proof-file.txt"
    outside.write_text("outside\n", encoding="utf-8")
    outside_digest = hashlib.sha256(outside.read_bytes()).hexdigest()
    checksums = tmp_path / "checksums.txt"
    checksums.write_text(
        checksums.read_text(encoding="utf-8") + f"{outside_digest}  ../{outside.name}\n",
        encoding="utf-8",
    )

    report = verify_kubernetes_dry_run_artifacts(tmp_path)

    assert report.ok is False
    assert any("checksum path escapes artifact directory: ../outside-proof-file.txt" in error for error in report.errors)
```

- [x] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_kubernetes_dry_run.py::test_kubernetes_dry_run_artifact_verifier_rejects_checksum_path_escape -q`

Expected: FAIL because the current verifier accepts the escaping checksum entry when the outside file exists and the digest matches.

- [x] **Step 3: Implement path containment guard**

Add a helper:

```python
def _checksum_member_path(artifact_dir: Path, relative_path: str) -> tuple[Path | None, str]:
    parsed = Path(relative_path)
    if parsed.is_absolute() or ".." in parsed.parts:
        return None, f"checksum path escapes artifact directory: {relative_path}"
    return artifact_dir / parsed, ""
```

Use it in `_verify_checksum_file()` before checking `exists()` or hashing.

- [x] **Step 4: Run focused verifier tests**

Run: `.venv/bin/python -m pytest tests/test_kubernetes_dry_run.py::test_kubernetes_dry_run_artifact_verifier_rejects_checksum_path_escape tests/test_kubernetes_dry_run.py::test_kubernetes_dry_run_artifact_verifier_rejects_missing_required_artifact tests/test_kubernetes_dry_run.py::test_kubernetes_dry_run_artifact_verifier_accepts_valid_bundle -q`

Expected: PASS.

### Task 2: Planning And Verification

**Files:**
- Modify: `.planning/STATE.md`
- Modify: `.planning/BACKLOG.md`
- Modify: `.planning/ROADMAP.md`
- Modify: `.planning/codebase/ARCHITECTURE.md`
- Modify: `.planning/codebase/INDEX.md`
- Modify: `docs/superpowers/plans/2026-05-24-kubernetes-proof-checksum-path-containment.md`

- [x] **Step 1: Record M7.D.24.M-prep checkpoint**

Update planning docs to say verifier rejects checksum entries that escape the artifact directory.

- [x] **Step 2: Run verification**

Run focused pytest, workflow/deploy-target/governance scripts, expanded M7 pytest, CLI artifact smoke, `git diff --check`, and full pre-commit.

Expected: all pass.

## Verification Log

- RED path-escape test:
  `.venv/bin/python -m pytest tests/test_kubernetes_dry_run.py::test_kubernetes_dry_run_artifact_verifier_rejects_checksum_path_escape -q`
  failed because the old verifier returned `ok=True` for a checksum entry
  pointing to `../outside-proof-file.txt` when that outside file existed and
  the digest matched.
- GREEN focused verifier slice:
  `.venv/bin/python -m pytest tests/test_kubernetes_dry_run.py::test_kubernetes_dry_run_artifact_verifier_rejects_checksum_path_escape tests/test_kubernetes_dry_run.py::test_kubernetes_dry_run_artifact_verifier_rejects_missing_required_artifact tests/test_kubernetes_dry_run.py::test_kubernetes_dry_run_artifact_verifier_accepts_valid_bundle -q`
  passed 3 tests.
- Full dry-run test module:
  `.venv/bin/python -m pytest tests/test_kubernetes_dry_run.py -q`
  passed 31 tests.
- Focused script and governance checks:
  `.venv/bin/python scripts/kubernetes_proof_workflow_check.py`,
  `.venv/bin/python scripts/deploy_target_check.py`, and
  `.venv/bin/python scripts/governance_check.py` passed; governance reported
  6 checks.
- Focused proof/governance pytest:
  `.venv/bin/python -m pytest tests/test_kubernetes_dry_run.py tests/test_governance_cmd.py::test_governance_check_script_runs -q`
  passed 32 tests.
- Expanded M7 regression:
  `.venv/bin/python -m pytest tests/test_component_contracts.py tests/test_component_update_report.py tests/test_dependency_constraints.py tests/test_deploy_conflicts.py tests/test_deploy_targets.py tests/test_governance_cmd.py tests/test_kubernetes_dry_run.py tests/test_kubernetes_render_check.py tests/test_kubernetes_renderer.py tests/test_model_catalog_unification.py tests/test_proxmox_exporter_ansible.py tests/test_proxmox_exporter_config.py tests/test_proxmox_exporter_service.py tests/test_proxmox_inventory.py tests/test_proxmox_module.py tests/test_targets_cmd.py tests/test_tool_candidates.py tests/test_tools_cmd.py tests/test_cli.py::test_governance_validate_command -q`
  passed 179 tests.
- CLI artifact smoke:
  `.venv/bin/python scripts/kubernetes_dry_run.py --json --target k3s --artifact-dir /tmp/agmind-k8s-path-containment-smoke.xgVmuy`
  wrote a skipped local proof bundle with 4 expected warnings, and
  `.venv/bin/python scripts/kubernetes_dry_run.py --json --verify-artifact-dir /tmp/agmind-k8s-path-containment-smoke.xgVmuy`
  accepted the bundle.
- Final workspace gates:
  `git diff --check` passed, and
  `.venv/bin/pre-commit run --all-files --show-diff-on-failure` passed. The
  full pre-commit run skipped deploy-target/governance hooks because their
  file globs were not selected, so those scripts were run explicitly above.
