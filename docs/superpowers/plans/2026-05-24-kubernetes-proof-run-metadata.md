# Kubernetes Proof Run Metadata Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add checksum-verified CI/local provenance metadata to Kubernetes proof bundles before real k3s evidence is captured.

**Architecture:** The Kubernetes dry-run harness writes an allowlisted `run-metadata.json` file into `--artifact-dir`, records the same metadata path/payload in `summary.json`, and includes the file in `checksums.txt`. The k3s deployment target declares the artifact, the manual proof workflow uploads it, and the workflow drift guard already enforces upload-scoped target artifacts.

**Tech Stack:** Python dataclasses/JSON, pytest, GitHub Actions YAML, deployment target YAML contracts.

---

### Task 1: Bundle Run Metadata

**Files:**
- Modify: `tests/test_kubernetes_dry_run.py`
- Modify: `agmind/services/kubernetes_dry_run.py`

- [x] **Step 1: Write the failing metadata artifact test**

```python
def test_kubernetes_dry_run_artifact_records_run_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from agmind.services.kubernetes_dry_run import (
        CommandResult,
        run_kubernetes_server_dry_run,
        verify_kubernetes_dry_run_artifacts,
    )

    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_WORKFLOW", "Kubernetes Proof")
    monkeypatch.setenv("GITHUB_RUN_ID", "12345")
    monkeypatch.setenv("GITHUB_SHA", "abc123")
    monkeypatch.setenv("RUNNER_NAME", "strix-k3s")
    monkeypatch.setenv("RUNNER_OS", "Linux")
    monkeypatch.setenv("RUNNER_ARCH", "X64")

    def fake_runner(command: tuple[str, ...], manifest: Path) -> CommandResult:
        return CommandResult(returncode=0, stdout="server dry-run ok", stderr="")

    run_kubernetes_server_dry_run(
        {"k3s-research": _kubernetes_target()},
        kubectl="kubectl",
        runner=fake_runner,
        artifact_dir=tmp_path,
    )

    metadata_path = tmp_path / "run-metadata.json"
    summary_payload = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
    metadata_payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    checksum_lines = (tmp_path / "checksums.txt").read_text(encoding="utf-8").splitlines()

    assert metadata_path.exists()
    assert summary_payload["run_metadata_path"] == str(metadata_path)
    assert summary_payload["run_metadata"] == metadata_payload
    assert metadata_payload["source"] == "github-actions"
    assert metadata_payload["github_workflow"] == "Kubernetes Proof"
    assert metadata_payload["github_run_id"] == "12345"
    assert metadata_payload["github_sha"] == "abc123"
    assert metadata_payload["runner_name"] == "strix-k3s"
    assert any(line.endswith("  run-metadata.json") for line in checksum_lines)
    assert verify_kubernetes_dry_run_artifacts(tmp_path).ok is True
```

- [x] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_kubernetes_dry_run.py::test_kubernetes_dry_run_artifact_records_run_metadata -q`

Expected: FAIL because `run-metadata.json` and summary fields do not exist yet.

- [x] **Step 3: Implement metadata generation and verification**

Add a small allowlisted metadata helper in `agmind/services/kubernetes_dry_run.py`, store the payload/path on `KubernetesDryRunReport`, write `run-metadata.json` before `summary.json`, include it in checksum generation, and make artifact verification require the summary metadata file to exist and match `summary.json`.

- [x] **Step 4: Run focused metadata tests**

Run: `.venv/bin/python -m pytest tests/test_kubernetes_dry_run.py::test_kubernetes_dry_run_artifact_records_run_metadata tests/test_kubernetes_dry_run.py::test_kubernetes_dry_run_artifact_verifier_accepts_valid_bundle -q`

Expected: PASS.

### Task 2: Contract And Workflow Upload

**Files:**
- Modify: `tests/test_kubernetes_dry_run.py`
- Modify: `tests/test_governance_cmd.py`
- Modify: `templates/deploy-targets/k3s.yaml`
- Modify: `.github/workflows/kubernetes-proof.yml`
- Modify: `agmind/deploy/target_checks.py`

- [x] **Step 1: Write failing contract/upload assertions**

Extend workflow tests to require `local-kubernetes-proof/k3s/run-metadata.json` in the k3s upload artifact list and add a workflow guard test that removes only that upload path while leaving other references intact.

- [x] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_kubernetes_dry_run.py::test_ci_has_manual_kubernetes_proof_artifact_workflow tests/test_governance_cmd.py::test_kubernetes_proof_workflow_guard_rejects_missing_declared_artifact_upload -q`

Expected: FAIL because the contract/workflow do not declare/upload `run-metadata.json` yet.

- [x] **Step 3: Update contract and workflow**

Add `local-kubernetes-proof/k3s/run-metadata.json` to the k3s target artifacts and upload path. Add `run-metadata.json` to `_expected_kubernetes_proof_artifacts()` so future Kubernetes proof contracts require it.

- [x] **Step 4: Run focused contract tests**

