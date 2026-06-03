from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from agmind.deploy.target_checks import (
    format_deployment_check_report,
    validate_deploy_target_report,
    validate_deploy_targets,
)
from agmind.deploy.targets import DeploymentTarget, load_deploy_targets

pytestmark = pytest.mark.backend_any

REPO_ROOT = Path(__file__).resolve().parents[2]


def _write_target(path: Path, *, target_id: str = "ubuntu-compose") -> Path:
    payload = {
        "id": target_id,
        "name": "Ubuntu Compose",
        "status": "supported",
        "summary": "Existing single-host AGmind lane: Ubuntu, Ansible, Docker Compose.",
        "runtime": {
            "kind": "compose",
            "renderer": "agmind render compose",
            "profiles": ["core", "rag"],
        },
        "provisioner": {"kind": "none"},
        "configurator": {
            "kind": "ansible",
            "inventory_source": "static-inventory",
            "playbooks": ["ansible/playbooks/site.yml"],
        },
        "storage_profile": "local-paths",
        "secrets_profile": "env-files",
        "verification": {
            "commands": [
                "agmind render compose --profile core,rag --domain ci.example.com --output /tmp/agmind-ubuntu-compose.yml",
                "docker compose --env-file /opt/agmind/.env -f /tmp/agmind-ubuntu-compose.yml config --quiet",
            ]
        },
    }
    path.write_text(yaml.safe_dump(payload, sort_keys=True), encoding="utf-8")
    return path


def _kubernetes_target_with_verification(
    *,
    commands: tuple[str, ...],
    artifacts: tuple[str, ...] = (),
) -> DeploymentTarget:
    return DeploymentTarget.model_validate(
        {
            "id": "k3s",
            "name": "k3s Homelab",
            "status": "research",
            "summary": "Research Kubernetes target.",
            "runtime": {
                "kind": "kubernetes",
                "renderer": "agmind render kubernetes",
                "profiles": ["core", "rag", "observability"],
            },
            "provisioner": {"kind": "external", "state_backend": "external"},
            "configurator": {
                "kind": "helm",
                "inventory_source": "kubeconfig",
                "charts": ["charts/agmind"],
                "manifests": ["k8s/agmind"],
            },
            "storage_profile": "longhorn",
            "secrets_profile": "external-secrets",
            "verification": {
                "commands": list(commands),
                "artifacts": list(artifacts),
                "expected_warning_codes": [
                    "amd-gpu-device-plugin",
                    "kubernetes-omitted",
                ],
            },
        }
    )


def _supported_compose_target() -> DeploymentTarget:
    return DeploymentTarget.model_validate(
        {
            "id": "ubuntu-compose",
            "name": "Ubuntu Compose",
            "status": "supported",
            "summary": "Existing single-host AGmind lane.",
            "runtime": {
                "kind": "compose",
                "renderer": "agmind render compose",
                "profiles": ["core", "rag"],
            },
            "provisioner": {"kind": "none"},
            "configurator": {"kind": "ansible", "inventory_source": "static-inventory"},
            "storage_profile": "local-paths",
            "secrets_profile": "env-files",
            "verification": {
                "commands": [
                    "agmind render compose --profile core,rag --domain ci.example.com --output /tmp/agmind-ubuntu-compose.yml",
                    "docker compose --env-file /opt/agmind/.env -f /tmp/agmind-ubuntu-compose.yml config --quiet",
                ]
            },
        }
    )


def test_deployment_target_parses_boundary_contract(tmp_path: Path) -> None:
    path = _write_target(tmp_path / "ubuntu-compose.yaml")

    target = DeploymentTarget.from_yaml(path)

    assert target.id == "ubuntu-compose"
    assert target.status == "supported"
    assert target.runtime.kind == "compose"
    assert target.runtime.renderer == "agmind render compose"
    assert target.provisioner.kind == "none"
    assert target.configurator.kind == "ansible"
    assert target.storage_profile == "local-paths"
    assert target.secrets_profile == "env-files"
    assert target.verification.commands == (
        "agmind render compose --profile core,rag --domain ci.example.com --output /tmp/agmind-ubuntu-compose.yml",
        "docker compose --env-file /opt/agmind/.env -f /tmp/agmind-ubuntu-compose.yml config --quiet",
    )
    assert target.verification.expected_warning_codes == ()


