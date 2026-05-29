"""Guard: secret-rendering Ansible tasks in the services role carry ``no_log: true`` (F.5).

The services role renders two secret-dense templates:

* ``env.j2`` → ``.env`` — materializes POSTGRES_PASSWORD / REDIS_PASSWORD /
  GRAFANA_PASSWORD / MINIO_ROOT_PASSWORD / MYSQL_ROOT_PASSWORD / N8N_ENCRYPTION_KEY.
* ``proxmox-pve.yml.j2`` → ``pve.yml`` — the Proxmox exporter API token.

Without ``no_log: true`` the full rendered template content (plaintext secrets) is
echoed to Ansible stdout/logs under ``--diff``/``-vvv`` (threat T-066-05). This test
parses the role task file and asserts each such ``ansible.builtin.template`` task
carries ``no_log: true``; it FAILS if ``no_log`` is dropped from a secret-render task.

The heuristic is intentionally narrow and explicit (match on the known secret
template ``src`` values) so it is deterministic and does not over-match unrelated
template tasks.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.backend_any

# tests/ansible/test_secret_task_no_log.py -> parents[2] == repo root.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_SERVICES_TASKS = _REPO_ROOT / "ansible" / "roles" / "services" / "tasks" / "main.yml"

# Template ``src`` values that render plaintext secrets and therefore MUST carry no_log.
_SECRET_TEMPLATE_SRCS = {"env.j2", "proxmox-pve.yml.j2"}


def _load_tasks() -> list[dict[str, object]]:
    tasks = yaml.safe_load(_SERVICES_TASKS.read_text(encoding="utf-8"))
    assert isinstance(tasks, list), f"expected a task list in {_SERVICES_TASKS}"
    return [t for t in tasks if isinstance(t, dict)]


def _secret_render_tasks() -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    for task in _load_tasks():
        template = task.get("ansible.builtin.template")
        if isinstance(template, dict) and template.get("src") in _SECRET_TEMPLATE_SRCS:
            out.append(task)
    return out


def test_services_task_file_exists() -> None:
    assert _SERVICES_TASKS.exists(), f"missing role task file: {_SERVICES_TASKS}"


def test_both_secret_render_tasks_are_found() -> None:
    """Both known secret-rendering template tasks are present in the role."""
    found_srcs = {
        task["ansible.builtin.template"]["src"]  # type: ignore[index]
        for task in _secret_render_tasks()
    }
    assert found_srcs == _SECRET_TEMPLATE_SRCS, (
        f"expected secret-render template srcs {_SECRET_TEMPLATE_SRCS}, found {found_srcs}"
    )


def test_secret_render_tasks_carry_no_log() -> None:
    """Every secret-rendering template task carries ``no_log: true`` (T-066-05)."""
    offenders: list[str] = []
    for task in _secret_render_tasks():
        name = str(task.get("name", "<unnamed>"))
        if task.get("no_log") is not True:
            offenders.append(name)
    assert not offenders, (
        "secret-rendering Ansible tasks missing `no_log: true` "
        f"(plaintext secrets could leak under --diff/-vvv): {offenders}"
    )


def test_env_render_task_specifically_has_no_log() -> None:
    """The .env render task (``env.j2``) carries ``no_log: true``."""
    env_tasks = [
        task
        for task in _secret_render_tasks()
        if task["ansible.builtin.template"]["src"] == "env.j2"  # type: ignore[index]
    ]
    assert len(env_tasks) == 1, f"expected exactly one env.j2 render task, found {len(env_tasks)}"
    assert env_tasks[0].get("no_log") is True, ".env render task must carry no_log: true"
