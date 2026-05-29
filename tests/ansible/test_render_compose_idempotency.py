"""Guard: the services-role compose render is idempotent, not force-recreate (G.4).

Audit finding M6.G / G.4: the ``Render docker-compose.yml`` task hard-coded
``changed_when: true`` and the ``recreate compose`` handler used
``recreate: always``. Together they force-recreate the ENTIRE stack on every
playbook run regardless of whether the rendered compose actually changed — a
non-idempotent, disruptive operation.

LOCKED fix (CONTEXT 06.7 G.4):

* The render task no longer hard-codes ``changed_when: true``. The render
  command writes to a temp/registered output with ``changed_when: false``;
  a content-based file-move task (``ansible.builtin.copy``) owns the real
  change signal and notifies the handler only on a genuine content diff.
* The ``recreate compose`` handler drops ``recreate: always`` and falls back to
  the ``community.docker.docker_compose_v2`` module default (recreate only the
  containers whose config actually changed).

This is a pure YAML-parse structural guard (no live ``ansible-playbook`` run).
It mirrors ``tests/ansible/test_secret_task_no_log.py`` (``yaml.safe_load`` →
filter dicts → select-by-key). It FAILS if the render task regains
``changed_when: true`` or the handler regains ``recreate: always``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.backend_any

# tests/ansible/test_render_compose_idempotency.py -> parents[2] == repo root.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_SERVICES_TASKS = _REPO_ROOT / "ansible" / "roles" / "services" / "tasks" / "main.yml"
_SERVICES_HANDLERS = _REPO_ROOT / "ansible" / "roles" / "services" / "handlers" / "main.yml"

# Substring selector for the render task. GOTCHA G.4-a: ``cmd`` is a single
# ``>-`` folded scalar string, so substring-match on the folded value — do NOT
# split on whitespace.
_RENDER_CMD_MARKER = "agmind render compose"
# Name selector for the handler under guard.
_RECREATE_HANDLER_NAME = "recreate compose"


def _load_tasks() -> list[dict[str, object]]:
    tasks = yaml.safe_load(_SERVICES_TASKS.read_text(encoding="utf-8"))
    assert isinstance(tasks, list), f"expected a task list in {_SERVICES_TASKS}"
    return [t for t in tasks if isinstance(t, dict)]


def _load_handlers() -> list[dict[str, object]]:
    handlers = yaml.safe_load(_SERVICES_HANDLERS.read_text(encoding="utf-8"))
    assert isinstance(handlers, list), f"expected a handler list in {_SERVICES_HANDLERS}"
    return [h for h in handlers if isinstance(h, dict)]


def _render_command_tasks() -> list[dict[str, object]]:
    """Tasks whose ``ansible.builtin.command.cmd`` invokes ``agmind render compose``."""
    out: list[dict[str, object]] = []
    for task in _load_tasks():
        command = task.get("ansible.builtin.command")
        if not isinstance(command, dict):
            continue
        cmd = command.get("cmd")
        if isinstance(cmd, str) and _RENDER_CMD_MARKER in cmd:
            out.append(task)
    return out


def _recreate_compose_handler() -> dict[str, object]:
    for handler in _load_handlers():
        if handler.get("name") == _RECREATE_HANDLER_NAME:
            return handler
    raise AssertionError(f"missing '{_RECREATE_HANDLER_NAME}' handler in {_SERVICES_HANDLERS}")


def test_role_yaml_files_exist() -> None:
    assert _SERVICES_TASKS.exists(), f"missing role task file: {_SERVICES_TASKS}"
    assert _SERVICES_HANDLERS.exists(), f"missing role handler file: {_SERVICES_HANDLERS}"


def test_render_command_task_is_found() -> None:
    """Exactly one task runs ``agmind render compose`` via ansible.builtin.command."""
    render_tasks = _render_command_tasks()
    assert len(render_tasks) == 1, (
        f"expected exactly one 'agmind render compose' command task, found {len(render_tasks)}"
    )


def test_render_task_does_not_hardcode_changed_when_true() -> None:
    """The render command no longer hard-codes ``changed_when: true`` (G.4).

    The render must be content-diff-driven: the render command writes to a
    temp/registered output and is marked ``changed_when: false`` (a sibling
    content-based copy owns the real change signal). Asserting
    ``changed_when is not True`` is robust to either ``changed_when: false`` or
    a registered-result expression.
    """
    render_tasks = _render_command_tasks()
    assert render_tasks, "no 'agmind render compose' command task found"
    offenders = [
        str(task.get("name", "<unnamed>"))
        for task in render_tasks
        if task.get("changed_when") is True
    ]
    assert not offenders, (
        "render-compose task still hard-codes `changed_when: true` "
        f"(forces recreate on every run, not content-diff-driven): {offenders}"
    )


def test_render_task_notifies_recreate_compose() -> None:
    """A real content change must still fire the ``recreate compose`` handler.

    The notify may live on the render command task or on the content-based
    file-move task that activates the rendered output — assert the render flow
    notifies ``recreate compose`` somewhere (so idempotency does not silently
    disable redeploy on a genuine change).
    """

    def _notifies(task: dict[str, object]) -> bool:
        notify = task.get("notify")
        if notify == _RECREATE_HANDLER_NAME:
            return True
        if isinstance(notify, list):
            return _RECREATE_HANDLER_NAME in notify
        return False

    assert any(_notifies(task) for task in _load_tasks()), (
        f"no task notifies '{_RECREATE_HANDLER_NAME}' — a real compose change "
        "would not trigger a redeploy"
    )


def test_recreate_compose_handler_drops_recreate_always() -> None:
    """The ``recreate compose`` handler no longer uses ``recreate: always`` (G.4).

    Dropping ``recreate: always`` lets ``community.docker.docker_compose_v2``
    use its module default: recreate only containers whose config changed,
    instead of tearing down and recreating the entire stack.
    """
    handler = _recreate_compose_handler()
    block = handler.get("community.docker.docker_compose_v2")
    assert isinstance(block, dict), (
        "'recreate compose' handler must use community.docker.docker_compose_v2"
    )
    assert block.get("recreate") != "always", (
        "'recreate compose' handler still sets `recreate: always` — every notify "
        "force-recreates the whole stack instead of only changed containers"
    )
