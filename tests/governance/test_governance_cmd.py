from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.backend_any

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_governance_check_api_runs_all_m7_gates() -> None:
    from agmind.governance import DEFAULT_CHECKS, run_governance_checks

    report = run_governance_checks()

    assert DEFAULT_CHECKS == (
        "docs-mirror",
        "components",
        "deploy-targets",
        "tool-candidates",
        "constraints",
        "topology",
        "kubernetes-render",
        "kubernetes-proof-workflow",
        "digest-pins",
    )
    assert report.ok is True
    assert tuple(result.name for result in report.results) == DEFAULT_CHECKS
    assert all(result.returncode == 0 for result in report.results)


def test_governance_cli_text_output(capsys: pytest.CaptureFixture[str]) -> None:
    from agmind.cli import governance_cmd

    rc = governance_cmd.cmd_validate()

    assert rc == 0
    out = capsys.readouterr().out
    assert "components: OK" in out
    assert "deploy-targets: OK" in out
    assert "tool-candidates: OK" in out
    assert "constraints: OK" in out
    assert "topology: OK" in out
    assert "kubernetes-render: OK" in out
    assert "kubernetes-proof-workflow: OK" in out
    assert "docs-mirror: OK" in out
    assert "governance OK: 9 checks (status=ok, warnings=0, infos=0, errors=0)" in out


def test_governance_cli_json_output(capsys: pytest.CaptureFixture[str]) -> None:
    from agmind.cli import governance_cmd

    rc = governance_cmd.cmd_validate(as_json=True)

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    summary = payload["summary"]

    # Gate behaviour — exact (what the governance logic must guarantee).
    assert summary["check_count"] == 9
    assert summary["ok_count"] == 9
    assert summary["failed_count"] == 0
    assert summary["health_status"] == "ok"
    assert summary["status_counts"] == {"failed": 0, "warning": 0, "info": 0, "ok": 9}
    assert summary["payload_count"] == 9
    assert summary["payload_error_count"] == 0
    assert summary["payload_error_checks"] == []
    assert summary["total_warnings"] == 0
    assert summary["total_infos"] == 0
    assert summary["total_errors"] == 0
    assert summary["failed_checks"] == []
    assert summary["warning_checks"] == []
    assert summary["info_checks"] == []

    # Research k8s/topology gates: warnings/infos allowed but all must be expected.
    assert summary["topology_warnings"] == 0
    assert summary["topology_infos"] == summary["topology_expected_infos"]
    assert summary["topology_unexpected_infos"] == 0
    assert summary["kubernetes_warnings"] == summary["kubernetes_expected_warnings"]
    assert summary["kubernetes_unexpected_warnings"] == 0
    assert summary["kubernetes_warning_summary"] == summary["kubernetes_expected_warning_summary"]
    assert summary["kubernetes_unexpected_warning_summary"] == {
        "info": 0,
        "warning": 0,
        "blocker": 0,
    }

    # Catalog census: present and positive so benign content growth does not
    # break this gate test; the per-check payloads below cross-check the counts.
    for key in (
        "component_contracts",
        "service_descriptors",
        "deploy_targets",
        "tool_candidates",
        "constraint_planes",
        "constraint_package_rules",
        "topology_profiles",
        "kubernetes_targets",
        "kubernetes_proof_targets",
        "docs_mirror_headings",
        "docs_mirror_code_blocks",
    ):
        assert isinstance(summary[key], int) and summary[key] > 0, key

    # Every gate reports healthy.
    assert {item["name"] for item in summary["check_health"]} == {
        "docs-mirror",
        "components",
        "deploy-targets",
        "tool-candidates",
        "constraints",
        "topology",
        "kubernetes-render",
        "kubernetes-proof-workflow",
        "digest-pins",
    }
    assert all(
        item["ok"] and item["status"] == "ok" and item["errors"] == 0
        for item in summary["check_health"]
    )
    assert [item["name"] for item in payload["checks"]] == [
        "docs-mirror",
        "components",
        "deploy-targets",
        "tool-candidates",
        "constraints",
        "topology",
        "kubernetes-render",
        "kubernetes-proof-workflow",
        "digest-pins",
    ]
    deploy_targets = next(item for item in payload["checks"] if item["name"] == "deploy-targets")
    topology = next(item for item in payload["checks"] if item["name"] == "topology")
    k8s_render = next(item for item in payload["checks"] if item["name"] == "kubernetes-render")
    proof = next(item for item in payload["checks"] if item["name"] == "kubernetes-proof-workflow")
    docs_mirror = next(item for item in payload["checks"] if item["name"] == "docs-mirror")
    assert docs_mirror["payload"]["heading_count"] > 0
    assert docs_mirror["payload"]["code_block_count"] > 0
    assert deploy_targets["payload"]["target_count"] > 0
    assert deploy_targets["payload"]["error_count"] == 0
    components = next(item for item in payload["checks"] if item["name"] == "components")
    tool_candidates = next(item for item in payload["checks"] if item["name"] == "tool-candidates")
    constraints = next(item for item in payload["checks"] if item["name"] == "constraints")
    assert components["payload"]["contract_count"] > 0
    assert tool_candidates["payload"]["candidate_count"] > 0
    assert constraints["payload"]["plane_count"] > 0
    assert topology["payload"]["info_count"] == topology["payload"]["expected_info_count"]
    assert topology["payload"]["unexpected_info_count"] == 0
    assert k8s_render["payload"]["targets"][0]["target_id"] == "k3s"
    assert proof["payload"]["target_count"] == 1


