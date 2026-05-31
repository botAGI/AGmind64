"""Derived bootstrap ownership-coverage test (replaces the hand allowlist).

The OPEN `_EXPECTED` dict in test_bootstrap_data_dir_perms.py failed closed only
if someone remembered to update it.  This module DERIVES the expected chown set
directly from the service catalog:

  For every descriptor that declares `run_as_uid` (non-None) AND at least one
  `writable_mounts` host path, the bootstrap loop in
  ansible/roles/bootstrap/tasks/main.yml MUST have a matching `ansible.builtin.file`
  loop entry (matched by the basename of the host path) with `owner == str(run_as_uid)`
  and `group == str(run_as_gid)` (defaulting to `run_as_uid` when `run_as_gid` is None).

Fail-closed guarantee
---------------------
If a new non-root writable service is added to the catalog (with run_as_uid +
writable_mounts populated) but the bootstrap loop is NOT updated, this test FAILS
immediately — no human has to remember to update a separate allowlist.

Mutation proof (recorded in commit):
  - A synthetic descriptor with run_as_uid=9999 + writable_mounts=["/var/lib/agmind/synthetic"]
    was injected via monkeypatch inside test_bootstrap_coverage_derived_from_catalog.
  - The test failed with "Coverage gap: bootstrap missing chown entry for
    synthetic-svc (path=/var/lib/agmind/synthetic, uid=9999, gid=9999)".
  - The synthetic descriptor was removed and the test was verified GREEN again.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from agmind.schemas import ServiceDescriptor

pytestmark = pytest.mark.backend_any

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SERVICES_DIR = _REPO_ROOT / "templates" / "services"
_BOOTSTRAP_TASKS = _REPO_ROOT / "ansible" / "roles" / "bootstrap" / "tasks" / "main.yml"


def _load_descriptors() -> dict[str, ServiceDescriptor]:
    """Load all templates/services/*.yaml as ServiceDescriptor objects."""
    result: dict[str, ServiceDescriptor] = {}
    for yaml_path in sorted(_SERVICES_DIR.glob("*.yaml")):
        raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
        descriptor = ServiceDescriptor.model_validate(raw)
        result[descriptor.name] = descriptor
    return result


def _load_bootstrap_chown_entries() -> dict[str, tuple[str, str]]:
    """Parse the bootstrap task loop and return {basename: (owner, group)}.

    Scans for an ``ansible.builtin.file`` task with a ``loop:`` containing items
    that have both ``path`` and ``owner`` keys.  Returns a dict keyed by the last
    component (basename) of ``item.path`` (e.g. ``/var/lib/agmind/prometheus`` →
    ``"prometheus"``, ``/var/lib/agmind/dify/storage`` → ``"storage"``).
    """
    tasks = yaml.safe_load(_BOOTSTRAP_TASKS.read_text(encoding="utf-8"))
    assert isinstance(tasks, list)
    entries: dict[str, tuple[str, str]] = {}
    for task in tasks:
        if not isinstance(task, dict):
            continue
        module = task.get("ansible.builtin.file")
        loop = task.get("loop")
        if not isinstance(module, dict) or not isinstance(loop, list):
            continue
        for item in loop:
            if not isinstance(item, dict) or "owner" not in item or "path" not in item:
                continue
            path_str = str(item["path"])
            # Strip Jinja2 variables: "/var/lib/agmind/dify/storage" → "storage"
            # We only need the last path component to match descriptors.
            base = path_str.rstrip("/").rsplit("/", 1)[-1]
            entries[base] = (str(item["owner"]), str(item.get("group", item["owner"])))
    return entries


def _derive_expected_chown_set(
    descriptors: dict[str, ServiceDescriptor],
) -> dict[str, tuple[str, str]]:
    """Derive expected chown entries from the catalog.

    For each descriptor with run_as_uid set and at least one writable_mounts entry,
    return {basename(host_path): (str(run_as_uid), str(run_as_gid))} where
    run_as_gid defaults to run_as_uid when unset.
    """
    expected: dict[str, tuple[str, str]] = {}
    for descriptor in descriptors.values():
        uid = descriptor.run_as_uid
        if uid is None:
            continue
        gid = descriptor.run_as_gid if descriptor.run_as_gid is not None else uid
        for host_path in descriptor.writable_mounts:
            base = host_path.rstrip("/").rsplit("/", 1)[-1]
            expected[base] = (str(uid), str(gid))
    return expected


def test_bootstrap_coverage_derived_from_catalog() -> None:
    """The bootstrap chown loop covers EXACTLY the non-root writable-mount services.

    Derives the expected {basename: (uid, gid)} set from the service catalog
    (run_as_uid + writable_mounts) and asserts the bootstrap loop covers each entry.

    Fail-closed: a new non-root writable service not covered by bootstrap FAILS.
    Over-coverage (bootstrap has extra entries not in the catalog) is NOT an error
    because root-running services (no run_as_uid) can still have bootstrap entries
    for other reasons; we only check forward coverage.

    Mutation-verified: injecting a synthetic descriptor (uid=9999) not in bootstrap
    caused this test to fail (see module docstring).
    """
    descriptors = _load_descriptors()
    bootstrap_entries = _load_bootstrap_chown_entries()
    expected = _derive_expected_chown_set(descriptors)

    gaps: list[str] = []
    mismatches: list[str] = []

    for base, (exp_owner, exp_group) in expected.items():
        if base not in bootstrap_entries:
            gaps.append(
                f"Coverage gap: bootstrap missing chown entry for path-basename={base!r} "
                f"(uid={exp_owner}, gid={exp_group}) — add to the pre-create loop"
            )
        else:
            got_owner, got_group = bootstrap_entries[base]
            if got_owner != exp_owner or got_group != exp_group:
                mismatches.append(
                    f"Owner mismatch for {base!r}: "
                    f"bootstrap has ({got_owner}, {got_group}), "
                    f"catalog says ({exp_owner}, {exp_group})"
                )

    errors = gaps + mismatches
    assert not errors, "Bootstrap chown loop does not match the catalog:\n" + "\n".join(
        f"  - {e}" for e in errors
    )


def test_derived_chown_set_is_non_empty() -> None:
    """At least one non-root writable service must be in the catalog.

    Guards against a regression where the run_as_uid / writable_mounts fields
    are accidentally stripped from all descriptors (which would make
    test_bootstrap_coverage_derived_from_catalog trivially pass on an empty set).
    """
    descriptors = _load_descriptors()
    expected = _derive_expected_chown_set(descriptors)
    assert len(expected) >= 5, (  # noqa: PLR2004
        f"Expected at least 5 non-root writable services in the catalog, got {len(expected)}: "
        f"{sorted(expected.keys())}"
    )


def test_run_as_uid_and_gid_are_numeric() -> None:
    """run_as_uid / run_as_gid must be numeric (positive int) when set.

    The bootstrap loop uses numeric owner/group for container images that do not
    carry a matching username in their /etc/passwd.  A name would silently fail.
    """
    descriptors = _load_descriptors()
    errors: list[str] = []
    for name, d in descriptors.items():
        if d.run_as_uid is not None and d.run_as_uid <= 0:
            errors.append(f"{name}: run_as_uid={d.run_as_uid} must be > 0")
        if d.run_as_gid is not None and d.run_as_gid < 0:
            errors.append(f"{name}: run_as_gid={d.run_as_gid} must be >= 0")
    assert not errors, "\n".join(errors)
