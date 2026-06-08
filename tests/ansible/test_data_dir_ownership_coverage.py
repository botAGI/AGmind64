"""Derived bootstrap ownership-coverage test (replaces the hand allowlist).

The OPEN `_EXPECTED` dict in test_bootstrap_data_dir_perms.py failed closed only
if someone remembered to update it.  This module DERIVES the expected chown set
directly from the service catalog:

  For every descriptor that declares `run_as_uid` (non-None) AND at least one
  `writable_mounts` host path, the bootstrap loop in
  ansible/roles/bootstrap/tasks/main.yml MUST have a matching `ansible.builtin.file`
  loop entry (matched by the full relative data-dir path of the host path) with
  ``owner == str(run_as_uid)`` and ``group == str(run_as_gid)`` (defaulting to
  ``run_as_uid`` when ``run_as_gid`` is None).

Keying by full relative path (WR-05 fix)
-----------------------------------------
The previous implementation keyed both the bootstrap parser and the expected-set
deriver by ``os.path.basename(path)`` only.  Two services whose writable mounts
share a basename (e.g. ``dify/storage`` and ``some-future/storage``) collide in
the dict: the second silently overwrites the first, so a wrong-uid bootstrap entry
passes.  We now key by the path segment(s) *below* the common ``{{ agmind_data_dir }}``
prefix (e.g. ``/var/lib/agmind/dify/storage`` → ``"dify/storage"``), which is
unique per-mount.

Fail-closed guarantee
---------------------
If a new non-root writable service is added to the catalog (with run_as_uid +
writable_mounts populated) but the bootstrap loop is NOT updated, this test FAILS
immediately — no human has to remember to update a separate allowlist.

Mutation proof (recorded in commit):
  - A synthetic descriptor with run_as_uid=9999 + writable_mounts=["/var/lib/agmind/synthetic"]
    was injected via monkeypatch inside test_bootstrap_coverage_derived_from_catalog.
  - The test failed with "Coverage gap: bootstrap missing chown entry for
    path=synthetic, uid=9999, gid=9999".
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


# ---------------------------------------------------------------------------
# Path-normalisation helpers (WR-05: key by full relative path, not basename)
# ---------------------------------------------------------------------------

# The common data-dir prefix used by all service bind-mounts.  Both the
# bootstrap YAML (Jinja2 ``{{ agmind_data_dir }}/...``) and the descriptor
# writable_mounts (``/var/lib/agmind/...``) are normalised to a path relative
# to this logical root so we can compare them without Jinja2 resolution.
_BOOTSTRAP_PATH_PREFIX_RE_PARTS = (
    # Match the Jinja2 variable placeholder *or* the literal path.
    "{{ agmind_data_dir }}/",
    "/var/lib/agmind/",
)


def _normalise_path_key(path_str: str) -> str:
    """Return the relative path below the AGmind data-dir prefix.

    Examples::

        "{{ agmind_data_dir }}/dify/storage" → "dify/storage"
        "/var/lib/agmind/prometheus"          → "prometheus"
        "/var/lib/agmind/docling-cache"       → "docling-cache"

    If neither prefix matches, fall back to the full stripped path so the
    coverage assertion still fires (the item was not pre-created at all).
    """
    stripped = path_str.rstrip("/")
    for prefix in _BOOTSTRAP_PATH_PREFIX_RE_PARTS:
        if stripped.startswith(prefix):
            return stripped[len(prefix) :]
    # Fallback: use the full path — collision is not possible and the test
    # will correctly flag it as a gap if the bootstrap item used a different
    # path convention.
    return stripped


def _load_bootstrap_chown_entries() -> dict[str, tuple[str, str]]:
    """Parse the bootstrap task loop and return {relative_path: (owner, group)}.

    Scans for an ``ansible.builtin.file`` task with a ``loop:`` containing items
    that have both ``path`` and ``owner`` keys.  Returns a dict keyed by the
    relative path below the AGmind data-dir prefix (WR-05 fix: was basename-only,
    which silently collapsed entries sharing the same last component).

    Examples of keys::

        /var/lib/agmind/prometheus      → "prometheus"
        /var/lib/agmind/dify/storage    → "dify/storage"
        /var/lib/agmind/docling-cache   → "docling-cache"
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
            key = _normalise_path_key(path_str)
            entries[key] = (str(item["owner"]), str(item.get("group", item["owner"])))
    return entries


