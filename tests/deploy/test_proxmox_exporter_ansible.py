from __future__ import annotations

from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.backend_any

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVICES_TASKS = REPO_ROOT / "ansible" / "roles" / "services" / "tasks" / "main.yml"
SERVICES_TEMPLATE = (
    REPO_ROOT / "ansible" / "roles" / "services" / "templates" / "proxmox-pve.yml.j2"
)
GROUP_VARS = REPO_ROOT / "ansible" / "group_vars" / "all.yml"
PVE_EXAMPLE = REPO_ROOT / "templates" / "observability" / "proxmox-exporter" / "pve.yml.example"


def _services_tasks() -> list[dict[str, object]]:
    return yaml.safe_load(SERVICES_TASKS.read_text(encoding="utf-8"))


def test_proxmox_exporter_ansible_template_uses_token_auth_only() -> None:
    assert SERVICES_TEMPLATE.exists(), "missing Proxmox exporter Ansible template"

    text = SERVICES_TEMPLATE.read_text(encoding="utf-8")

    assert "default:" in text
    assert "agmind_proxmox_exporter_user" in text
    assert "agmind_proxmox_exporter_token_name" in text
    assert "agmind_proxmox_exporter_token_value" in text
    assert "agmind_proxmox_exporter_verify_ssl" in text
    assert "to_json" in text
    assert "password:" not in text
    assert "<PVE_TOKEN_VALUE>" not in text


def test_group_vars_document_proxmox_exporter_without_token_default() -> None:
    text = GROUP_VARS.read_text(encoding="utf-8")

    assert "agmind_proxmox_exporter_existing_config: false" in text
    assert "agmind_proxmox_exporter_verify_ssl: true" in text
    assert "# agmind_proxmox_exporter_user:" in text
    assert "# agmind_proxmox_exporter_token_name:" in text
    assert "# agmind_proxmox_exporter_token_value:" in text

    data = yaml.safe_load(text)
    assert "agmind_proxmox_exporter_token_value" not in data


def test_services_role_validates_proxmox_token_vars_before_compose_up() -> None:
    tasks = _services_tasks()
    task = next(
        item for item in tasks if item.get("name") == "Validate Proxmox exporter token settings"
    )

    assert "ansible.builtin.assert" in task
    conditions = " ".join(task["when"])
    assert "'proxmox' in agmind_profiles" in conditions
    assert "agmind_proxmox_exporter_existing_config" in conditions
    assert task.get("no_log") is True

    checks = " ".join(task["ansible.builtin.assert"]["that"])
    assert "agmind_proxmox_exporter_user" in checks
    assert "agmind_proxmox_exporter_token_name" in checks
    assert "agmind_proxmox_exporter_token_value" in checks


def test_services_role_creates_proxmox_config_dir_before_compose_render() -> None:
    tasks = _services_tasks()
    names = [str(item.get("name", "")) for item in tasks]

    dir_index = names.index("Ensure Proxmox exporter config directory exists")
    render_index = names.index("Render docker-compose.yml через agmind render compose")
    assert dir_index < render_index

    task = tasks[dir_index]
    assert task["ansible.builtin.file"]["path"] == "{{ agmind_config_dir }}/proxmox-exporter"
    assert task["ansible.builtin.file"]["mode"] == "0750"
    assert "'proxmox' in agmind_profiles" in " ".join(task["when"])


def test_services_role_renders_proxmox_config_without_logging_secret() -> None:
    tasks = _services_tasks()
    task = next(
        item for item in tasks if item.get("name") == "Render Proxmox exporter token config"
    )

    template = task["ansible.builtin.template"]
    assert template["src"] == "proxmox-pve.yml.j2"
    assert template["dest"] == "{{ agmind_config_dir }}/proxmox-exporter/pve.yml"
    assert template["mode"] == "0640"
    assert task.get("no_log") is True
    conditions = " ".join(task["when"])
    assert "'proxmox' in agmind_profiles" in conditions
    assert "agmind_proxmox_exporter_existing_config" in conditions


def test_services_role_validates_existing_proxmox_config_file() -> None:
    tasks = _services_tasks()
    stat_task = next(
        item for item in tasks if item.get("name") == "Check existing Proxmox exporter config"
    )
    assert stat_task["ansible.builtin.stat"]["path"] == (
        "{{ agmind_config_dir }}/proxmox-exporter/pve.yml"
    )
    assert stat_task["register"] == "agmind_proxmox_exporter_existing_config_stat"
    assert "agmind_proxmox_exporter_existing_config" in " ".join(stat_task["when"])

    assert_task = next(
        item
        for item in tasks
        if item.get("name") == "Validate existing Proxmox exporter config file"
    )
    checks = " ".join(assert_task["ansible.builtin.assert"]["that"])
    assert "agmind_proxmox_exporter_existing_config_stat.stat.exists" in checks
    assert "not agmind_proxmox_exporter_existing_config_stat.stat.isdir" in checks
    assert "agmind_proxmox_exporter_existing_config" in " ".join(assert_task["when"])


def test_pve_example_mentions_matching_ansible_vars() -> None:
    text = PVE_EXAMPLE.read_text(encoding="utf-8")

    assert "agmind_proxmox_exporter_user" in text
    assert "agmind_proxmox_exporter_token_name" in text
    assert "agmind_proxmox_exporter_token_value" in text
