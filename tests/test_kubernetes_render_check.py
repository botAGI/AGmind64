from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from agmind.deploy.targets import DeploymentTarget

pytestmark = pytest.mark.backend_any

REPO_ROOT = Path(__file__).resolve().parents[1]


def _kubernetes_target(status: str) -> DeploymentTarget:
    return DeploymentTarget.model_validate(
        {
            "id": f"k3s-{status}",
            "name": f"k3s {status}",
            "status": status,
            "summary": "Test Kubernetes target.",
            "runtime": {
                "kind": "kubernetes",
                "renderer": "agmind render kubernetes",
                "profiles": ["core", "rag", "observability"],
            },
            "provisioner": {"kind": "external", "state_backend": "external"},
            "configurator": {
                "kind": "kubectl",
                "inventory_source": "kubeconfig",
                "manifests": ["k8s/agmind"],
            },
            "storage_profile": "longhorn",
            "secrets_profile": "external-secrets",
            "verification": {
                "commands": [
                    "agmind render kubernetes --profile core,rag,observability",
                    "kubectl apply --dry-run=server -f /tmp/agmind-k8s.yaml",
                ],
                "expected_warning_codes": [],
            },
        }
    )


def _services_dir_with_unknown_device(tmp_path: Path) -> Path:
    services_dir = tmp_path / "services"
    services_dir.mkdir()
    payload = {
        "name": "llama-llm",
        "image": "ghcr.io/ggml-org/llama.cpp:server-vulkan-b9049",
        "tier": "inference",
        "purpose": "Test inference service with unmapped device.",
        "profiles": ["core"],
        "ports": ["127.0.0.1:8080:8080"],
        "devices": ["/dev/custom0"],
        "resources": {"cpus": 1.0, "mem_limit": "1g"},
    }
    (services_dir / "llama-llm.yaml").write_text(
        yaml.safe_dump(payload, sort_keys=False),
        encoding="utf-8",
    )
    return services_dir


def test_kubernetes_render_check_reports_research_target() -> None:
    from agmind.deploy.targets import load_deploy_targets
    from agmind.services.kubernetes_checks import (
        format_kubernetes_render_report,
        validate_kubernetes_render_targets,
    )

    report = validate_kubernetes_render_targets(load_deploy_targets())

    assert report.ok is True
    assert len(report.targets) == 1
    target = report.targets[0]
    assert target.target_id == "k3s"
    assert target.renderer == "agmind render kubernetes"
    assert target.ok is True
    assert target.object_count == 36
    assert target.deployment_count == 22
    assert target.service_count == 13
    assert target.warning_count == 4
    assert target.warning_summary["blocker"] == 0
    assert target.warning_summary["warning"] == 4

    rendered = format_kubernetes_render_report(report)
    assert "k3s: OK" in rendered
    assert "kubernetes render OK: 1 targets" in rendered
    assert "warnings:" in rendered
    assert "blocker=0" in rendered
    assert "warning=" in rendered
    assert "blockers:" not in rendered
    assert "docker-device=" not in rendered
    assert "docker-socket=" not in rendered


def test_kubernetes_render_check_allows_blockers_for_research_target(tmp_path: Path) -> None:
    from agmind.services.kubernetes_checks import validate_kubernetes_render_targets

    services_dir = _services_dir_with_unknown_device(tmp_path)
    report = validate_kubernetes_render_targets(
        {"k3s-research": _kubernetes_target("research")},
        services_dir=services_dir,
    )

    assert report.ok is True
    assert report.targets[0].warning_summary["blocker"] > 0
    assert report.targets[0].errors == ()


def test_kubernetes_render_check_rejects_blockers_for_experimental_target(
    tmp_path: Path,
) -> None:
    from agmind.services.kubernetes_checks import validate_kubernetes_render_targets

    services_dir = _services_dir_with_unknown_device(tmp_path)
    report = validate_kubernetes_render_targets(
        {"k3s-experimental": _kubernetes_target("experimental")},
        services_dir=services_dir,
    )

    assert report.ok is False
    assert report.targets[0].warning_summary["blocker"] > 0
    assert any(
        "blocker warnings require research status" in item for item in report.targets[0].errors
    )


def test_kubernetes_render_check_strict_passes_with_expected_warning_policy() -> None:
    from agmind.deploy.targets import load_deploy_targets
    from agmind.services.kubernetes_checks import validate_kubernetes_render_targets

    report = validate_kubernetes_render_targets(load_deploy_targets(), strict=True)

    assert report.ok is True
    assert report.targets[0].target_id == "k3s"
    assert report.targets[0].errors == ()


def test_kubernetes_render_check_strict_allows_expected_warning_codes(tmp_path: Path) -> None:
    from agmind.services.kubernetes_checks import validate_kubernetes_render_targets

    services_dir = _services_dir_with_unknown_device(tmp_path)
    target_payload = _kubernetes_target("research").model_dump(mode="json")
    target_payload["verification"] = {
        "commands": [],
        "expected_warning_codes": ["docker-device"],
    }
    target = DeploymentTarget.model_validate(target_payload)

    report = validate_kubernetes_render_targets(
        {"k3s-research": target},
        services_dir=services_dir,
        strict=True,
    )

    assert report.ok is True
    assert report.targets[0].warning_summary["blocker"] == 1
    assert report.targets[0].errors == ()