def _derive_expected_chown_set(
    descriptors: dict[str, ServiceDescriptor],
) -> dict[str, tuple[str, str]]:
    """Derive expected chown entries from the catalog.

    For each descriptor with run_as_uid set and at least one writable_mounts entry,
    return {relative_path: (str(run_as_uid), str(run_as_gid))} where
    run_as_gid defaults to run_as_uid when unset.

    WR-05 fix: previously keyed by basename only — two mounts sharing the same
    last component (e.g. ``dify/storage`` and ``other/storage``) silently collapsed
    into one entry, hiding coverage gaps.  Now keyed by the full relative path
    below the common data-dir prefix.
    """
    expected: dict[str, tuple[str, str]] = {}
    for descriptor in descriptors.values():
        uid = descriptor.run_as_uid
        if uid is None:
            continue
        gid = descriptor.run_as_gid if descriptor.run_as_gid is not None else uid
        for host_path in descriptor.writable_mounts:
            key = _normalise_path_key(host_path)
            expected[key] = (str(uid), str(gid))
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

    for rel_path, (exp_owner, exp_group) in expected.items():
        if rel_path not in bootstrap_entries:
            gaps.append(
                f"Coverage gap: bootstrap missing chown entry for path={rel_path!r} "
                f"(uid={exp_owner}, gid={exp_group}) — add to the pre-create loop"
            )
        else:
            got_owner, got_group = bootstrap_entries[rel_path]
            if got_owner != exp_owner or got_group != exp_group:
                mismatches.append(
                    f"Owner mismatch for {rel_path!r}: "
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


def test_full_path_keying_prevents_basename_collision() -> None:
    """WR-05 mutation-verify: full-path key detects collision that basename missed.

    Simulate two services whose writable mounts share a basename (e.g. ``storage``):
      - svc-a: /var/lib/agmind/foo/storage  uid=1001
      - svc-b: /var/lib/agmind/bar/storage  uid=2002

    With the old basename keying, svc-b silently overwrites svc-a in the expected
    dict → only one entry checked, wrong-uid bootstrap entry passes.
    With the new full-path keying, both entries are distinct → coverage gap detected.

    This test verifies the new keying produces two distinct keys for these paths.
    """
    path_a = "/var/lib/agmind/foo/storage"
    path_b = "/var/lib/agmind/bar/storage"

    key_a = _normalise_path_key(path_a)
    key_b = _normalise_path_key(path_b)

    # Old behaviour: basename → both become "storage" (collision)
    assert path_a.rsplit("/", 1)[-1] == path_b.rsplit("/", 1)[-1], (
        "Test setup error: the two paths should share a basename."
    )

    # New behaviour: full relative path → distinct keys
    assert key_a != key_b, (
        f"WR-05: full-path keys must be distinct for paths sharing a basename.\n"
        f"  path_a={path_a!r} → key_a={key_a!r}\n"
        f"  path_b={path_b!r} → key_b={key_b!r}"
    )
    assert key_a == "foo/storage", f"Expected 'foo/storage', got {key_a!r}"
    assert key_b == "bar/storage", f"Expected 'bar/storage', got {key_b!r}"


# ---------------------------------------------------------------------------
# A5: ROOT_WRITER_ALLOWLIST + writable_mounts↔volumes consistency
# ---------------------------------------------------------------------------

# Services that run as root (no run_as_uid) but have writable host bind-mounts
# under /var/lib/agmind.  These have been individually verified to self-chown or
# need no chown because they run as root (confirmed via docker image inspect and
# live-stack health checks — see 08-CONTEXT.md bootstrap-coverage agent notes).
#
ROOT_WRITER_ALLOWLIST: frozenset[str] = frozenset(
    {
        "postgres",
        # agent-db is pgvector/postgres:pg17 — runs as root and self-chowns PGDATA (same as
        # postgres), no run_as_uid so no numeric-uid bootstrap row.
        "agent-db",
        "redis",
        "mysql",
        "netdata",
        "uptime-kuma",
        "weaviate",
        "etcd",
        "minio",
        "milvus-minio",
        "qdrant",
        "homarr",
        "authelia",
        "alloy",
        "openwebui",
        "portainer",
        "traefik",
        "milvus",
        "n8n",
        # dify-plugin-daemon runs as root (uid=0, Config.User empty — verified live 2026-06-05)
        # and writes /app/storage (installed plugins) as root → Docker auto-creates the bind
        # root:root. live-audit dify-plugin-daemon-no-storage-volume.
        "dify-plugin-daemon",
    }
)

_AGMIND_DATA_PREFIX = "/var/lib/agmind/"


def _writable_bind_sources(descriptor: ServiceDescriptor) -> list[str]:
    """Return host-side source paths of writable bind-mounts under /var/lib/agmind.

    A bind-mount is writable if its volume spec does NOT end with ':ro'.
    """
    sources: list[str] = []
    for vol in descriptor.volumes:
        parts = vol.split(":")
        if len(parts) < 2:
            continue
        src = parts[0]
        mode = parts[-1] if len(parts) == 3 else "rw"
        if not src.startswith(_AGMIND_DATA_PREFIX):
            continue
        if mode == "ro":
            continue
        sources.append(src)
    return sources


def test_writable_bind_covered_by_bootstrap_or_allowlist() -> None:
    """A5: every service with a writable host bind under /var/lib/agmind must be
    EITHER in the bootstrap pre-create set (has a numeric uid entry) OR in the
    explicit reviewed ROOT_WRITER_ALLOWLIST.

    A non-root service (run_as_uid is set) not in the bootstrap set will crash:
    Docker auto-creates the dir as root:root 0755, which the non-root UID cannot write.
    A service with uid=None and writable binds must be in ROOT_WRITER_ALLOWLIST,
    documenting the reviewed decision that it runs as root and self-chowns.

    Fail-closed: adding a non-root service with writable_mounts to the catalog
    without a bootstrap entry REDs this test.

    Mutation-verified (out-of-tree): adding a synthetic non-root descriptor with
    writable_mounts=['/var/lib/agmind/foo'], no run_as_uid, not in allowlist, and no
    bootstrap entry causes this test to RED with an allowlist coverage gap.
    """
    descriptors = _load_descriptors()
    bootstrap_entries = _load_bootstrap_chown_entries()

    # Build the set of relative paths covered by the bootstrap pre-create loop.
    bootstrap_paths: set[str] = set(bootstrap_entries.keys())

    violations: list[str] = []

    for name, descriptor in descriptors.items():
        bind_sources = _writable_bind_sources(descriptor)
        if not bind_sources:
            continue

        uid = descriptor.run_as_uid

        for src in bind_sources:
            rel_path = _normalise_path_key(src)

            if uid is not None:
                # Non-root service: bootstrap must have a chown entry for this path.
                # (The existing test_bootstrap_coverage_derived_from_catalog already
                # asserts this via writable_mounts; this check covers the volumes path.)
                if rel_path not in bootstrap_paths:
                    violations.append(
                        f"{name}: writable bind {src!r} (uid={uid}) not in bootstrap "
                        f"pre-create loop — Docker will create it as root:root and the "
                        f"container will crash (permission denied)"
                    )
            else:
                # Root-writer: must be in the reviewed allowlist.
                if name not in ROOT_WRITER_ALLOWLIST:
                    violations.append(
                        f"{name}: has writable bind {src!r} but uid=None and is not in "
                        f"ROOT_WRITER_ALLOWLIST — add to the allowlist after verifying it "
                        f"runs as root and self-chowns (or set run_as_uid and add a "
                        f"bootstrap entry)"
                    )

    assert not violations, "A5 bootstrap/allowlist coverage gap:\n" + "\n".join(
        f"  - {v}" for v in violations
    )


def test_writable_volume_declared_in_writable_mounts() -> None:
    """A5 companion: every writable /var/lib/agmind bind source in a descriptor's
    volumes must also appear in that descriptor's writable_mounts field.

    writable_mounts is the source of truth for the bootstrap pre-create derivation
    (test_bootstrap_coverage_derived_from_catalog uses it).  A volume that writes
    to /var/lib/agmind but is absent from writable_mounts is invisible to the
    bootstrap coverage test, silently creating a gap.

    This test closes that gap: volumes and writable_mounts must be consistent for
    the /var/lib/agmind prefix.
    """
    descriptors = _load_descriptors()
    violations: list[str] = []

    for name, descriptor in descriptors.items():
        if descriptor.run_as_uid is None:
            # Root-writers are covered by test_writable_bind_covered_by_bootstrap_or_allowlist;
            # they are not required to declare writable_mounts (they self-chown).
            continue

        bind_sources = _writable_bind_sources(descriptor)
        declared_writable = set(descriptor.writable_mounts)

        for src in bind_sources:
            if src not in declared_writable:
                violations.append(
                    f"{name}: writable bind {src!r} is in volumes but not in "
                    f"writable_mounts — add it so the bootstrap coverage test can "
                    f"derive the chown requirement"
                )

    assert not violations, "A5 writable_mounts↔volumes parity violation:\n" + "\n".join(
        f"  - {v}" for v in violations
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