def test_governance_check_script_json_includes_structured_gate_payloads() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "checks" / "governance_check.py"),
            "--json",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    payload = json.loads(result.stdout)
    assert payload["summary"]["payload_count"] == 9
    assert payload["summary"]["total_warnings"] == 0
    assert payload["summary"]["total_infos"] == 0
    assert payload["summary"]["total_errors"] == 0
    assert payload["summary"]["health_status"] == "ok"
    assert payload["summary"]["status_counts"] == {
        "failed": 0,
        "warning": 0,
        "info": 0,
        "ok": 9,
    }
    assert payload["summary"]["failed_checks"] == []
    assert payload["summary"]["warning_checks"] == []
    assert payload["summary"]["info_checks"] == []
    assert payload["summary"]["check_health"][5] == {
        "name": "topology",
        "ok": True,
        "status": "ok",
        "warnings": 0,
        "infos": 0,
        "errors": 0,
    }
    assert payload["summary"]["check_health"][6] == {
        "name": "kubernetes-render",
        "ok": True,
        "status": "ok",
        "warnings": 0,
        "infos": 0,
        "errors": 0,
    }
    assert payload["summary"]["kubernetes_warning_summary"] == {
        "info": 0,
        "warning": 4,
        "blocker": 0,
    }
    assert payload["summary"]["kubernetes_expected_warning_summary"] == {
        "info": 0,
        "warning": 4,
        "blocker": 0,
    }
    assert payload["summary"]["kubernetes_unexpected_warning_summary"] == {
        "info": 0,
        "warning": 0,
        "blocker": 0,
    }
    # H.4: topology now validates all 13 isolation lanes; isolation-mode promotes
    # single-profile dependency warnings to expected infos (count ≥ 1).
    assert payload["summary"]["topology_expected_infos"] >= 1
    assert payload["summary"]["topology_unexpected_infos"] == 0
    assert all(item["payload"] is not None for item in payload["checks"])
    deploy_targets = next(item for item in payload["checks"] if item["name"] == "deploy-targets")
    proof = next(item for item in payload["checks"] if item["name"] == "kubernetes-proof-workflow")
    components = next(item for item in payload["checks"] if item["name"] == "components")
    constraints = next(item for item in payload["checks"] if item["name"] == "constraints")
    tool_candidates = next(item for item in payload["checks"] if item["name"] == "tool-candidates")
    docs_mirror = next(item for item in payload["checks"] if item["name"] == "docs-mirror")
    assert docs_mirror["stdout"].startswith("README mirror OK:")
    assert docs_mirror["payload"]["ok"] is True
    assert components["stdout"].startswith("component contracts OK:")
    assert deploy_targets["stdout"].startswith("deployment targets OK:")
    assert tool_candidates["stdout"].startswith("tool candidates OK:")
    assert constraints["stdout"].startswith("dependency constraints OK:")
    assert components["payload"]["service_count"] == 46
    assert deploy_targets["payload"]["target_count"] == 3
    assert tool_candidates["payload"]["candidate_count"] == 11
    assert constraints["payload"]["package_rule_count"] == 46
    assert proof["payload"]["target_count"] == 1