def test_deployment_target_parses_expected_warning_codes(tmp_path: Path) -> None:
    path = _write_target(tmp_path / "k3s.yaml", target_id="k3s")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["runtime"]["kind"] = "kubernetes"
    data["runtime"]["renderer"] = "agmind render kubernetes"
    data["status"] = "research"
    data["configurator"] = {"kind": "kubectl", "inventory_source": "kubeconfig"}
    data["storage_profile"] = "longhorn"
    data["secrets_profile"] = "external-secrets"
    data["verification"]["expected_warning_codes"] = [
        "amd-gpu-device-plugin",
        "kubernetes-omitted",
    ]
    path.write_text(yaml.safe_dump(data, sort_keys=True), encoding="utf-8")

    target = DeploymentTarget.from_yaml(path)

    assert target.verification.expected_warning_codes == (
        "amd-gpu-device-plugin",
        "kubernetes-omitted",
    )


def test_deployment_target_parses_runtime_excluded_services(tmp_path: Path) -> None:
    path = _write_target(tmp_path / "k3s.yaml", target_id="k3s")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["runtime"]["kind"] = "kubernetes"
    data["runtime"]["renderer"] = "agmind render kubernetes"
    data["runtime"]["excluded_services"] = ["dozzle", "watchtower"]
    data["status"] = "research"
    data["configurator"] = {"kind": "kubectl", "inventory_source": "kubeconfig"}
    data["storage_profile"] = "longhorn"
    data["secrets_profile"] = "external-secrets"
    path.write_text(yaml.safe_dump(data, sort_keys=True), encoding="utf-8")

    target = DeploymentTarget.from_yaml(path)

    assert target.runtime.excluded_services == ("dozzle", "watchtower")


def test_deployment_target_parses_expected_warnings(tmp_path: Path) -> None:
    path = _write_target(tmp_path / "k3s.yaml", target_id="k3s")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["runtime"]["kind"] = "kubernetes"
    data["runtime"]["renderer"] = "agmind render kubernetes"
    data["status"] = "research"
    data["configurator"] = {"kind": "kubectl", "inventory_source": "kubeconfig"}
    data["storage_profile"] = "longhorn"
    data["secrets_profile"] = "external-secrets"
    data["verification"]["expected_warnings"] = [
        {"service": "llama-llm", "code": "amd-gpu-device-plugin"},
        {"service": "portainer", "code": "kubernetes-omitted"},
    ]
    path.write_text(yaml.safe_dump(data, sort_keys=True), encoding="utf-8")

    target = DeploymentTarget.from_yaml(path)

    assert [item.model_dump() for item in target.verification.expected_warnings] == [
        {"service": "llama-llm", "code": "amd-gpu-device-plugin"},
        {"service": "portainer", "code": "kubernetes-omitted"},
    ]


def test_deployment_target_rejects_invalid_id(tmp_path: Path) -> None:
    path = _write_target(tmp_path / "bad.yaml", target_id="Proxmox VM")

    with pytest.raises(ValidationError, match="id"):
        DeploymentTarget.from_yaml(path)


def test_deployment_target_rejects_unknown_runtime_kind(tmp_path: Path) -> None:
    path = _write_target(tmp_path / "bad-runtime.yaml")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["runtime"]["kind"] = "swarm"
    path.write_text(yaml.safe_dump(data, sort_keys=True), encoding="utf-8")

    with pytest.raises(ValidationError, match="runtime"):
        DeploymentTarget.from_yaml(path)


