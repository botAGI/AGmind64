"""Crash-loop blockers: non-root container data dirs must be pre-created with the
right UID in bootstrap.

The Docker daemon auto-creates a missing bind-mount host dir as root:root 0755, so
non-root container users cannot write and crash-loop:
  prometheus -> uid 65534 ("open /prometheus/queries.active: permission denied")
  grafana    -> uid 472
  loki       -> uid 10001 (after the ruler dir moves onto the writable /loki mount)
  n8n        -> uid 1000  ("EACCES open /home/node/.n8n/config")
  elasticsearch -> uid 1000:0 ("failed to obtain node locks ... not writable")

The ServiceDescriptor schema now carries optional `run_as_uid`, `run_as_gid`, and
`writable_mounts` hints (added in 07-05 GREEN).  The DERIVED coverage test in
tests/ansible/test_data_dir_ownership_coverage.py asserts the bootstrap loop covers
exactly those services.

This file is kept for the `_load_tasks` helper and for documentation context.
The open `_EXPECTED` hand-allowlist assertion has been SUPERSEDED by the catalog-
derived test; that test fails closed while the old dict would silently pass open.
"""

from __future__ import annotations

from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
_BOOTSTRAP_TASKS = _REPO_ROOT / "ansible" / "roles" / "bootstrap" / "tasks" / "main.yml"


def _load_tasks() -> list[dict[str, object]]:
    tasks = yaml.safe_load(_BOOTSTRAP_TASKS.read_text(encoding="utf-8"))
    assert isinstance(tasks, list)
    return [t for t in tasks if isinstance(t, dict)]
