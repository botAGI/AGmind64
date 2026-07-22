"""The Ansible `services` role must materialize EVERY secret file the Python installer does.

`_materialize_runtime_files` (steps/configs.py) and `agmind ops rotate-secrets` write the DB and
Authelia `_FILE` secrets from the single-source registries in `agmind.install.secret_keys`
(DB_SECRET_FILES, AUTHELIA_SECRET_FILES). The `ansible-playbook install.yml -t services` path is
the parallel installer, and it silently covered only postgres + mysql — so an ansible install of
authelia (or agent-db) rendered `_FILE` env pointing at files nothing wrote, and the container
failed at boot ("session: option 'secret' is required"). This gate fails if the ansible role's
secret-file tasks drift from the registries (Phase 15 deferred item)."""

from __future__ import annotations

import pytest
import yaml

from agmind.install.secret_keys import AUTHELIA_SECRET_FILES, DB_SECRET_FILES
from agmind.services.renderer import REPO_ROOT

pytestmark = pytest.mark.backend_any

_SERVICES_TASKS = REPO_ROOT / "ansible" / "roles" / "services" / "tasks" / "main.yml"

# The task names that write files under /var/lib/agmind/secrets/ via a copy+loop.
_SECRET_TASK_NAMES = {
    "Save DB server passwords to secret files",
    "Save authelia secret files",
}


def _ansible_secret_dests() -> set[str]:
    """Collect every `dest` filename materialized by the role's secret-file loop tasks."""
    tasks = yaml.safe_load(_SERVICES_TASKS.read_text(encoding="utf-8"))
    dests: set[str] = set()
    for task in tasks:
        if task.get("name") not in _SECRET_TASK_NAMES:
            continue
        for item in task.get("loop", []):
            dests.add(item["dest"])
    return dests


def test_ansible_materializes_every_db_secret_file() -> None:
    dests = _ansible_secret_dests()
    missing = {fname for _svc, fname, _env in DB_SECRET_FILES} - dests
    assert not missing, (
        f"ansible services role does not write DB secret file(s) {sorted(missing)} — "
        f"the -t services install path would render *_FILE env pointing at absent files"
    )


def test_ansible_materializes_every_authelia_secret_file() -> None:
    dests = _ansible_secret_dests()
    missing = {fname for _svc, fname, _env in AUTHELIA_SECRET_FILES} - dests
    assert not missing, (
        f"ansible services role does not write authelia secret file(s) {sorted(missing)} — "
        f"an ansible install of authelia would fail at boot on a missing *_FILE secret"
    )


def test_ansible_secret_dests_are_all_registry_backed() -> None:
    """No orphan secret file in the role that isn't in a registry (keeps the two in lockstep)."""
    dests = _ansible_secret_dests()
    known = {fname for _s, fname, _e in DB_SECRET_FILES} | {
        fname for _s, fname, _e in AUTHELIA_SECRET_FILES
    }
    orphan = dests - known
    assert not orphan, f"ansible writes secret file(s) {sorted(orphan)} not in any registry"