def test_load_deploy_targets_is_sorted_by_id(tmp_path: Path) -> None:
    root = tmp_path / "deploy-targets"
    root.mkdir()
    _write_target(root / "z-ubuntu.yaml", target_id="ubuntu-compose")
    _write_target(root / "a-k3s.yaml", target_id="k3s")

    loaded = load_deploy_targets(root)

    assert tuple(loaded) == ("k3s", "ubuntu-compose")


def test_load_deploy_targets_rejects_duplicate_ids(tmp_path: Path) -> None:
    root = tmp_path / "deploy-targets"
    root.mkdir()
    _write_target(root / "one.yaml", target_id="ubuntu-compose")
    _write_target(root / "two.yaml", target_id="ubuntu-compose")

    with pytest.raises(ValueError, match="duplicate deployment target id"):
        load_deploy_targets(root)


def test_repository_deploy_targets_load() -> None:
    targets = load_deploy_targets()

    expected = {"ubuntu-compose", "proxmox-vm-compose", "k3s"}
    assert expected <= set(targets)
    assert targets["ubuntu-compose"].runtime.kind == "compose"
    assert targets["ubuntu-compose"].status == "supported"
    assert targets["proxmox-vm-compose"].provisioner.kind == "opentofu-proxmox"
    assert targets["proxmox-vm-compose"].configurator.kind == "ansible"
    assert targets["k3s"].runtime.kind == "kubernetes"
    assert targets["k3s"].runtime.renderer == "agmind render kubernetes"
    assert targets["k3s"].status == "research"
    assert targets["k3s"].runtime.excluded_services == (
        "alloy",
        "cadvisor",
        "dozzle",
        "homarr",
        "netdata",
        "portainer",
        "watchtower",
    )
    assert targets["k3s"].verification.expected_warning_codes == ()
    assert [item.model_dump() for item in targets["k3s"].verification.expected_warnings] == [
        {"service": "cadvisor", "code": "kubernetes-omitted"},
        {"service": "llama-embed", "code": "amd-gpu-device-plugin"},
        {"service": "llama-llm", "code": "amd-gpu-device-plugin"},
        {"service": "llama-rerank", "code": "amd-gpu-device-plugin"},
        {"service": "ssrf-proxy", "code": "docker-security-opt"},
    ]
    assert (
        "scripts/proof/kubernetes_dry_run.py --target k3s --require-cluster --require-amd-gpu "
        "--artifact-dir local-kubernetes-proof/k3s" in targets["k3s"].verification.commands
    )


def test_repository_k3s_declares_real_proof_artifact_bundle() -> None:
    targets = load_deploy_targets()
    k3s = targets["k3s"]

    assert (
        "scripts/proof/kubernetes_dry_run.py --verify-artifact-dir local-kubernetes-proof/k3s"
        in k3s.verification.commands
    )
    assert k3s.verification.artifacts == (
        "local-kubernetes-proof/k3s/k3s.yaml",
        "local-kubernetes-proof/k3s/k3s.dry-run.json",
        "local-kubernetes-proof/k3s/proof-command.txt",
        "local-kubernetes-proof/k3s/run-metadata.json",
        "local-kubernetes-proof/k3s/summary.json",
        "local-kubernetes-proof/k3s/checksums.txt",
    )


def test_validate_deploy_targets_rejects_kubernetes_proof_without_artifact_dir(
    tmp_path: Path,
) -> None:
    del tmp_path
    supported = _supported_compose_target()
    k3s = _kubernetes_target_with_verification(
        commands=(
            "scripts/proof/kubernetes_dry_run.py --target k3s --require-cluster --require-amd-gpu",
        )
    )

    errors = validate_deploy_targets({"ubuntu-compose": supported, "k3s": k3s})

    assert "k3s: Kubernetes proof command must include --artifact-dir" in errors


