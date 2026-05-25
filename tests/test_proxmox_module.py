from __future__ import annotations

import re
from pathlib import Path

import pytest

from agmind.deploy.targets import load_deploy_targets

pytestmark = pytest.mark.backend_any

REPO_ROOT = Path(__file__).resolve().parents[1]
MODULE_DIR = REPO_ROOT / "infra" / "proxmox" / "vm-compose"


def _read(relative_path: str) -> str:
    return (MODULE_DIR / relative_path).read_text(encoding="utf-8")


def test_proxmox_target_points_to_opentofu_module() -> None:
    target = load_deploy_targets()["proxmox-vm-compose"]

    assert target.provisioner.module == "infra/proxmox/vm-compose"
    assert target.provisioner.outputs == ("ansible_inventory", "agmind_hosts")
    assert "tofu -chdir=infra/proxmox/vm-compose validate" in target.verification.commands


def test_proxmox_module_has_expected_files() -> None:
    expected = {
        ".gitignore",
        "README.md",
        "main.tf",
        "outputs.tf",
        "providers.tf",
        "terraform.tfvars.example",
        "variables.tf",
        "versions.tf",
    }

    existing = {path.name for path in MODULE_DIR.iterdir()} if MODULE_DIR.exists() else set()

    assert expected <= existing


def test_proxmox_module_pins_current_provider_line() -> None:
    versions = _read("versions.tf")

    assert 'required_version = ">= 1.8.0"' in versions
    assert 'source  = "bpg/proxmox"' in versions
    assert 'version = "~> 0.93.0"' in versions


def test_proxmox_provider_uses_sensitive_variables() -> None:
    providers = _read("providers.tf")
    variables = _read("variables.tf")

    assert "endpoint  = var.proxmox_endpoint" in providers
    assert "api_token = var.proxmox_api_token" in providers
    assert "insecure  = var.proxmox_insecure" in providers
    assert re.search(
        r'variable "proxmox_api_token"\s+\{[^}]*sensitive\s+=\s+true',
        variables,
        flags=re.DOTALL,
    )
    assert 'default = "PVEAPIToken=' not in variables
    assert "pm_api_token_secret" not in providers


def test_proxmox_module_defines_cloud_init_and_vm_resources() -> None:
    main = _read("main.tf")

    assert 'resource "proxmox_virtual_environment_file" "cloud_init"' in main
    assert 'content_type = "snippets"' in main
    assert "source_raw {" in main
    assert 'resource "proxmox_virtual_environment_vm" "agmind"' in main
    assert "clone {" in main
    assert "vm_id = each.value.template_vm_id" in main
    assert re.search(
        r"user_data_file_id\s+=\s+proxmox_virtual_environment_file\.cloud_init\[each\.key\]\.id",
        main,
    )
    assert "bridge = each.value.network_bridge" in main
    assert "agent {" in main


def test_proxmox_module_outputs_inventory_bridge_contract() -> None:
    outputs = _read("outputs.tf")

    assert 'output "agmind_hosts"' in outputs
    assert 'output "ansible_inventory"' in outputs
    assert "agmind_nodes" in outputs
    assert "agmind_master" in outputs
    assert "agmind_workers" in outputs
    assert "ansible_host" in outputs
    assert "ansible_user" in outputs


def test_proxmox_module_does_not_track_local_state_or_real_tfvars() -> None:
    ignored = _read(".gitignore")

    assert ".terraform/" in ignored
    assert "*.tfstate" in ignored
    assert "*.tfvars" in ignored
    assert "!terraform.tfvars.example" in ignored
