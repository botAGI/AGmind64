"""Regression tests for installer preflight Ansible tasks."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.backend_any


def _ansible_playbook() -> str:
    local = Path(sys.executable).with_name("ansible-playbook")
    if local.exists():
        return str(local)
    found = shutil.which("ansible-playbook")
    if found is None:
        pytest.skip("ansible-playbook not available")
    return found


def test_preflight_kernel_version_condition_handles_kernel_suffixes(tmp_path: Path) -> None:
    """The real preflight condition must not crash on Ubuntu kernel release strings."""

    tasks_path = Path("ansible/roles/preflight/tasks/main.yml")
    tasks = yaml.safe_load(tasks_path.read_text(encoding="utf-8"))
    warn_task = next(task for task in tasks if task.get("name") == "Warn about old kernel")
    condition = warn_task["when"]

    playbook = [
        {
            "hosts": "localhost",
            "gather_facts": False,
            "tasks": [
                {
                    "name": "supported hwe kernel is not old",
                    "vars": {"kernel_check": {"stdout": "6.17.0-19-generic"}},
                    "ansible.builtin.debug": {"msg": "supported-kernel-warn"},
                    "when": condition,
                },
                {
                    "name": "older ubuntu kernel warns",
                    "vars": {"kernel_check": {"stdout": "6.8.0-63-generic"}},
                    "ansible.builtin.debug": {"msg": "old-kernel-warn"},
                    "when": condition,
                },
                {
                    "name": "newer mainline kernel is not old",
                    "vars": {"kernel_check": {"stdout": "6.18.4-generic"}},
                    "ansible.builtin.debug": {"msg": "newer-kernel-warn"},
                    "when": condition,
                },
            ],
        }
    ]
    playbook_path = tmp_path / "kernel.yml"
    playbook_path.write_text(yaml.safe_dump(playbook, sort_keys=False), encoding="utf-8")

    env = {**os.environ, "ANSIBLE_NOCOLOR": "1"}
    result = subprocess.run(
        [_ansible_playbook(), "-i", "localhost,", "-c", "local", str(playbook_path)],
        cwd=Path.cwd(),
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "old-kernel-warn" in result.stdout
    assert "supported-kernel-warn" not in result.stdout
    assert "newer-kernel-warn" not in result.stdout