def test_governance_check_script_runs() -> None:
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "checks" / "governance_check.py")],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert "governance OK: 9 checks (status=ok, warnings=0, infos=0, errors=0)" in result.stdout


def test_governance_failure_summary_marks_unknown_check_failed() -> None:
    from agmind.governance import format_governance_report, run_governance_checks

    report = run_governance_checks(checks=("missing-gate",), structured=True)

    payload = report.to_json()
    assert payload["ok"] is False
    assert payload["summary"]["health_status"] == "failed"
    assert payload["summary"]["status_counts"] == {
        "failed": 1,
        "warning": 0,
        "info": 0,
        "ok": 0,
    }
    assert payload["summary"]["failed_checks"] == ["missing-gate"]
    assert payload["summary"]["total_errors"] == 1
    assert payload["summary"]["check_health"] == [
        {
            "name": "missing-gate",
            "ok": False,
            "status": "failed",
            "warnings": 0,
            "infos": 0,
            "errors": 1,
        }
    ]
    assert "unknown governance check: missing-gate" in payload["checks"][0]["stderr"]
    assert (
        "governance FAILED: 1/1 checks failed "
        "(status=failed, warnings=0, infos=0, errors=1)" in format_governance_report(report)
    )


def test_governance_payload_errors_fail_report_even_with_zero_returncode() -> None:
    from agmind.governance import (
        GovernanceCheckResult,
        GovernanceReport,
        format_governance_report,
    )

    report = GovernanceReport(
        results=(
            GovernanceCheckResult(
                name="payload-error",
                returncode=0,
                stdout="payload check reported errors",
                stderr="",
                payload={"error_count": 1},
            ),
        )
    )

    payload = report.to_json()
    assert report.results[0].ok is False
    assert report.ok is False
    assert payload["ok"] is False
    assert payload["checks"][0]["ok"] is False
    assert payload["summary"]["ok_count"] == 0
    assert payload["summary"]["failed_count"] == 1
    assert payload["summary"]["health_status"] == "failed"
    assert payload["summary"]["status_counts"] == {
        "failed": 1,
        "warning": 0,
        "info": 0,
        "ok": 0,
    }
    assert payload["summary"]["failed_checks"] == ["payload-error"]
    assert payload["summary"]["check_health"] == [
        {
            "name": "payload-error",
            "ok": False,
            "status": "failed",
            "warnings": 0,
            "infos": 0,
            "errors": 1,
        }
    ]
    text = format_governance_report(report)
    assert "payload-error: FAILED" in text
    assert (
        "governance FAILED: 1/1 checks failed "
        "(status=failed, warnings=0, infos=0, errors=1)" in text
    )


