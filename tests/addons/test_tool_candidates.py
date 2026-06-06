from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from agmind.addons import ToolCandidate, load_tool_candidates
from agmind.deploy import load_deploy_targets
from agmind.schemas import ServiceDescriptor

pytestmark = pytest.mark.backend_any
REPO_ROOT = Path(__file__).resolve().parents[2]


def _write_candidate(path: Path, *, candidate_id: str = "comfyui") -> Path:
    payload = {
        "id": candidate_id,
        "name": "ComfyUI",
        "status": "candidate",
        "category": "creative-ai",
        "summary": "Optional node-based image workflow UI.",
        "admission": {
            "scope": "service-profile",
            "runtime": "compose",
            "contracts": ["render", "operate"],
            "component_contract_required": True,
            "service_descriptor_required": True,
            "image_pin_required": True,
        },
        "dependencies": {
            "deploy_targets": ["ubuntu-compose", "proxmox-vm-compose"],
            "profiles": ["creative-ai"],
            "storage_profiles": ["local-paths", "proxmox-zfs"],
            "secrets_profiles": ["env-files", "sops-age"],
            "ports": ["8188"],
            "requires_gpu": True,
        },
        "risks": [
            "GPU runtime and image strategy must be verified before descriptor work.",
        ],
        "next_step": "Research image/runtime strategy and Strix Halo backend matrix.",
        "verification": {
            "commands": ["agmind render compose --profile creative-ai --domain ci.example.com"],
            "research_refs": ["docs/adr/0014-deploy-targets-and-provisioning-boundary.md"],
        },
    }
    path.write_text(yaml.safe_dump(payload, sort_keys=True), encoding="utf-8")
    return path


def test_tool_candidate_parses_admission_boundary(tmp_path: Path) -> None:
    path = _write_candidate(tmp_path / "comfyui.yaml")

    candidate = ToolCandidate.from_yaml(path)

    assert candidate.id == "comfyui"
    assert candidate.status == "candidate"
    assert candidate.category == "creative-ai"
    assert candidate.admission.scope == "service-profile"
    assert candidate.admission.contracts == ("render", "operate")
    assert candidate.admission.service_descriptor_required is True
    assert candidate.dependencies.deploy_targets == ("ubuntu-compose", "proxmox-vm-compose")
    assert candidate.dependencies.requires_gpu is True


def test_tool_candidate_rejects_invalid_id(tmp_path: Path) -> None:
    path = _write_candidate(tmp_path / "bad.yaml", candidate_id="Comfy UI")

    with pytest.raises(ValidationError, match="id"):
        ToolCandidate.from_yaml(path)


def test_tool_candidate_rejects_unknown_admission_contract(tmp_path: Path) -> None:
    path = _write_candidate(tmp_path / "bad-contract.yaml")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["admission"]["contracts"] = ["vibe"]
    path.write_text(yaml.safe_dump(data, sort_keys=True), encoding="utf-8")

    with pytest.raises(ValidationError, match="contracts"):
        ToolCandidate.from_yaml(path)


def test_load_tool_candidates_is_sorted_by_id(tmp_path: Path) -> None:
    root = tmp_path / "tool-candidates"
    root.mkdir()
    _write_candidate(root / "z-n8n.yaml", candidate_id="n8n")
    _write_candidate(root / "a-comfyui.yaml", candidate_id="comfyui")

    loaded = load_tool_candidates(root)

    assert tuple(loaded) == ("comfyui", "n8n")


def test_repository_tool_candidates_load() -> None:
    candidates = load_tool_candidates()

    expected = {
        "comfyui",
        "external-secrets-operator",
        "n8n",
        "keycloak",
        "longhorn",
        "sops-age",
        "vault",
        "infisical",
        "harbor",
        "restic-kopia",
        "proxmox-exporter",
    }
    assert expected <= set(candidates)


def test_repository_tool_candidate_target_references_exist() -> None:
    target_ids = set(load_deploy_targets())
    candidates = load_tool_candidates()

    missing = {
        candidate.id: sorted(set(candidate.dependencies.deploy_targets) - target_ids)
        for candidate in candidates.values()
        if set(candidate.dependencies.deploy_targets) - target_ids
    }

    assert missing == {}


