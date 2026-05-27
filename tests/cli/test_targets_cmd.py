from __future__ import annotations

import json
from pathlib import Path

import pytest

from agmind.deploy.targets import DeploymentTarget, load_deploy_targets

pytestmark = pytest.mark.backend_any


def test_validate_repository_deploy_targets_have_no_local_drift() -> None:
    from agmind.deploy.target_checks import validate_deploy_targets

    errors = validate_deploy_targets(load_deploy_targets())

    assert errors == []


def test_validate_deploy_targets_reports_missing_playbook(tmp_path: Path) -> None:
    from agmind.deploy.target_checks import validate_deploy_targets

    target = DeploymentTarget.model_validate(
        {
            "id": "demo-compose",
            "name": "Demo Compose",
            "status": "supported",
            "summary": "Demo supported target.",
            "runtime": {
                "kind": "compose",
                "renderer": "agmind render compose",
                "profiles": ["core"],
            },
            "provisioner": {"kind": "none"},
            "configurator": {
                "kind": "ansible",
                "inventory_source": "static-inventory",
                "playbooks": ["ansible/missing.yml"],
            },
            "storage_profile": "local-paths",
            "secrets_profile": "env-files",
            "verification": {"commands": ["agmind render compose --profile core"]},
        }
    )

    errors = validate_deploy_targets({"demo-compose": target}, repo_root=tmp_path)

    assert "demo-compose: configurator playbook not found: ansible/missing.yml" in errors


def test_targets_list_json_includes_target_ladder(capsys: pytest.CaptureFixture[str]) -> None:
    from agmind.cli import targets_cmd

    rc = targets_cmd.cmd_list(as_json=True)

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["summary"] == {"experimental": 1, "research": 1, "supported": 1}
    proxmox = next(target for target in payload["targets"] if target["id"] == "proxmox-vm-compose")
    assert proxmox["status"] == "experimental"
    assert proxmox["runtime"]["kind"] == "compose"
    assert proxmox["provisioner"]["module"] == "infra/proxmox/vm-compose"


def test_targets_status_reports_verification_commands(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from agmind.cli import targets_cmd

    rc = targets_cmd.cmd_status("proxmox-vm-compose")

    assert rc == 0
    out = capsys.readouterr().out
    assert "proxmox-vm-compose" in out
    assert "Status: experimental" in out
    assert "Provisioner: opentofu-proxmox" in out
    assert "tofu -chdir=infra/proxmox/vm-compose validate" in out


def test_targets_status_missing_returns_error(capsys: pytest.CaptureFixture[str]) -> None:
    from agmind.cli import targets_cmd

    rc = targets_cmd.cmd_status("missing-target")

    assert rc == 1
    assert "Deployment target 'missing-target' not found" in capsys.readouterr().err


def test_targets_validate_uses_target_gate(capsys: pytest.CaptureFixture[str]) -> None:
    from agmind.cli import targets_cmd

    rc = targets_cmd.cmd_validate()

    assert rc == 0
    assert "deployment targets OK: 3 targets" in capsys.readouterr().out


def test_targets_validate_json_uses_structured_report(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from agmind.cli import targets_cmd

    rc = targets_cmd.cmd_validate(as_json=True)

    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["target_count"] == 3
    assert payload["error_count"] == 0
    assert payload["warning_count"] == 0
    assert payload["info_count"] == 0
    assert payload["issues"] == []
