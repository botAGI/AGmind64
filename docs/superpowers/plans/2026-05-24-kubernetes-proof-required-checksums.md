# Kubernetes Proof Required Checksums Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Kubernetes proof artifact verifier reject bundles where required evidence files exist but are not covered by `checksums.txt`.

**Architecture:** Keep checksum generation unchanged. Strengthen `verify_kubernetes_dry_run_artifacts()` so it derives required bundle members from `summary.json` (`summary.json`, `proof_command_path`, `run_metadata_path`, and each target's `manifest_path`/`report_path`) and verifies every required member appears in the parsed checksum file. This closes a silent-integrity gap without changing the manual proof workflow shape.

**Tech Stack:** Python JSON/path validation, pytest, existing Kubernetes dry-run artifact verifier.

---

### Task 1: Required Checksum Coverage

**Files:**
- Modify: `tests/test_kubernetes_dry_run.py`
- Modify: `agmind/services/kubernetes_dry_run.py`

- [x] **Step 1: Write the failing missing-checksum test**

```python
def test_kubernetes_dry_run_artifact_verifier_rejects_missing_required_checksum(
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
    checksums = tmp_path / "checksums.txt"
    checksums.write_text(
        "\n".join(
            line
            for line in checksums.read_text(encoding="utf-8").splitlines()
            if not line.endswith("  run-metadata.json")
        )
        + "\n",
        encoding="utf-8",
    )

    report = verify_kubernetes_dry_run_artifacts(tmp_path)

    assert report.ok is False
    assert "missing checksum entry for required artifact: run-metadata.json" in report.errors
```

- [x] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_kubernetes_dry_run.py::test_kubernetes_dry_run_artifact_verifier_rejects_missing_required_checksum -q`

Expected: FAIL because the current verifier accepts existing metadata that is missing from `checksums.txt`.

- [x] **Step 3: Implement required checksum coverage**

Add helper logic in `agmind/services/kubernetes_dry_run.py`:

```python
def _verify_required_checksum_entries(
    artifact_dir: Path,
    summary_payload: dict[str, Any],
    checksummed_paths: set[str],
) -> list[str]:
    required = {"summary.json"}
    for key in ("proof_command_path", "run_metadata_path"):
        path_text = summary_payload.get(key, "")
        if isinstance(path_text, str) and path_text:
            required.add(Path(path_text).name)
    targets = summary_payload.get("targets", [])
    if isinstance(targets, list):
        for target in targets:
            if not isinstance(target, dict):
                continue
            for key in ("manifest_path", "report_path"):
                path_text = target.get(key, "")
                if isinstance(path_text, str) and path_text:
                    required.add(Path(path_text).name)
    return [
        f"missing checksum entry for required artifact: {relative_path}"
        for relative_path in sorted(required)
        if relative_path not in checksummed_paths and (artifact_dir / relative_path).exists()
    ]
```

Call it after `summary.json` is parsed and after checksum entries are collected.

- [x] **Step 4: Run focused verifier tests**

Run: `.venv/bin/python -m pytest tests/test_kubernetes_dry_run.py::test_kubernetes_dry_run_artifact_verifier_rejects_missing_required_checksum tests/test_kubernetes_dry_run.py::test_kubernetes_dry_run_artifact_verifier_accepts_valid_bundle tests/test_kubernetes_dry_run.py::test_kubernetes_dry_run_artifact_records_run_metadata -q`

Expected: PASS.

### Task 2: Planning And Verification

**Files:**
- Modify: `.planning/STATE.md`
- Modify: `.planning/BACKLOG.md`
- Modify: `.planning/ROADMAP.md`
- Modify: `.planning/codebase/ARCHITECTURE.md`
- Modify: `.planning/codebase/INDEX.md`
- Modify: `docs/superpowers/plans/2026-05-24-kubernetes-proof-required-checksums.md`

- [x] **Step 1: Record M7.D.24.K-prep checkpoint**

Update planning docs to say verifier now rejects bundles where required evidence files are not listed in `checksums.txt`.

- [x] **Step 2: Run verification**

Run focused pytest, workflow/deploy-target/governance scripts, expanded M7 pytest, `git diff --check`, and full pre-commit.

Expected: all pass.

## Verification Log

- RED missing-checksum test:
  `.venv/bin/python -m pytest tests/test_kubernetes_dry_run.py::test_kubernetes_dry_run_artifact_verifier_rejects_missing_required_checksum -q`
  failed because the old verifier returned `ok=True` after the
  `run-metadata.json` checksum entry was removed.
- GREEN focused verifier slice:
  `.venv/bin/python -m pytest tests/test_kubernetes_dry_run.py::test_kubernetes_dry_run_artifact_verifier_rejects_missing_required_checksum tests/test_kubernetes_dry_run.py::test_kubernetes_dry_run_artifact_verifier_accepts_valid_bundle tests/test_kubernetes_dry_run.py::test_kubernetes_dry_run_artifact_records_run_metadata -q`
  passed 3 tests.
- Full dry-run test module:
  `.venv/bin/python -m pytest tests/test_kubernetes_dry_run.py -q`
  passed 29 tests.
- Contract scripts:
  `.venv/bin/python scripts/kubernetes_proof_workflow_check.py`,
  `.venv/bin/python scripts/deploy_target_check.py`, and
  `.venv/bin/python scripts/governance_check.py` passed.
- Focused proof/governance slice:
  `.venv/bin/python -m pytest tests/test_kubernetes_dry_run.py tests/test_governance_cmd.py::test_governance_check_script_runs -q`
  passed 30 tests.
- Expanded M7 regression:
  `.venv/bin/python -m pytest tests/test_component_contracts.py tests/test_component_update_report.py tests/test_dependency_constraints.py tests/test_deploy_conflicts.py tests/test_deploy_targets.py tests/test_governance_cmd.py tests/test_kubernetes_dry_run.py tests/test_kubernetes_render_check.py tests/test_kubernetes_renderer.py tests/test_model_catalog_unification.py tests/test_proxmox_exporter_ansible.py tests/test_proxmox_exporter_config.py tests/test_proxmox_exporter_service.py tests/test_proxmox_inventory.py tests/test_proxmox_module.py tests/test_targets_cmd.py tests/test_tool_candidates.py tests/test_tools_cmd.py tests/test_cli.py::test_governance_validate_command -q`
  passed 177 tests.
- CLI artifact smoke:
  `.venv/bin/python scripts/kubernetes_dry_run.py --json --target k3s --artifact-dir /tmp/agmind-k8s-required-checksums-smoke.DWbL2W`
  wrote the bundle, and
  `.venv/bin/python scripts/kubernetes_dry_run.py --json --verify-artifact-dir /tmp/agmind-k8s-required-checksums-smoke.DWbL2W`
  reported `ok: true`.
- Final whitespace/hook gate:
  `git diff --check` passed, and
  `.venv/bin/pre-commit run --all-files --show-diff-on-failure` passed.
