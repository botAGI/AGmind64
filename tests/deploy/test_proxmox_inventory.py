from __future__ import annotations

import json
import stat
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from agmind.deploy.proxmox_inventory import (
    ProxmoxInventoryError,
    inventory_from_tofu_outputs,
    render_inventory_yaml,
    write_inventory_from_tofu_outputs,
)

pytestmark = pytest.mark.backend_any

REPO_ROOT = Path(__file__).resolve().parents[2]


def _tofu_outputs() -> dict[str, object]:
    inventory = {
        "all": {
            "children": {
                "agmind_master": {
                    "hosts": {
                        "agmind-master-01": {
                            "ansible_host": "10.10.10.20",
                            "ansible_user": "agmind",
                            "node_name": "pve",
                            "vm_id": 120,
                        }
                    }
                },
                "agmind_workers": {
                    "hosts": {
                        "agmind-worker-01": {
                            "ansible_host": "10.10.10.21",
                            "ansible_user": "agmind",
                            "node_name": "pve",
                            "vm_id": 121,
                        }
                    }
                },
            }
        }
    }
    return {
        "ansible_inventory": {
            "sensitive": False,
            "type": ["object", {}],
            "value": inventory,
        },
        "agmind_hosts": {
            "sensitive": False,
            "type": ["object", {}],
            "value": {},
        },
    }


def test_inventory_from_tofu_outputs_enriches_ansible_groups() -> None:
    inventory = inventory_from_tofu_outputs(_tofu_outputs())

    all_block = inventory["all"]
    children = all_block["children"]
    assert "agmind_nodes" in children
    assert "agmind_master" in children
    assert "agmind_workers" in children
    assert all_block["vars"]["agmind_install_dir"] == "/opt/agmind"

    master = children["agmind_master"]["hosts"]["agmind-master-01"]
    assert master["ansible_host"] == "10.10.10.20"
    assert master["agmind_role"] == "master"
    assert master["agmind_cluster_role"] == "coordinator"
    assert master["agmind_profiles"] == ["core", "rag", "observability"]

    worker = children["agmind_workers"]["hosts"]["agmind-worker-01"]
    assert worker["ansible_host"] == "10.10.10.21"
    assert worker["agmind_role"] == "worker"
    assert worker["agmind_cluster_role"] == "worker"
    assert worker["agmind_profiles"] == ["core"]
    assert worker["agmind_worker_endpoint"] == "http://agmind-worker-01.local:8080"


def test_inventory_from_tofu_outputs_accepts_raw_inventory_value() -> None:
    raw_inventory = _tofu_outputs()["ansible_inventory"]["value"]  # type: ignore[index]

    inventory = inventory_from_tofu_outputs(raw_inventory)

    assert "agmind_nodes" in inventory["all"]["children"]


def test_inventory_from_tofu_outputs_rejects_missing_output() -> None:
    with pytest.raises(ProxmoxInventoryError, match="ansible_inventory"):
        inventory_from_tofu_outputs({"agmind_hosts": {"value": {}}})


def test_render_inventory_yaml_round_trips() -> None:
    inventory = inventory_from_tofu_outputs(_tofu_outputs())
    text = render_inventory_yaml(inventory)

    parsed = yaml.safe_load(text)

    assert (
        parsed["all"]["children"]["agmind_master"]["hosts"]["agmind-master-01"]["agmind_role"]
        == "master"
    )
    assert text.startswith("# AGmind Proxmox inventory")


def test_write_inventory_from_tofu_outputs_creates_file(tmp_path: Path) -> None:
    output_path = tmp_path / "proxmox.generated.yml"

    written = write_inventory_from_tofu_outputs(_tofu_outputs(), output_path)

    assert written == output_path
    assert output_path.exists()
    assert stat.S_IMODE(output_path.stat().st_mode) == 0o644
    parsed = yaml.safe_load(output_path.read_text(encoding="utf-8"))
    assert "agmind_nodes" in parsed["all"]["children"]


def test_write_inventory_preserves_existing_file_on_write_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output_path = tmp_path / "proxmox.generated.yml"
    old = "# old inventory\nall: {}\n"
    output_path.write_text(old, encoding="utf-8")
    output_path.chmod(0o600)

    def boom(*args: object, **kwargs: object) -> int:
        raise OSError("disk full")

    monkeypatch.setattr("agmind.core.files.os.open", boom)

    with pytest.raises(OSError, match="disk full"):
        write_inventory_from_tofu_outputs(_tofu_outputs(), output_path)

    assert output_path.read_text(encoding="utf-8") == old
    assert stat.S_IMODE(output_path.stat().st_mode) == 0o600
    assert not output_path.with_name(f".{output_path.name}.tmp").exists()


def test_script_converts_tofu_output_json(tmp_path: Path) -> None:
    input_path = tmp_path / "tofu-output.json"
    output_path = tmp_path / "inventory.yml"
    input_path.write_text(json.dumps(_tofu_outputs()), encoding="utf-8")

    result = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "ops" / "proxmox_inventory.py"),
            "--input",
            str(input_path),
            "--output",
            str(output_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert str(output_path) in result.stdout
    parsed = yaml.safe_load(output_path.read_text(encoding="utf-8"))
    assert (
        parsed["all"]["children"]["agmind_workers"]["hosts"]["agmind-worker-01"][
            "agmind_cluster_role"
        ]
        == "worker"
    )


def test_generated_inventory_is_gitignored() -> None:
    gitignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")

    assert "ansible/inventory/*.generated.yml" in gitignore
