"""Regression coverage for the Ansible Python package install role."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.backend_any


def test_agmind_package_install_is_not_editable_from_operator_home() -> None:
    tasks_path = Path("ansible/roles/agmind_python/tasks/main.yml")
    tasks = yaml.safe_load(tasks_path.read_text(encoding="utf-8"))
    install_task = next(task for task in tasks if task.get("name") == "Install agmind package (dev extras)")

    pip_args = install_task["ansible.builtin.pip"]
    assert pip_args["name"] == ["agmind[dev] @ file://{{ playbook_dir }}/.."]
    assert "editable" not in pip_args
    assert "become_user" not in install_task