def test_governance_nonzero_returncode_counts_error_when_payload_omits_error_count() -> None:
    from agmind.governance import GovernanceCheckResult, GovernanceReport

    report = GovernanceReport(
        results=(
            GovernanceCheckResult(
                name="payload-missing-error-count",
                returncode=1,
                stdout="",
                stderr="payload check failed",
                payload={},
            ),
        )
    )

    payload = report.to_json()
    assert report.results[0].ok is False
    assert payload["ok"] is False
    assert payload["summary"]["total_errors"] == 1
    assert payload["summary"]["health_status"] == "failed"
    assert payload["summary"]["status_counts"] == {
        "failed": 1,
        "warning": 0,
        "info": 0,
        "ok": 0,
    }
    assert payload["summary"]["failed_checks"] == ["payload-missing-error-count"]
    assert payload["summary"]["check_health"] == [
        {
            "name": "payload-missing-error-count",
            "ok": False,
            "status": "failed",
            "warnings": 0,
            "infos": 0,
            "errors": 1,
        }
    ]


def test_governance_structured_check_fails_when_json_payload_is_invalid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import agmind.governance as governance

    def run_text() -> int:
        print("broken structured check OK")
        return 0

    def run_json() -> int:
        print("not-json")
        return 0

    monkeypatch.setattr(
        governance,
        "_load_check_functions",
        lambda: {
            "broken-json": governance.GovernanceCheckFunctions(
                run=run_text,
                run_json=run_json,
            )
        },
    )

    report = governance.run_governance_checks(checks=("broken-json",), structured=True)

    payload = report.to_json()
    assert report.results[0].ok is False
    assert report.results[0].returncode == 2
    assert payload["ok"] is False
    assert payload["summary"]["payload_count"] == 0
    assert payload["summary"]["payload_error_count"] == 1
    assert payload["summary"]["payload_error_checks"] == ["broken-json"]
    assert payload["summary"]["total_errors"] == 1
    assert payload["summary"]["health_status"] == "failed"
    assert payload["summary"]["failed_checks"] == ["broken-json"]
    assert (
        payload["checks"][0]["payload_error"] == "invalid structured JSON payload for broken-json"
    )
    assert payload["summary"]["check_health"] == [
        {
            "name": "broken-json",
            "ok": False,
            "status": "failed",
            "warnings": 0,
            "infos": 0,
            "errors": 1,
        }
    ]
    assert "invalid structured JSON payload for broken-json" in payload["checks"][0]["stderr"]
    text = governance.format_governance_report(report)
    assert "broken-json: FAILED" in text
    assert "invalid structured JSON payload for broken-json" in text


