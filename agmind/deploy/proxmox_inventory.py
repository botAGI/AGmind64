"""Convert Proxmox OpenTofu outputs into AGmind Ansible inventory."""

from __future__ import annotations

import copy
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Final, cast

import yaml

from agmind.core.files import write_text_atomic

DEFAULT_INVENTORY_PATH: Final = Path("ansible/inventory/proxmox.generated.yml")
MASTER_PROFILES: Final = ("core", "rag", "observability")
WORKER_PROFILES: Final = ("core",)

DEFAULT_ALL_VARS: Final[dict[str, object]] = {
    "agmind_install_dir": "/opt/agmind",
    "agmind_data_dir": "/var/lib/agmind",
    "agmind_config_dir": "/etc/agmind",
    "agmind_user": "agmind",
    "agmind_group": "agmind",
    "agmind_lan_only": True,
    "agmind_mdns_enabled": True,
}


class ProxmoxInventoryError(ValueError):
    """Raised when OpenTofu outputs cannot produce a valid AGmind inventory."""


def load_tofu_output_json(path: Path) -> dict[str, object]:
    """Load `tofu output -json` from disk."""
    try:
        raw: object = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ProxmoxInventoryError(f"{path}: invalid JSON: {exc}") from exc

    if not isinstance(raw, dict):
        raise ProxmoxInventoryError(f"{path}: expected a JSON object")
    return cast(dict[str, object], raw)


def inventory_from_tofu_outputs(outputs: Mapping[str, object]) -> dict[str, object]:
    """Build AGmind Ansible inventory from OpenTofu output JSON."""
    raw_inventory = _extract_inventory_value(outputs)
    all_block = _mapping(raw_inventory.get("all"), "ansible_inventory.all")
    children = _mapping(all_block.get("children"), "ansible_inventory.all.children")

    master_hosts = _hosts(children, "agmind_master")
    worker_hosts = _hosts(children, "agmind_workers", allow_empty=True)
    if not master_hosts:
        raise ProxmoxInventoryError(
            "ansible_inventory must include at least one agmind_master host"
        )

    all_vars = dict(DEFAULT_ALL_VARS)
    raw_vars = all_block.get("vars", {})
    if isinstance(raw_vars, Mapping):
        all_vars.update(cast(Mapping[str, object], raw_vars))

    normalized_master = _normalize_hosts(master_hosts, role="master")
    normalized_workers = _normalize_hosts(worker_hosts, role="worker")

    return {
        "all": {
            "vars": all_vars,
            "children": {
                "agmind_nodes": {
                    "children": {
                        "agmind_master": {},
                        "agmind_workers": {},
                    }
                },
                "agmind_master": {"hosts": normalized_master},
                "agmind_workers": {"hosts": normalized_workers},
            },
        }
    }


def render_inventory_yaml(inventory: Mapping[str, object]) -> str:
    """Render inventory mapping to YAML with a generated-file header."""
    body = yaml.safe_dump(
        dict(inventory),
        sort_keys=False,
        allow_unicode=True,
    )
    return (
        "# AGmind Proxmox inventory — generated from OpenTofu outputs.\n"
        "# Regenerate with scripts/ops/proxmox_inventory.py after tofu apply.\n"
        "# Hand-edits may be overwritten.\n\n"
        f"{body}"
    )


def write_inventory_from_tofu_outputs(
    outputs: Mapping[str, object],
    output_path: Path = DEFAULT_INVENTORY_PATH,
) -> Path:
    """Convert OpenTofu outputs and write a 0644 Ansible inventory YAML file."""
    inventory = inventory_from_tofu_outputs(outputs)
    write_text_atomic(output_path, render_inventory_yaml(inventory), mode=0o644)
    return output_path


def _extract_inventory_value(outputs: Mapping[str, object]) -> Mapping[str, object]:
    if "all" in outputs:
        return outputs

    wrapped = outputs.get("ansible_inventory")
    if not isinstance(wrapped, Mapping):
        raise ProxmoxInventoryError("tofu output JSON must include ansible_inventory")

    value = wrapped.get("value")
    if not isinstance(value, Mapping):
        raise ProxmoxInventoryError("ansible_inventory output must contain an object value")
    return cast(Mapping[str, object], value)


def _hosts(
    children: Mapping[str, object],
    group: str,
    *,
    allow_empty: bool = False,
) -> Mapping[str, object]:
    group_value = _mapping(children.get(group), f"ansible_inventory.all.children.{group}")
    hosts = group_value.get("hosts", {})
    if hosts is None and allow_empty:
        return {}
    if not isinstance(hosts, Mapping):
        raise ProxmoxInventoryError(f"{group}.hosts must be an object")
    return cast(Mapping[str, object], hosts)


def _normalize_hosts(hosts: Mapping[str, object], *, role: str) -> dict[str, dict[str, object]]:
    normalized: dict[str, dict[str, object]] = {}
    for hostname in sorted(hosts):
        host_vars = _mapping(hosts[hostname], f"host {hostname}")
        entry = copy.deepcopy(dict(host_vars))
        ansible_host = entry.get("ansible_host")
        if not isinstance(ansible_host, str) or not ansible_host:
            raise ProxmoxInventoryError(f"host {hostname} must define ansible_host")

        ansible_user = entry.get("ansible_user", "agmind")
        if not isinstance(ansible_user, str) or not ansible_user:
            raise ProxmoxInventoryError(f"host {hostname} must define ansible_user as a string")

        entry["ansible_user"] = ansible_user
        entry.setdefault("agmind_role", role)
        if role == "master":
            entry.setdefault("agmind_profiles", list(MASTER_PROFILES))
            entry.setdefault("agmind_cluster_role", "coordinator")
        else:
            entry.setdefault("agmind_profiles", list(WORKER_PROFILES))
            entry.setdefault("agmind_cluster_role", "worker")
            entry.setdefault("agmind_worker_endpoint", f"http://{hostname}.local:8080")
        normalized[hostname] = entry
    return normalized


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ProxmoxInventoryError(f"{label} must be an object")
    return cast(Mapping[str, object], value)


__all__ = [
    "DEFAULT_INVENTORY_PATH",
    "ProxmoxInventoryError",
    "inventory_from_tofu_outputs",
    "load_tofu_output_json",
    "render_inventory_yaml",
    "write_inventory_from_tofu_outputs",
]