def test_validate_deploy_target_report_classifies_missing_artifact_dir_as_error(
    tmp_path: Path,
) -> None:
    del tmp_path
    supported = _supported_compose_target()
    k3s = _kubernetes_target_with_verification(
        commands=(
            "scripts/proof/kubernetes_dry_run.py --target k3s --require-cluster --require-amd-gpu",
        )
    )

    report = validate_deploy_target_report({"ubuntu-compose": supported, "k3s": k3s})

    assert report.ok is False
    assert report.target_count == 2
    assert report.error_count == 1
    assert report.warning_count == 0
    assert report.info_count == 0
    assert report.errors[0].severity == "error"
    assert report.errors[0].kind == "missing_kubernetes_artifact_dir"
    assert report.errors[0].target_id == "k3s"
    assert report.errors[0].message == "k3s: Kubernetes proof command must include --artifact-dir"
    payload = report.to_json()
    assert payload["ok"] is False
    assert payload["error_count"] == 1
    assert payload["issues"][0]["kind"] == "missing_kubernetes_artifact_dir"
    assert "- k3s: Kubernetes proof command must include --artifact-dir" in (
        format_deployment_check_report(report, ok_label="deployment targets")
    )


def test_validate_deploy_targets_rejects_kubernetes_proof_without_bundle_verifier(
    tmp_path: Path,
) -> None:
    del tmp_path
    supported = _supported_compose_target()
    k3s = _kubernetes_target_with_verification(
        commands=(
            "scripts/proof/kubernetes_dry_run.py --target k3s --require-cluster --require-amd-gpu "
            "--artifact-dir local-kubernetes-proof/k3s",
        ),
        artifacts=(
            "local-kubernetes-proof/k3s/k3s.yaml",
            "local-kubernetes-proof/k3s/k3s.dry-run.json",
            "local-kubernetes-proof/k3s/proof-command.txt",
            "local-kubernetes-proof/k3s/run-metadata.json",
            "local-kubernetes-proof/k3s/summary.json",
            "local-kubernetes-proof/k3s/checksums.txt",
        ),
    )

    errors = validate_deploy_targets({"ubuntu-compose": supported, "k3s": k3s})

    assert (
        "k3s: Kubernetes proof artifact dir lacks verifier command: "
        "scripts/proof/kubernetes_dry_run.py --verify-artifact-dir local-kubernetes-proof/k3s"
    ) in errors


def test_validate_deploy_targets_rejects_kubernetes_proof_without_bundle_artifacts(
    tmp_path: Path,
) -> None:
    del tmp_path
    supported = _supported_compose_target()
    k3s = _kubernetes_target_with_verification(
        commands=(
            "scripts/proof/kubernetes_dry_run.py --target k3s --require-cluster --require-amd-gpu "
            "--artifact-dir local-kubernetes-proof/k3s",
            "scripts/proof/kubernetes_dry_run.py --verify-artifact-dir local-kubernetes-proof/k3s",
        )
    )

    errors = validate_deploy_targets({"ubuntu-compose": supported, "k3s": k3s})

    assert (
        "k3s: Kubernetes proof artifacts missing: "
        "local-kubernetes-proof/k3s/checksums.txt, "
        "local-kubernetes-proof/k3s/k3s.dry-run.json, "
        "local-kubernetes-proof/k3s/k3s.yaml, "
        "local-kubernetes-proof/k3s/proof-command.txt, "
        "local-kubernetes-proof/k3s/run-metadata.json, "
        "local-kubernetes-proof/k3s/summary.json"
    ) in errors