Run: `.venv/bin/python -m pytest tests/test_kubernetes_dry_run.py::test_ci_has_manual_kubernetes_proof_artifact_workflow tests/test_governance_cmd.py::test_kubernetes_proof_workflow_guard_rejects_missing_declared_artifact_upload tests/test_kubernetes_dry_run.py::test_kubernetes_proof_workflow_check_script_runs -q`

Expected: PASS.

### Task 3: Planning And Verification

**Files:**
- Modify: `.planning/STATE.md`
- Modify: `.planning/BACKLOG.md`
- Modify: `.planning/ROADMAP.md`
- Modify: `.planning/codebase/ARCHITECTURE.md`
- Modify: `.planning/codebase/INDEX.md`
- Modify: `docs/superpowers/plans/2026-05-24-kubernetes-proof-run-metadata.md`

- [x] **Step 1: Record M7.D.24.J-prep checkpoint**

Update planning docs to say Kubernetes proof bundles now include checksum-verified `run-metadata.json` with allowlisted GitHub/runner provenance.

- [x] **Step 2: Run verification**

Run focused pytest, `scripts/kubernetes_proof_workflow_check.py`, `scripts/governance_check.py`, expanded M7 pytest, `git diff --check`, and pre-commit.

Expected: all pass.

## Verification Log

- RED metadata artifact test:
  `.venv/bin/python -m pytest tests/test_kubernetes_dry_run.py::test_kubernetes_dry_run_artifact_records_run_metadata -q`
  failed because `run-metadata.json` did not exist.
- GREEN metadata slice:
  `.venv/bin/python -m pytest tests/test_kubernetes_dry_run.py::test_kubernetes_dry_run_artifact_records_run_metadata tests/test_kubernetes_dry_run.py::test_kubernetes_dry_run_artifact_verifier_accepts_valid_bundle -q`
  passed 2 tests.
- RED workflow upload assertion:
  `.venv/bin/python -m pytest tests/test_kubernetes_dry_run.py::test_ci_has_manual_kubernetes_proof_artifact_workflow -q`
  failed because `local-kubernetes-proof/k3s/run-metadata.json` was absent from the workflow.
- GREEN focused metadata/contract slice:
  `.venv/bin/python -m pytest tests/test_kubernetes_dry_run.py::test_kubernetes_dry_run_artifact_records_run_metadata tests/test_kubernetes_dry_run.py::test_kubernetes_dry_run_artifact_verifier_accepts_valid_bundle tests/test_kubernetes_dry_run.py::test_ci_has_manual_kubernetes_proof_artifact_workflow tests/test_governance_cmd.py::test_kubernetes_proof_workflow_guard_rejects_missing_declared_artifact_upload tests/test_kubernetes_dry_run.py::test_kubernetes_proof_workflow_check_script_runs -q`
  passed 5 tests.
- Full dry-run test module:
  `.venv/bin/python -m pytest tests/test_kubernetes_dry_run.py -q`
  passed 28 tests.
- Focused deploy-target expectation repair after expanded regression exposed
  stale 5-file bundle assertions:
  `.venv/bin/python -m pytest tests/test_deploy_targets.py::test_repository_k3s_declares_real_proof_artifact_bundle tests/test_deploy_targets.py::test_validate_deploy_targets_rejects_kubernetes_proof_without_bundle_artifacts tests/test_deploy_targets.py::test_validate_deploy_targets_rejects_kubernetes_proof_without_bundle_verifier -q`
  passed 3 tests.
- Contract scripts:
  `.venv/bin/python scripts/kubernetes_proof_workflow_check.py`,
  `.venv/bin/python scripts/deploy_target_check.py`, and
  `.venv/bin/python scripts/governance_check.py` passed.
- CLI artifact smoke:
  `.venv/bin/python scripts/kubernetes_dry_run.py --json --target k3s --artifact-dir /tmp/agmind-k8s-run-metadata-smoke.FOQTy6`
  wrote `run_metadata_path`, and
  `.venv/bin/python scripts/kubernetes_dry_run.py --json --verify-artifact-dir /tmp/agmind-k8s-run-metadata-smoke.FOQTy6`
  reported `ok: true` with `run-metadata.json` verified.
- Expanded M7 regression:
  `.venv/bin/python -m pytest tests/test_component_contracts.py tests/test_component_update_report.py tests/test_dependency_constraints.py tests/test_deploy_conflicts.py tests/test_deploy_targets.py tests/test_governance_cmd.py tests/test_kubernetes_dry_run.py tests/test_kubernetes_render_check.py tests/test_kubernetes_renderer.py tests/test_model_catalog_unification.py tests/test_proxmox_exporter_ansible.py tests/test_proxmox_exporter_config.py tests/test_proxmox_exporter_service.py tests/test_proxmox_inventory.py tests/test_proxmox_module.py tests/test_targets_cmd.py tests/test_tool_candidates.py tests/test_tools_cmd.py tests/test_cli.py::test_governance_validate_command -q`
  passed 176 tests after updating the stale deploy-target artifact expectations.
- Final whitespace/hook gate:
  `git diff --check` passed, and
  `.venv/bin/pre-commit run --all-files --show-diff-on-failure` passed.
