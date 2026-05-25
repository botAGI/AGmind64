# Kubernetes Proof Target Report Consistency Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Kubernetes proof artifact verifier reject bundles where a target `<id>.dry-run.json` report no longer matches the corresponding target object in `summary.json`.

**Architecture:** Keep copied/uploaded bundle verification basename-based so bundles remain portable across artifact directories. Add a target report consistency verifier beside the proof-command and run-metadata verifiers: for each summary target with `report_path`, read the report artifact by basename, parse JSON, and compare the loaded object with the target object recorded in `summary.json`. This catches regenerated-checksum drift in target evidence files.

**Tech Stack:** Python JSON verification, existing Kubernetes dry-run artifact verifier, pytest, GSD planning docs.

---

### Task 1: Reject Target Report Drift

**Files:**
- Modify: `tests/test_kubernetes_dry_run.py`
- Modify: `agmind/services/kubernetes_dry_run.py`

- [x] **Step 1: Write the failing target-report consistency test**

Add this test near the other artifact verifier tests:

```python
def test_kubernetes_dry_run_artifact_verifier_rejects_target_report_summary_mismatch(
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
    target_report = tmp_path / "k3s-research.dry-run.json"
    target_payload = json.loads(target_report.read_text(encoding="utf-8"))
    target_payload["status"] = "failed"
    target_report.write_text(json.dumps(target_payload, indent=2) + "\n", encoding="utf-8")
    target_digest = hashlib.sha256(target_report.read_bytes()).hexdigest()
    checksums = tmp_path / "checksums.txt"
    checksums.write_text(
        "\n".join(
            f"{target_digest}  k3s-research.dry-run.json"
            if line.endswith("  k3s-research.dry-run.json")
            else line
            for line in checksums.read_text(encoding="utf-8").splitlines()
        )
        + "\n",
        encoding="utf-8",
    )

    report = verify_kubernetes_dry_run_artifacts(tmp_path)

    assert report.ok is False
    assert "k3s-research: target report artifact does not match summary.json target" in report.errors
```

- [x] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_kubernetes_dry_run.py::test_kubernetes_dry_run_artifact_verifier_rejects_target_report_summary_mismatch -q`

Expected: FAIL because the current verifier accepts target report drift when `checksums.txt` is regenerated for the changed report.

- [x] **Step 3: Implement the target report consistency verifier**

Add a helper and call it from `verify_kubernetes_dry_run_artifacts()` after proof-command/run-metadata checks:

```python
def _verify_target_report_artifacts(
    artifact_dir: Path,
    summary_payload: dict[str, Any],
) -> list[str]:
    errors: list[str] = []
    targets = summary_payload.get("targets", [])
    if not isinstance(targets, list):
        return []
    for target in targets:
        if not isinstance(target, dict):
            continue
        target_id = str(target.get("target_id", "<unknown>"))
        report_path_text = target.get("report_path", "")
        if not isinstance(report_path_text, str) or not report_path_text:
            continue
        report_path = artifact_dir / Path(report_path_text).name
        if not report_path.exists():
            errors.append(f"{target_id}: missing target report artifact: {report_path.name}")
            continue
        try:
            loaded = json.loads(report_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            errors.append(f"{target_id}: invalid target report artifact {report_path.name}: {exc}")
            continue
        if not isinstance(loaded, dict):
            errors.append(f"{target_id}: invalid target report artifact {report_path.name}: expected object")
            continue
        if loaded != target:
            errors.append(f"{target_id}: target report artifact does not match summary.json target")
    return errors
```

- [x] **Step 4: Run focused verifier tests**

Run: `.venv/bin/python -m pytest tests/test_kubernetes_dry_run.py::test_kubernetes_dry_run_artifact_verifier_rejects_target_report_summary_mismatch tests/test_kubernetes_dry_run.py::test_kubernetes_dry_run_artifact_verifier_rejects_proof_command_summary_mismatch tests/test_kubernetes_dry_run.py::test_kubernetes_dry_run_artifact_verifier_accepts_valid_bundle -q`

Expected: PASS.

### Task 2: Planning And Verification

**Files:**
- Modify: `.planning/STATE.md`
- Modify: `.planning/BACKLOG.md`
- Modify: `.planning/ROADMAP.md`
- Modify: `.planning/codebase/ARCHITECTURE.md`
- Modify: `.planning/codebase/INDEX.md`
- Modify: `docs/superpowers/plans/2026-05-24-kubernetes-proof-target-report-consistency.md`

- [x] **Step 1: Record M7.D.24.O-prep checkpoint**

Update planning docs to say verifier rejects target dry-run report drift from `summary.json`.

- [x] **Step 2: Run verification**

Run focused pytest, full Kubernetes dry-run pytest, workflow/deploy-target/governance scripts, expanded M7 pytest, CLI artifact smoke, `git diff --check`, and full pre-commit.

Expected: all pass. Full pre-commit may skip deploy-target/governance hooks by file globs; run those scripts explicitly and record that fact.

## Verification Log

- RED target-report consistency test:
  `.venv/bin/python -m pytest tests/test_kubernetes_dry_run.py::test_kubernetes_dry_run_artifact_verifier_rejects_target_report_summary_mismatch -q`
  failed because the old verifier returned `ok=True` when
  `k3s-research.dry-run.json` changed and its checksum line was regenerated
  while the corresponding `summary.json` target stayed unchanged.
- GREEN focused verifier slice:
  `.venv/bin/python -m pytest tests/test_kubernetes_dry_run.py::test_kubernetes_dry_run_artifact_verifier_rejects_target_report_summary_mismatch tests/test_kubernetes_dry_run.py::test_kubernetes_dry_run_artifact_verifier_rejects_proof_command_summary_mismatch tests/test_kubernetes_dry_run.py::test_kubernetes_dry_run_artifact_verifier_accepts_valid_bundle -q`
  passed 3 tests.
- Full dry-run test module:
  `.venv/bin/python -m pytest tests/test_kubernetes_dry_run.py -q`
  passed 33 tests.
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
  passed 34 tests.
- Expanded M7 regression:
  `.venv/bin/python -m pytest tests/test_component_contracts.py tests/test_component_update_report.py tests/test_dependency_constraints.py tests/test_deploy_conflicts.py tests/test_deploy_targets.py tests/test_governance_cmd.py tests/test_kubernetes_dry_run.py tests/test_kubernetes_render_check.py tests/test_kubernetes_renderer.py tests/test_model_catalog_unification.py tests/test_proxmox_exporter_ansible.py tests/test_proxmox_exporter_config.py tests/test_proxmox_exporter_service.py tests/test_proxmox_inventory.py tests/test_proxmox_module.py tests/test_targets_cmd.py tests/test_tool_candidates.py tests/test_tools_cmd.py tests/test_cli.py::test_governance_validate_command -q`
  passed 181 tests.
- CLI artifact smoke:
  `.venv/bin/python scripts/kubernetes_dry_run.py --json --target k3s --artifact-dir /tmp/agmind-k8s-target-report-smoke.RwFqFS`
  wrote a skipped local proof bundle with 4 expected warnings, and
  `.venv/bin/python scripts/kubernetes_dry_run.py --json --verify-artifact-dir /tmp/agmind-k8s-target-report-smoke.RwFqFS`
  accepted the bundle.
- Final workspace gates:
  `git diff --check` passed, and
  `.venv/bin/pre-commit run --all-files --show-diff-on-failure` passed. The
  full pre-commit run skipped deploy-target/governance hooks because their
  file globs were not selected, so those scripts were run explicitly above.
