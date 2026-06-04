"""Review HIGH bootstrap-user-docker-group-absent: on a clean host docker/render/video
don't exist yet, so `useradd -G <missing>` exits rc=6 and creates NO user. The bootstrap
must pre-create those groups before the user task AND must not swallow a real failure."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.backend_any

_TASKS = Path("ansible/roles/bootstrap/tasks/main.yml")


def _tasks() -> list[dict]:
    return yaml.safe_load(_TASKS.read_text(encoding="utf-8"))


def test_groups_are_precreated_before_the_user_task() -> None:
    tasks = _tasks()
    names = [str(t.get("name", "")) for t in tasks]
    user_idx = next(i for i, n in enumerate(names) if n == "Create agmind user")
    group_idx = next(
        i for i, t in enumerate(tasks) if "ansible.builtin.group" in t and t.get("loop")
    )
    assert group_idx < user_idx, "groups must be ensured BEFORE the user is created"
    group_task = tasks[group_idx]["ansible.builtin.group"]
    assert group_task["state"] == "present"
    assert set(tasks[group_idx]["loop"]) >= {"docker", "render", "video"}


def test_user_task_does_not_swallow_failures() -> None:
    tasks = _tasks()
    user_task = next(t for t in tasks if t.get("name") == "Create agmind user")
    # The blanket `failed_when: false` masked the rc=6 "no such group" abort — it is gone.
    assert "failed_when" not in user_task, (
        "Create agmind user must not blanket-swallow failures (failed_when: false hid the "
        "clean-host group-absent abort)"
    )
    assert set(user_task["ansible.builtin.user"]["groups"]) == {"docker", "render", "video"}