def test_governance_console_entrypoint_runs() -> None:
    agmind = Path(sys.executable).with_name("agmind")
    result = subprocess.run(
        [str(agmind), "governance", "validate"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert "governance OK: 9 checks (status=ok, warnings=0, infos=0, errors=0)" in result.stdout


def test_pre_commit_runs_governance_check_for_aggregate_files() -> None:
    config = yaml.safe_load((REPO_ROOT / ".pre-commit-config.yaml").read_text())

    hooks = [hook for repo in config["repos"] if repo["repo"] == "local" for hook in repo["hooks"]]
    hook = next(item for item in hooks if item["id"] == "agmind-governance-check")

    assert hook["entry"] == ".venv/bin/python scripts/checks/governance_check.py"
    assert "agmind/governance/" in hook["files"]
    assert "agmind/cli/governance_cmd\\.py" in hook["files"]
    assert r"scripts/checks/governance_check\.py" in hook["files"]
    assert r"scripts/checks/topology_check\.py" in hook["files"]
    assert r"scripts/checks/kubernetes_render_check\.py" in hook["files"]
    assert r"scripts/checks/kubernetes_proof_workflow_check\.py" in hook["files"]
    assert r"scripts/checks/docs_mirror_check\.py" in hook["files"]
    assert "README\\.md" in hook["files"]
    assert "README\\.ru\\.md" in hook["files"]
    assert "\\.github/workflows/kubernetes-proof\\.yml" in hook["files"]


def test_pre_commit_validates_json_files() -> None:
    """Guard JSON-backed runtime catalogs and i18n files before pytest fallback."""
    config = yaml.safe_load((REPO_ROOT / ".pre-commit-config.yaml").read_text())

    hooks = [
        hook
        for repo in config["repos"]
        if repo["repo"] == "https://github.com/pre-commit/pre-commit-hooks"
        for hook in repo["hooks"]
    ]

    assert "check-json" in {hook["id"] for hook in hooks}


def test_ci_runs_governance_summary_gate() -> None:
    workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "governance-validate" in workflow
    assert "docs-mirror-validate" in workflow
    assert "topology-validate" in workflow
    assert "kubernetes-proof-workflow-validate" in workflow
    assert (
        "needs: [docs-mirror-validate, component-validate, deploy-target-validate, tool-candidate-validate, constraints-validate, topology-validate, healthcheck-tool-validate, kubernetes-render-validate, kubernetes-proof-workflow-validate]"
        in workflow
    )
    assert "scripts/checks/docs_mirror_check.py" in workflow
    assert "scripts/checks/topology_check.py" in workflow
    assert "scripts/checks/governance_check.py" in workflow


def test_kubernetes_proof_workflow_guard_rejects_non_always_verifier(tmp_path: Path) -> None:
    from agmind.deploy import load_deploy_targets
    from agmind.deploy.target_checks import (
        validate_kubernetes_proof_workflow,
        validate_kubernetes_proof_workflow_report,
    )

    workflow_path = REPO_ROOT / ".github" / "workflows" / "kubernetes-proof.yml"
    workflow = workflow_path.read_text(encoding="utf-8")
    verifier_block = (
        "      - name: Verify k3s proof bundle\n"
        "        if: always()\n"
        "        run: |\n"
        "          set -o pipefail\n"
        "          set +e\n"
        "          mkdir -p local-kubernetes-proof/k3s\n"
        "          .venv/bin/python scripts/proof/kubernetes_dry_run.py --json --verify-artifact-dir "
        "local-kubernetes-proof/k3s | tee local-kubernetes-proof/k3s/verification.json\n"
        "          status=${PIPESTATUS[0]}\n"
        '          exit "$status"'
    )
    non_always_verifier_block = (
        "      - name: Verify k3s proof bundle\n"
        "        run: |\n"
        "          set -o pipefail\n"
        "          set +e\n"
        "          mkdir -p local-kubernetes-proof/k3s\n"
        "          .venv/bin/python scripts/proof/kubernetes_dry_run.py --json --verify-artifact-dir "
        "local-kubernetes-proof/k3s | tee local-kubernetes-proof/k3s/verification.json\n"
        "          status=${PIPESTATUS[0]}\n"
        '          exit "$status"'
    )
    tmp_workflow = tmp_path / "kubernetes-proof.yml"
    tmp_workflow.write_text(
        workflow.replace(verifier_block, non_always_verifier_block),
        encoding="utf-8",
    )

    errors = validate_kubernetes_proof_workflow(
        load_deploy_targets(),
        workflow_path=tmp_workflow,
    )

    assert "k3s: Kubernetes proof workflow verifier must run with if: always()" in errors

    report = validate_kubernetes_proof_workflow_report(
        load_deploy_targets(),
        workflow_path=tmp_workflow,
    )
    assert report.ok is False
    assert report.error_count == 1
    assert report.warning_count == 0
    assert report.errors[0].kind == "workflow_verifier_not_always"
    assert report.errors[0].target_id == "k3s"


def test_kubernetes_proof_workflow_guard_rejects_missing_bundle_diagnostics(
    tmp_path: Path,
) -> None:
    from agmind.deploy import load_deploy_targets
    from agmind.deploy.target_checks import validate_kubernetes_proof_workflow

    workflow_path = REPO_ROOT / ".github" / "workflows" / "kubernetes-proof.yml"
    workflow = workflow_path.read_text(encoding="utf-8")
    diagnostic_step = (
        "      - name: Summarize k3s proof bundle\n"
        "        if: always()\n"
        "        run: |\n"
        "          set -euo pipefail\n"
        "          if [ ! -d local-kubernetes-proof/k3s ]; then\n"
        '            echo "k3s proof bundle directory is missing"\n'
        "            exit 0\n"
        "          fi\n"
        "          find local-kubernetes-proof/k3s -maxdepth 1 -type f -print | sort\n"
        "          if [ -f local-kubernetes-proof/k3s/checksums.txt ]; then\n"
        '            echo "checksums.txt:"\n'
        "            cat local-kubernetes-proof/k3s/checksums.txt\n"
        "          fi\n\n"
    )
    tmp_workflow = tmp_path / "kubernetes-proof.yml"
    tmp_workflow.write_text(
        workflow.replace(diagnostic_step, ""),
        encoding="utf-8",
    )

    errors = validate_kubernetes_proof_workflow(
        load_deploy_targets(),
        workflow_path=tmp_workflow,
    )

    assert (
        "k3s: Kubernetes proof workflow must summarize proof bundle contents with if: always()"
    ) in errors


def test_kubernetes_proof_workflow_guard_rejects_missing_verification_report(
    tmp_path: Path,
) -> None:
    from agmind.deploy import load_deploy_targets
    from agmind.deploy.target_checks import validate_kubernetes_proof_workflow

    workflow_path = REPO_ROOT / ".github" / "workflows" / "kubernetes-proof.yml"
    workflow = workflow_path.read_text(encoding="utf-8")
    tmp_workflow = tmp_path / "kubernetes-proof.yml"
    tmp_workflow.write_text(
        workflow.replace(" | tee local-kubernetes-proof/k3s/verification.json", ""),
        encoding="utf-8",
    )

    errors = validate_kubernetes_proof_workflow(
        load_deploy_targets(),
        workflow_path=tmp_workflow,
    )

    assert "k3s: Kubernetes proof workflow verifier must write verification.json" in errors


def test_kubernetes_proof_workflow_guard_rejects_missing_verification_report_upload(
    tmp_path: Path,
) -> None:
    from agmind.deploy import load_deploy_targets
    from agmind.deploy.target_checks import validate_kubernetes_proof_workflow

    workflow_path = REPO_ROOT / ".github" / "workflows" / "kubernetes-proof.yml"
    workflow = workflow_path.read_text(encoding="utf-8")
    tmp_workflow = tmp_path / "kubernetes-proof.yml"
    tmp_workflow.write_text(
        workflow.replace("            local-kubernetes-proof/k3s/verification.json\n", ""),
        encoding="utf-8",
    )

    errors = validate_kubernetes_proof_workflow(
        load_deploy_targets(),
        workflow_path=tmp_workflow,
    )

    assert (
        "k3s: Kubernetes proof workflow missing verifier artifact: "
        "local-kubernetes-proof/k3s/verification.json"
    ) in errors


def test_kubernetes_proof_workflow_guard_rejects_missing_declared_artifact_upload(
    tmp_path: Path,
) -> None:
    from agmind.deploy import load_deploy_targets
    from agmind.deploy.target_checks import validate_kubernetes_proof_workflow

    workflow_path = REPO_ROOT / ".github" / "workflows" / "kubernetes-proof.yml"
    workflow = workflow_path.read_text(encoding="utf-8")
    tmp_workflow = tmp_path / "kubernetes-proof.yml"
    tmp_workflow.write_text(
        workflow.replace("            local-kubernetes-proof/k3s/checksums.txt\n", ""),
        encoding="utf-8",
    )

    errors = validate_kubernetes_proof_workflow(
        load_deploy_targets(),
        workflow_path=tmp_workflow,
    )

    assert (
        "k3s: Kubernetes proof workflow missing artifact: local-kubernetes-proof/k3s/checksums.txt"
    ) in errors


def test_kubernetes_proof_workflow_check_script_json_output() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "checks" / "kubernetes_proof_workflow_check.py"),
            "--json",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["target_count"] == 1
    assert payload["error_count"] == 0
    assert payload["warning_count"] == 0
    assert payload["info_count"] == 0
