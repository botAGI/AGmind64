"""Regression coverage for Docker Compose plugin version detection."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.backend_any


def _docker_tasks() -> list[dict[str, object]]:
    path = Path("ansible/roles/docker/tasks/main.yml")
    tasks = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(tasks, list)
    return [task for task in tasks if isinstance(task, dict)]


def test_docker_compose_version_parser_handles_short_and_verbose_output() -> None:
    role_text = Path("ansible/roles/docker/tasks/main.yml").read_text(encoding="utf-8")
    assert "split | last | regex_replace('^v', '')" in role_text
    assert "regex_search('[0-9]+" not in role_text

    tasks = _docker_tasks()
    parse_tasks = [
        task
        for task in tasks
        if task.get("name")
        in {
            "Parse Docker Compose plugin version",
            "Parse final Docker Compose plugin version",
        }
    ]

    assert len(parse_tasks) == 2
    for task in parse_tasks:
        assert "ansible.builtin.set_fact" in task


def test_docker_compose_final_version_recheck_retries_after_package_install() -> None:
    recheck = next(
        task for task in _docker_tasks() if task.get("name") == "Re-check Docker Compose plugin version"
    )

    assert recheck["retries"] == 5
    assert recheck["delay"] == 2
    until = str(recheck["until"])
    assert "docker_compose_final_version_cmd.rc == 0" in until
    assert "docker_compose_final_version_cmd.stdout | trim | length" in until
