"""Crash-loop blockers: non-root container data dirs must be pre-created with the
right UID in bootstrap.

The Docker daemon auto-creates a missing bind-mount host dir as root:root 0755, so
non-root container users cannot write and crash-loop:
  prometheus -> uid 65534 ("open /prometheus/queries.active: permission denied")
  grafana    -> uid 472
  loki       -> uid 10001 (after the ruler dir moves onto the writable /loki mount)
  n8n        -> uid 1000  ("EACCES open /home/node/.n8n/config")
  elasticsearch -> uid 1000:0 ("failed to obtain node locks ... not writable")

The ServiceDescriptor schema has no `user` field, so the fix lives in bootstrap:
pre-create each data dir owned by the matching numeric uid/gid (NOT the agmind
user, which the image's non-root uid still can't write to).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.backend_any

_REPO_ROOT = Path(__file__).resolve().parents[2]
_BOOTSTRAP_TASKS = _REPO_ROOT / "ansible" / "roles" / "bootstrap" / "tasks" / "main.yml"

# data-dir basename -> (owner, group) the container image runs as
_EXPECTED = {
    "prometheus": ("65534", "65534"),
    "grafana": ("472", "472"),
    "loki": ("10001", "10001"),
    "n8n": ("1000", "1000"),
    "elasticsearch": ("1000", "0"),
}


def _load_tasks() -> list[dict[str, object]]:
    tasks = yaml.safe_load(_BOOTSTRAP_TASKS.read_text(encoding="utf-8"))
    assert isinstance(tasks, list)
    return [t for t in tasks if isinstance(t, dict)]


def test_bootstrap_precreates_data_dirs_with_container_uids() -> None:
    found: dict[str, tuple[str, str]] = {}
    for task in _load_tasks():
        module = task.get("ansible.builtin.file")
        loop = task.get("loop")
        if not isinstance(module, dict) or not isinstance(loop, list):
            continue
        for item in loop:
            if not isinstance(item, dict) or "owner" not in item:
                continue
            base = str(item.get("path", "")).rstrip("/").rsplit("/", 1)[-1]
            if base in _EXPECTED:
                found[base] = (str(item["owner"]), str(item.get("group")))
    assert found == _EXPECTED