def test_validate_deploy_targets_rejects_unknown_runtime_excluded_service() -> None:
    supported = _supported_compose_target()
    k3s_payload = _kubernetes_target_with_verification(
        commands=(
            "scripts/proof/kubernetes_dry_run.py --target k3s --require-cluster "
            "--require-amd-gpu --artifact-dir local-kubernetes-proof/k3s",
            "scripts/proof/kubernetes_dry_run.py --verify-artifact-dir local-kubernetes-proof/k3s",
        ),
        artifacts=(
            "local-kubernetes-proof/k3s/k3s.yaml",
            "local-kubernetes-proof/k3s/k3s.dry-run.json",
            "local-kubernetes-proof/k3s/proof-command.txt",
            "local-kubernetes-proof/k3s/run-metadata.json",
            "local-kubernetes-proof/k3s/summary.json",
            "local-kubernetes-proof/k3s/checksums.txt",
        ),
    ).model_dump(mode="json")
    k3s_payload["runtime"]["excluded_services"] = ["does-not-exist"]
    k3s = DeploymentTarget.model_validate(k3s_payload)

    report = validate_deploy_target_report({"ubuntu-compose": supported, "k3s": k3s})

    assert report.ok is False
    assert report.errors[0].kind == "missing_runtime_excluded_service"
    assert report.errors[0].message == (
        "k3s: runtime excluded_services reference unknown service(s): does-not-exist"
    )


def test_validate_deploy_targets_rejects_unknown_runtime_profile() -> None:
    supported = _supported_compose_target()
    k3s_payload = _kubernetes_target_with_verification(
        commands=(
            "scripts/proof/kubernetes_dry_run.py --target k3s --require-cluster "
            "--require-amd-gpu --artifact-dir local-kubernetes-proof/k3s",
            "scripts/proof/kubernetes_dry_run.py --verify-artifact-dir local-kubernetes-proof/k3s",
        ),
        artifacts=(
            "local-kubernetes-proof/k3s/k3s.yaml",
            "local-kubernetes-proof/k3s/k3s.dry-run.json",
            "local-kubernetes-proof/k3s/proof-command.txt",
            "local-kubernetes-proof/k3s/run-metadata.json",
            "local-kubernetes-proof/k3s/summary.json",
            "local-kubernetes-proof/k3s/checksums.txt",
        ),
    ).model_dump(mode="json")
    k3s_payload["runtime"]["profiles"] = ["core", "missing-profile"]
    k3s = DeploymentTarget.model_validate(k3s_payload)

    report = validate_deploy_target_report({"ubuntu-compose": supported, "k3s": k3s})

    assert report.ok is False
    assert report.errors[0].kind == "missing_runtime_profile"
    assert report.errors[0].message == (
        "k3s: runtime profiles reference unknown profile(s): missing-profile"
    )


def test_export_schemas_writes_deploy_target_schema(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import scripts.dev.export_schemas as export_schemas

    monkeypatch.setattr(export_schemas, "SCHEMAS_DIR", tmp_path)

    out = export_schemas.export_deployment_target_schema()
    data = json.loads(out.read_text(encoding="utf-8"))

    assert out.name == "deploy-target.json"
    assert data["$id"].endswith("/schemas/deploy-target.json")
    assert data["title"] == "AGmind Deployment Target"


def test_deploy_target_check_script_runs() -> None:
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "checks" / "deploy_target_check.py")],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert "deployment targets OK" in result.stdout


def test_deploy_target_check_script_json_output() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "checks" / "deploy_target_check.py"),
            "--json",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["target_count"] == 3
    assert payload["error_count"] == 0
    assert payload["warning_count"] == 0
    assert payload["info_count"] == 0


def test_pre_commit_runs_deploy_target_check() -> None:
    config = yaml.safe_load((REPO_ROOT / ".pre-commit-config.yaml").read_text())

    hooks = [hook for repo in config["repos"] if repo["repo"] == "local" for hook in repo["hooks"]]
    hook = next(item for item in hooks if item["id"] == "agmind-deploy-target-check")

    assert hook["entry"] == ".venv/bin/python scripts/checks/deploy_target_check.py"
    assert "templates/deploy-targets/" in hook["files"]
    assert "agmind/deploy/target_checks\\.py" in hook["files"]


def test_ci_runs_deploy_target_contract_gate() -> None:
    workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "templates/schemas/deploy-target.json" in workflow
    assert "templates/deploy-targets/*.yaml" in workflow
    assert "scripts/checks/deploy_target_check.py" in workflow
