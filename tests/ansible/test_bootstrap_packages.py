"""Regression coverage for bootstrap apt package names."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.backend_any


def test_bootstrap_installs_package_that_provides_lspci() -> None:
    tasks_path = Path("ansible/roles/bootstrap/tasks/main.yml")
    tasks = yaml.safe_load(tasks_path.read_text(encoding="utf-8"))
    install_task = next(task for task in tasks if task.get("name") == "Install base packages")
    packages = install_task["ansible.builtin.apt"]["name"]

    assert "pciutils" in packages
    assert "lspci" not in packages
