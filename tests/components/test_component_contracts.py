from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from agmind.components import ComponentContract, load_component_contracts
from agmind.services.renderer import load_descriptors

pytestmark = pytest.mark.backend_any


def _write_contract(path: Path, *, component_id: str = "ollama") -> Path:
    payload = {
        "id": component_id,
        "kind": "inference",
        "core": {
            "upstream": "ollama/ollama",
            "recommended_version": "0.12.10",
            "current_pin": "0.12.10",
            "update_policy": "compatible-minor",
        },
        "runtime": {
            "service_descriptors": ["ollama"],
            "compose_profiles": ["cpu"],
            "ports": ["ollama:11434"],
        },
        "provides": ["llm.openai_compatible"],
        "requires": {"capabilities": ["compute.cpu"]},
        "conflicts": {"services": ["vllm"]},
        "verification": {
            "smoke": ["agmind doctor --profile cpu"],
            "schema_refs": ["templates/schemas/service.json"],
        },
    }
    path.write_text(yaml.safe_dump(payload, sort_keys=True), encoding="utf-8")
    return path


def test_component_contract_parses_runtime_boundaries(tmp_path: Path) -> None:
    path = _write_contract(tmp_path / "ollama.yaml")

    contract = ComponentContract.from_yaml(path)

    assert contract.id == "ollama"
    assert contract.kind == "inference"
    assert contract.core.current_pin == "0.12.10"
    assert contract.runtime.service_descriptors == ("ollama",)
    assert contract.runtime.ports == ("ollama:11434",)
    assert contract.provides == ("llm.openai_compatible",)
    assert contract.requires.capabilities == ("compute.cpu",)
    assert contract.conflicts.services == ("vllm",)
    assert contract.verification.smoke == ("agmind doctor --profile cpu",)


def test_load_component_contracts_is_sorted_by_id(tmp_path: Path) -> None:
    root = tmp_path / "components"
    root.mkdir()
    _write_contract(root / "z-ollama.yaml", component_id="ollama")
    _write_contract(root / "a-postgres.yaml", component_id="postgres")

    loaded = load_component_contracts(root)

    assert tuple(loaded) == ("ollama", "postgres")
    assert loaded["postgres"].runtime.service_descriptors == ("ollama",)


def test_component_contract_rejects_invalid_id(tmp_path: Path) -> None:
    path = _write_contract(tmp_path / "bad.yaml", component_id="Bad ID")

    with pytest.raises(ValidationError, match="id"):
        ComponentContract.from_yaml(path)


def test_component_contract_rejects_unknown_update_policy(tmp_path: Path) -> None:
    path = _write_contract(tmp_path / "bad-policy.yaml")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["core"]["update_policy"] = "wing-it"
    path.write_text(yaml.safe_dump(data, sort_keys=True), encoding="utf-8")

    with pytest.raises(ValidationError, match="update_policy"):
        ComponentContract.from_yaml(path)


def test_repository_component_contracts_load() -> None:
    contracts = load_component_contracts()

    expected = {
        "agmind-core",
        "llama-cpp-gfx1151",
        "dify",
        "ragflow",
        "automation-stack",
        "stateful-services",
        "edge-proxy",
        "observability-stack",
        "app-interfaces",
        "model-catalog",
    }
    assert expected <= set(contracts)


def test_repository_component_contracts_have_version_policy() -> None:
    contracts = load_component_contracts()

    missing_policy = [
        contract.id
        for contract in contracts.values()
        if not contract.core.recommended_version or not contract.core.update_policy
    ]
    assert missing_policy == []


def test_every_component_service_descriptor_exists() -> None:
    contracts = load_component_contracts()
    descriptors = load_descriptors()

    missing: list[str] = []
    for contract in contracts.values():
        for service_name in contract.runtime.service_descriptors:
            if service_name not in descriptors:
                missing.append(f"{contract.id}:{service_name}")

    assert missing == []


def test_ragflow_component_declares_runtime_dependency_capabilities() -> None:
    contracts = load_component_contracts()

    required = set(contracts["ragflow"].requires.capabilities)

    assert {
        "llm_inference",
        "embedding_inference",
        "search_index",
        "mysql_db",
        "object_storage",
        "redis_cache",
    } <= required


def test_automation_stack_declares_n8n_runtime_boundary() -> None:
    contracts = load_component_contracts()

    automation = contracts["automation-stack"]

    assert automation.kind == "app"
    assert automation.runtime.service_descriptors == ("n8n",)
    assert automation.runtime.compose_profiles == ("automation",)
    assert "workflow_automation" in automation.provides


def test_observability_stack_declares_operator_console_runtime_services() -> None:
    contracts = load_component_contracts()

    observability = contracts["observability-stack"]

    assert {
        "uptime-kuma",
        "homarr",
        "watchtower",
        "dozzle",
        "netdata",
    } <= set(observability.runtime.service_descriptors)
    assert "service_health_dashboard" in observability.provides
    assert "container_update_monitor" in observability.provides


def test_every_service_descriptor_has_exactly_one_component_owner() -> None:
    contracts = load_component_contracts()
    descriptors = load_descriptors()

    owners: dict[str, list[str]] = {}
    for contract in contracts.values():
        for service_name in contract.runtime.service_descriptors:
            owners.setdefault(service_name, []).append(contract.id)

    missing = sorted(set(descriptors) - set(owners))
    duplicates = {
        service_name: component_ids
        for service_name, component_ids in sorted(owners.items())
        if len(component_ids) > 1
    }

    assert missing == []
    assert duplicates == {}


def test_component_check_script_runs() -> None:
    import subprocess
    import sys

    repo_root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, str(repo_root / "scripts" / "checks" / "component_check.py")],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert "component contracts" in result.stdout.lower()


def test_component_check_script_json_output() -> None:
    import subprocess
    import sys

    repo_root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [sys.executable, str(repo_root / "scripts" / "checks" / "component_check.py"), "--json"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["contract_count"] == 12
    assert payload["service_count"] == 46  # +phoenix (Arize Phoenix tracing) 2026-06-07
    assert payload["error_count"] == 0
    assert payload["issues"] == []


def test_component_check_rejects_unknown_profile_set(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import scripts.checks.component_check as component_check

    monkeypatch.setattr(
        component_check,
        "PROFILE_SETS",
        (("core", "missing-profile"),),
    )

    rc = component_check.main(["--json"])

    payload = json.loads(capsys.readouterr().out)
    assert rc == 1
    assert payload["ok"] is False
    assert payload["issues"][0]["message"] == (
        "core,missing-profile: unknown profile(s): missing-profile"
    )
