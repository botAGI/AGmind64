"""Shared canonical profile-set list for all 14 AGmind isolaton lanes.

Single source of truth consumed by:
  - ``topology_checks.validate_topology_profiles`` (default argument)
  - ``scripts/checks/compose_profile_check.py`` (render validation)
  - ``tests/services/test_profile_sets.py`` (drift guard)

Adding a new profile to any service descriptor MUST be accompanied by a
matching entry here.  The guard test ``test_all_profile_sets_match_live_profiles``
will fail CI if the two sets diverge.
"""

from __future__ import annotations

ALL_PROFILE_SETS: tuple[tuple[str, ...], ...] = (
    ("core",),
    ("rag",),
    ("rag-milvus",),
    ("rag-weaviate",),
    ("ragflow",),
    ("observability",),
    ("ui",),
    ("security",),
    ("automation",),
    ("proxmox",),
    ("tracing",),
    ("agents-pydantic",),
    ("agents-agno",),
    ("full",),
)
"""One single-profile isolation lane per known AGmind compose profile.

Order matches the canonical catalog order (core-first, then features, then
full). Each tuple is intentionally a single profile so every profile can be
exercised in isolation without pulling in unrelated services.

Note: ``core-nginx`` was removed in Phase 08 (user decision: nginx removed
from catalog — same defect class as caddy: no templates/nginx/ conf.d, boots
default page, fails /_nginx_health health check).
"""

# ---------------------------------------------------------------------------
# Convenience helpers
# ---------------------------------------------------------------------------


def all_profile_names() -> frozenset[str]:
    """Return the flat set of profile names declared in ``ALL_PROFILE_SETS``."""
    return frozenset(p for ps in ALL_PROFILE_SETS for p in ps)


__all__ = [
    "ALL_PROFILE_SETS",
    "all_profile_names",
]
