"""Lane D regression asserts for the clean-machine pip/venv install fixes.

Three clean-machine install bugs reached the operator serially this session (all now FIXED
in ``ansible/roles/agmind_python/tasks/main.yml``); these parse-the-YAML asserts guard them
from silent reversion (no live ansible run needed):

1. A clean host has ``python3-venv`` but NOT the separate ``virtualenv`` pip package — the
   ansible ``pip`` module otherwise demands a ``virtualenv`` executable. BOTH pip tasks must
   carry ``virtualenv_command: python3 -m venv``.
2. A ``file://`` URL dep makes pip reinstall every run; the install task must use
   ``state: forcereinstall`` (NOT ``present``) to skip the fragile uninstall/rollback path
   that corrupted the venv on re-run.
3. A prior interrupted pip leaves ``~``-prefixed backup dirs + stale
   ``*.dist-info/INSTALLER*.tmp`` poison; a purge task must remove them BEFORE the install
   task so a dirty venv self-heals.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

pytestmark = pytest.mark.backend_any

_TASKS_PATH = Path("ansible/roles/agmind_python/tasks/main.yml")


def _tasks() -> list[dict[str, Any]]:
    parsed: list[dict[str, Any]] = yaml.safe_load(_TASKS_PATH.read_text(encoding="utf-8"))
    return parsed


def _name(task: dict[str, Any]) -> str:
    return str(task.get("name", ""))


def _pip_tasks(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [t for t in tasks if "ansible.builtin.pip" in t]


def test_both_pip_tasks_use_stdlib_venv_command() -> None:
    """Clean host lacks the `virtualenv` pkg → both venv-targeting pip tasks must force the
    stdlib `python3 -m venv` creator, else the ansible pip module fails on a freshly-wiped
    host. live clean-install 2026-06-07."""
    pip_tasks = _pip_tasks(_tasks())
    assert len(pip_tasks) == 2, (
        f"expected the two venv pip tasks, got {[_name(t) for t in pip_tasks]}"
    )
    for task in pip_tasks:
        args = task["ansible.builtin.pip"]
        assert args.get("virtualenv") == "{{ agmind_install_dir }}/venv", _name(task)
        assert args.get("virtualenv_command") == "python3 -m venv", (
            f"task {_name(task)!r} missing virtualenv_command: python3 -m venv"
        )


def test_install_task_uses_forcereinstall_not_present() -> None:
    """A `file://` URL dep reinstalls every run anyway; forcereinstall skips the uninstall/
    rollback dance that hit OSError on a missing INSTALLER<rand>.tmp and corrupted the venv."""
    install_task = next(t for t in _tasks() if _name(t).startswith("Install agmind package"))
    args = install_task["ansible.builtin.pip"]
    assert args.get("state") == "forcereinstall", (
        f"install task must use state: forcereinstall, got {args.get('state')!r}"
    )
    assert args.get("state") != "present"


def test_purge_task_runs_before_install_and_removes_poison() -> None:
    """A purge task must remove `~`-prefixed pip backups and stale `*.dist-info/INSTALLER*.tmp`
    BEFORE the install task, so a venv left half-installed by a prior failed run self-heals
    instead of failing bootstrap with rc=2."""
    tasks = _tasks()
    names = [_name(t) for t in tasks]

    purge_idx = next(
        (
            i
            for i, t in enumerate(tasks)
            if "ansible.builtin.shell" in t and "purge" in _name(t).lower()
        ),
        None,
    )
    assert purge_idx is not None, f"no purge shell task found among: {names}"

    install_idx = next(
        i for i, t in enumerate(tasks) if _name(t).startswith("Install agmind package")
    )
    assert purge_idx < install_idx, "purge task must run BEFORE the install task"

    purge_cmd = tasks[purge_idx]["ansible.builtin.shell"]
    # `~*` matches the `~gmind` (and any other `~`-prefixed) pip backup dirs.
    assert "site-packages/~*" in purge_cmd, f"purge must remove ~-prefixed backups: {purge_cmd!r}"
    assert "*.dist-info/INSTALLER*.tmp" in purge_cmd, (
        f"purge must remove stale INSTALLER*.tmp poison: {purge_cmd!r}"
    )

    purge_task = tasks[purge_idx]
    # Must not fail the play if there's nothing to clean / no leftovers on a first run.
    assert purge_task.get("failed_when") is False
    assert purge_task.get("changed_when") is False


def test_create_venv_self_heals_pip_less_venv_via_clear() -> None:
    """A venv whose ensurepip was interrupted has bin/python but NO pip; the create task must guard
    on `bin/pip` (not bin/python) and use `python3 -m venv --clear`, so a partial/pip-less venv
    self-heals (wipe + recreate WITH pip) on re-run. Validated end-to-end in a clean ubuntu
    container. live clean-install 2026-06-07 (the pip-less-venv rake)."""
    create = next(
        t
        for t in _tasks()
        if "ansible.builtin.command" in t
        and "venv" in str(t["ansible.builtin.command"].get("cmd", ""))
    )
    cmd = create["ansible.builtin.command"]
    assert "--clear" in str(cmd.get("cmd", "")), "create-venv must use `python3 -m venv --clear`"
    assert str(cmd.get("creates", "")).endswith("/venv/bin/pip"), (
        "guard on bin/pip (not bin/python) so a pip-less venv is recreated"
    )
