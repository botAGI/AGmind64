# Kubernetes Proof Target IDs Consistency Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Kubernetes proof artifact verifier reject bundles where `summary.json::target_ids` no longer matches the target objects recorded in `summary.json`.

**Architecture:** Extend the existing summary self-consistency verifier. `target_ids` is a derived aggregate field from the selected target reports, so the verifier should require it to be a string list and compare it to `[target["target_id"] for target in targets]`. This closes another regenerated-checksum aggregate drift case without changing the artifact format.

**Tech Stack:** Python JSON/dict validation, existing Kubernetes dry-run artifact verifier, pytest, GSD planning docs.

---

### Task 1: Reject Target IDs Drift

**Files:**
- Modify: `tests/test_kubernetes_dry_run.py`
- Modify: `agmind/services/kubernetes_dry_run.py`

- [x] **Step 1: Write the failing target_ids consistency test**

Add this test near the other artifact verifier tests:

```python
def test_kubernetes_dry_run_artifact_verifier_rejects_target_ids_mismatch(
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
    summary_payload["target_ids"] = ["other"]
    summary.write_text(json.dumps(summary_payload, indent=2) + "\n", encoding="utf-8")
    summary_digest = hashlib.sha256(summary.read_bytes()).hexdigest()
    checksums = tmp_path / "checksums.txt"
    checksums.write_text(
        "\n".join(
            f"{summary_digest}  summary.json" if line.endswith("  summary.json") else line
            for line in checksums.read_text(encoding="utf-8").splitlines()
        )
        + "\n",
        encoding="utf-8",
    )

    report = verify_kubernetes_dry_run_artifacts(tmp_path)

    assert report.ok is False
    assert "summary.json target_ids do not match target records" in report.errors
```

- [x] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_kubernetes_dry_run.py::test_kubernetes_dry_run_artifact_verifier_rejects_target_ids_mismatch -q`

Expected: FAIL because the current verifier accepts regenerated-checksum `summary.json` drift when only `target_ids` changes.

- [x] **Step 3: Implement the target_ids consistency check**

Extend `_verify_summary_consistency()`:

```python
    target_ids = summary_payload.get("target_ids", [])
    if not isinstance(target_ids, list) or not all(isinstance(target_id, str) for target_id in target_ids):
        errors.append("invalid summary.json: expected target_ids string list")
    derived_target_ids = [
        target.get("target_id", "")
        for target in targets
        if isinstance(target, dict) and isinstance(target.get("target_id", ""), str)
    ]
    if (
        isinstance(target_ids, list)
        and all(isinstance(target_id, str) for target_id in target_ids)
        and target_ids != derived_target_ids
    ):
        errors.append("summary.json target_ids do not match target records")
```

- [x] **Step 4: Run focused verifier tests**

Run: `.venv/bin/python -m pytest tests/test_kubernetes_dry_run.py::test_kubernetes_dry_run_artifact_verifier_rejects_target_ids_mismatch tests/test_kubernetes_dry_run.py::test_kubernetes_dry_run_artifact_verifier_rejects_summary_ok_mismatch tests/test_kubernetes_dry_run.py::test_kubernetes_dry_run_artifact_verifier_accepts_valid_bundle -q`

Expected: PASS.

### Task 2: Planning And Verification

**Files:**
- Modify: `.planning/STATE.md`
- Modify: `.planning/BACKLOG.md`
- Modify: `.planning/ROADMAP.md`
- Modify: `.planning/codebase/ARCHITECTURE.md`
- Modify: `.planning/codebase/INDEX.md`
- Modify: `docs/superpowers/plans/2026-05-24-kubernetes-proof-target-ids-consistency.md`

- [x] **Step 1: Record M7.D.24.Q-prep checkpoint**

Update planning docs to say verifier rejects `summary.json::target_ids` drift from target records.

- [x] **Step 2: Run verification**

Run focused pytest, full Kubernetes dry-run pytest, workflow/deploy-target/governance scripts, expanded M7 pytest, CLI artifact smoke, `git diff --check`, and full pre-commit.

Expected: all pass. Full pre-commit may skip deploy-target/governance hooks by file globs; run those scripts explicitly and record that fact.

## Verification Log

- RED target_ids consistency test:
  `.venv/bin/python -m pytest tests/test_kubernetes_dry_run.py::test_kubernetes_dry_run_artifact_verifier_rejects_target_ids_mismatch -q`
  failed because the old verifier returned `ok=True` when
  `summary.json::target_ids` changed and the summary checksum line was
  regenerated while target records stayed unchanged.
- GREEN focused verifier slice:
  `.venv/bin/python -m pytest tests/test_kubernetes_dry_run.py::test_kubernetes_dry_run_artifact_verifier_rejects_target_ids_mismatch tests/test_kubernetes_dry_run.py::test_kubernetes_dry_run_artifact_verifier_rejects_summary_ok_mismatch tests/test_kubernetes_dry_run.py::test_kubernetes_dry_run_artifact_verifier_accepts_valid_bundle -q`
  passed 3 tests.
- Full dry-run test module:
  `.venv/bin/python -m pytest tests/test_kubernetes_dry_run.py -q`
  passed 35 tests.
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
  passed 36 tests.
- Expanded M7 regression:
  `.venv/bin/python -m pytest tests/test_component_contracts.py tests/test_component_update_report.py tests/test_dependency_constraints.py tests/test_deploy_conflicts.py tests/test_deploy_targets.py tests/test_governance_cmd.py tests/test_kubernetes_dry_run.py tests/test_kubernetes_render_check.py tests/test_kubernetes_renderer.py tests/test_model_catalog_unification.py tests/test_proxmox_exporter_ansible.py tests/test_proxmox_exporter_config.py tests/test_proxmox_exporter_service.py tests/test_proxmox_inventory.py tests/test_proxmox_module.py tests/test_targets_cmd.py tests/test_tool_candidates.py tests/test_tools_cmd.py tests/test_cli.py::test_governance_validate_command -q`
  passed 183 tests.
- CLI artifact smoke:
  `.venv/bin/python scripts/kubernetes_dry_run.py --json --target k3s --artifact-dir /tmp/agmind-k8s-target-ids-smoke.ggqI4d`
  wrote a skipped local proof bundle with 4 expected warnings, and
  `.venv/bin/python scripts/kubernetes_dry_run.py --json --verify-artifact-dir /tmp/agmind-k8s-target-ids-smoke.ggqI4d`
  accepted the bundle.
- Final workspace gates:
  `git diff --check` passed, and
  `.venv/bin/pre-commit run --all-files --show-diff-on-failure` passed. The
  full pre-commit run skipped deploy-target/governance hooks because their
  file globs were not selected, so those scripts were run explicitly above.