def test_comfyui_is_candidate_not_core_runtime() -> None:
    comfyui = load_tool_candidates()["comfyui"]

    assert comfyui.status == "candidate"
    assert comfyui.dependencies.requires_gpu is True
    assert comfyui.admission.scope == "service-profile"
    assert comfyui.dependencies.profiles == ("creative-ai",)
    assert "core" not in comfyui.dependencies.profiles


def test_n8n_is_accepted_automation_runtime() -> None:
    n8n = load_tool_candidates()["n8n"]

    assert n8n.status == "accepted"
    assert n8n.category == "automation"
    assert n8n.recommended_version == "2.22.3"
    assert n8n.dependencies.profiles == ("automation",)
    # domain-only via traefik+Authelia; the 127.0.0.1 loopback publish was removed
    # (live-audit n8n-owner-unclaimed-loopback).
    assert n8n.dependencies.ports == ()
    assert n8n.admission.service_descriptor_required is True
    assert n8n.admission.component_contract_required is True
    assert n8n.admission.image_pin_required is True


def test_sops_age_is_external_secrets_integration() -> None:
    sops = load_tool_candidates()["sops-age"]

    assert sops.category == "secrets"
    assert sops.admission.scope == "external-integration"
    assert sops.admission.service_descriptor_required is False
    assert sops.admission.contracts == ("secure",)
    assert "sops-age" in sops.dependencies.secrets_profiles


def test_longhorn_is_k3s_storage_addon_candidate() -> None:
    longhorn = load_tool_candidates()["longhorn"]

    assert longhorn.status == "candidate"
    assert longhorn.category == "storage"
    assert longhorn.recommended_version == "1.11.2"
    assert "github.com/longhorn/longhorn/releases/tag/v1.11.2" in longhorn.version_source
    assert longhorn.admission.scope == "deploy-target-addon"
    assert longhorn.admission.runtime == "kubernetes"
    assert longhorn.admission.service_descriptor_required is False
    assert longhorn.admission.component_contract_required is False
    assert "k3s" in longhorn.dependencies.deploy_targets
    assert longhorn.dependencies.storage_profiles == ("longhorn",)
    assert "recover" in longhorn.admission.contracts


def test_external_secrets_operator_is_k3s_secrets_addon_candidate() -> None:
    eso = load_tool_candidates()["external-secrets-operator"]

    assert eso.status == "candidate"
    assert eso.category == "secrets"
    assert eso.recommended_version == "2.4.1"
    assert "github.com/external-secrets/external-secrets/releases/tag/v2.4.1" in eso.version_source
    assert eso.admission.scope == "deploy-target-addon"
    assert eso.admission.runtime == "kubernetes"
    assert eso.admission.service_descriptor_required is False
    assert eso.admission.component_contract_required is False
    assert "k3s" in eso.dependencies.deploy_targets
    assert "external-secrets" in eso.dependencies.secrets_profiles
    assert "secure" in eso.admission.contracts


def test_tool_candidate_schema_includes_storage_category() -> None:
    data = json.loads((REPO_ROOT / "templates" / "schemas" / "tool-candidate.json").read_text())

    assert "storage" in data["properties"]["category"]["enum"]


def test_tool_candidate_check_script_runs() -> None:
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "checks" / "tool_candidate_check.py")],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert "tool candidates" in result.stdout.lower()


def test_tool_candidate_check_script_json_output() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "checks" / "tool_candidate_check.py"),
            "--json",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["candidate_count"] == 11
    assert payload["target_count"] == 3
    assert payload["error_count"] == 0
    assert payload["issues"] == []


def test_pre_commit_runs_tool_candidate_check() -> None:
    config = yaml.safe_load((REPO_ROOT / ".pre-commit-config.yaml").read_text())

    hooks = [hook for repo in config["repos"] if repo["repo"] == "local" for hook in repo["hooks"]]
    hook = next(item for item in hooks if item["id"] == "agmind-tool-candidate-check")

    assert hook["entry"] == ".venv/bin/python scripts/checks/tool_candidate_check.py"
    assert "templates/tool-candidates/" in hook["files"]
    assert "templates/schemas/tool-candidate\\.json" in hook["files"]
    assert "templates/services/" in hook["files"]
    assert "templates/components/" in hook["files"]
    assert "agmind/addons/" in hook["files"]