def test_kubernetes_render_check_json_roundtrip() -> None:
    from agmind.deploy.targets import load_deploy_targets
    from agmind.services.kubernetes_checks import validate_kubernetes_render_targets

    payload = validate_kubernetes_render_targets(load_deploy_targets()).to_json()

    assert payload["ok"] is True
    assert payload["targets"][0]["target_id"] == "k3s"
    assert payload["targets"][0]["object_count"] == 36
    assert payload["targets"][0]["deployment_count"] == 22
    assert payload["targets"][0]["service_count"] == 13
    assert payload["targets"][0]["warning_count"] == 4
    assert payload["targets"][0]["warning_summary"]["blocker"] == 0
    assert payload["targets"][0]["warning_summary"]["warning"] == 4
    assert payload["warning_summary"]["blocker"] == 0
    assert not any(
        item["code"] == "env-interpolation" and item["service"] == "llama-embed"
        for item in payload["targets"][0]["warnings"]
    )
    assert not any(
        item["code"] == "env-interpolation"
        and item["service"] in {"grafana", "postgres", "redis", "postgres-exporter"}
        for item in payload["targets"][0]["warnings"]
    )
    assert not any(
        item["code"] == "docker-security-opt" and item["service"] == "llama-llm"
        for item in payload["targets"][0]["warnings"]
    )
    assert not any(
        item["code"] == "docker-group-add"
        and item["service"] in {"llama-embed", "llama-llm", "llama-rerank"}
        for item in payload["targets"][0]["warnings"]
    )
    assert not any(
        item["code"] == "env-interpolation"
        and item["service"] == "llama-llm"
        and "AGMIND_ROPE_SCALING" in item["message"]
        for item in payload["targets"][0]["warnings"]
    )
    assert not any(
        item["code"] == "env-interpolation"
        and item["service"] == "portainer"
        and "AGMIND_RERANK_FILE" in item["message"]
        for item in payload["targets"][0]["warnings"]
    )
    assert not any(
        item["code"] == "command-interpolation" for item in payload["targets"][0]["warnings"]
    )
    assert any(
        item["code"] == "kubernetes-omitted"
        and item["service"] == "portainer"
        and "Compose-only Docker management service" in item["message"]
        for item in payload["targets"][0]["warnings"]
    )
    warning = next(
        item
        for item in payload["targets"][0]["warnings"]
        if item["code"] == "amd-gpu-device-plugin"
    )
    assert set(warning) == {"service", "code", "severity", "message", "remediation"}
    assert warning["service"]
    assert warning["severity"] == "warning"
    assert warning["remediation"]


def test_kubernetes_render_check_script_runs() -> None:
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "kubernetes_render_check.py")],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert "kubernetes render OK: 1 targets" in result.stdout
    assert "blocker=0" in result.stdout
    assert "blockers:" not in result.stdout
    assert "docker-device=" not in result.stdout


def test_kubernetes_render_check_script_json_runs() -> None:
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "kubernetes_render_check.py"), "--json"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["targets"][0]["target_id"] == "k3s"
    assert payload["targets"][0]["warning_summary"]["blocker"] == 0
    assert payload["warning_summary"]["warning"] == 4
    assert any(
        item["code"] == "amd-gpu-device-plugin" and item["severity"] == "warning"
        for item in payload["targets"][0]["warnings"]
    )
    assert any(
        item["code"] == "kubernetes-omitted"
        and item["service"] == "portainer"
        and item["severity"] == "warning"
        for item in payload["targets"][0]["warnings"]
    )
    assert not any(
        item["code"] == "command-interpolation" for item in payload["targets"][0]["warnings"]
    )
    assert not any(item["severity"] == "blocker" for item in payload["targets"][0]["warnings"])


def test_kubernetes_render_check_script_strict_runs() -> None:
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "kubernetes_render_check.py"), "--strict"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert "kubernetes render OK: 1 targets" in result.stdout
    assert "strict mode rejects" not in result.stdout


def test_pre_commit_runs_kubernetes_render_check() -> None:
    config = yaml.safe_load((REPO_ROOT / ".pre-commit-config.yaml").read_text())

    hooks = [hook for repo in config["repos"] if repo["repo"] == "local" for hook in repo["hooks"]]
    hook = next(item for item in hooks if item["id"] == "agmind-kubernetes-render-check")

    assert hook["entry"] == ".venv/bin/python scripts/kubernetes_render_check.py"
    assert "agmind/services/kubernetes_renderer\\.py" in hook["files"]
    assert "agmind/services/kubernetes_checks\\.py" in hook["files"]
    assert "templates/deploy-targets/" in hook["files"]


def test_ci_runs_kubernetes_render_gate() -> None:
    workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "kubernetes-render-validate" in workflow
    assert "scripts/kubernetes_render_check.py" in workflow
    assert (
        "needs: [component-validate, deploy-target-validate, tool-candidate-validate, constraints-validate, topology-validate, kubernetes-render-validate, kubernetes-proof-workflow-validate]"
        in workflow
    )