def test_ci_runs_tool_candidate_contract_gate() -> None:
    workflow = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")

    assert "templates/schemas/tool-candidate.json" in workflow
    assert "templates/tool-candidates/*.yaml" in workflow
    assert "tool-candidate-validate" in workflow
    assert "scripts/checks/tool_candidate_check.py" in workflow


def _accepted_candidate(*, candidate_id: str = "demo") -> ToolCandidate:
    return ToolCandidate.model_validate(
        {
            "id": candidate_id,
            "name": "Demo",
            "status": "accepted",
            "category": "observability",
            "summary": "Accepted test candidate.",
            "admission": {
                "scope": "service-profile",
                "runtime": "compose",
                "contracts": ["render", "operate"],
                "component_contract_required": True,
                "service_descriptor_required": True,
                "image_pin_required": True,
            },
            "dependencies": {
                "deploy_targets": ["ubuntu-compose"],
                "profiles": ["demo"],
                "ports": ["9221"],
            },
            "risks": ["test risk"],
            "next_step": "test next step",
            "verification": {
                "commands": ["agmind render compose --profile demo --domain ci.example.com"]
            },
        }
    )


def _descriptor(
    *,
    name: str = "demo",
    digest: str | None = "sha256:" + "a" * 64,
    profiles: list[str] | None = None,
    ports: list[str] | None = None,
) -> ServiceDescriptor:
    payload: dict[str, object] = {
        "name": name,
        "image": "example/demo:1.0.0",
        "tier": "ops",
        "profiles": profiles or ["demo"],
        "ports": ports or ["127.0.0.1:9221:9221"],
    }
    if digest is not None:
        payload["digest"] = digest
    return ServiceDescriptor.model_validate(payload)


def test_accepted_candidate_requires_matching_service_descriptor() -> None:
    import scripts.checks.tool_candidate_check as checker

    errors = checker.validate_tool_candidates(
        {"demo": _accepted_candidate()},
        load_deploy_targets(),
        {},
        {},
    )

    assert "demo: accepted candidate requires service descriptor demo" in errors


def test_accepted_candidate_requires_single_component_owner() -> None:
    import scripts.checks.tool_candidate_check as checker

    errors = checker.validate_tool_candidates(
        {"demo": _accepted_candidate()},
        load_deploy_targets(),
        {"demo": _descriptor()},
        {},
    )

    assert "demo: accepted candidate requires exactly one component owner" in errors


def test_accepted_candidate_requires_digest_profile_and_port_match() -> None:
    import scripts.checks.tool_candidate_check as checker

    class Contract:
        runtime = type("Runtime", (), {"service_descriptors": ("demo",)})()

    errors = checker.validate_tool_candidates(
        {"demo": _accepted_candidate()},
        load_deploy_targets(),
        {"demo": _descriptor(digest=None, profiles=["other"], ports=["127.0.0.1:9000:9000"])},
        {"owner": Contract()},
    )

    assert "demo: accepted candidate requires digest-pinned descriptor" in errors
    assert "demo: candidate profiles not present in descriptor: demo" in errors
    assert "demo: candidate ports not present in descriptor: 9221" in errors


def test_repository_accepted_candidates_match_runtime_admission() -> None:
    import scripts.checks.tool_candidate_check as checker
    from agmind.components import load_component_contracts
    from agmind.services.renderer import load_descriptors

    errors = checker.validate_tool_candidates(
        load_tool_candidates(),
        load_deploy_targets(),
        load_descriptors(),
        load_component_contracts(),
    )

    accepted_errors = [error for error in errors if error.startswith("proxmox-exporter:")]
    assert accepted_errors == []


def test_export_schemas_writes_tool_candidate_schema(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import scripts.dev.export_schemas as export_schemas

    monkeypatch.setattr(export_schemas, "SCHEMAS_DIR", tmp_path)

    out = export_schemas.export_tool_candidate_schema()
    data = json.loads(out.read_text(encoding="utf-8"))

    assert out.name == "tool-candidate.json"
    assert data["$id"].endswith("/schemas/tool-candidate.json")
    assert data["title"] == "AGmind Tool Candidate"
