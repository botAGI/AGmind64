"""Guard: ALL_PROFILE_SETS must stay in sync with the live descriptor catalog.

This test is a single source-of-truth drift guard.  If a developer adds a new
compose profile to any ``templates/services/*.yaml`` descriptor without also
registering it in ``agmind/services/profile_sets.py``, this test fails CI
before the new profile is silently missed by topology/render validation.

Mutation-verify RED contract (documented here for CI traceability):
  - Inject a 12th profile by monkeypatching ``available_profiles`` to return
    the real set plus ``{"test-only-12th"}`` → the parity assertion FAILS.
  - Revert → GREEN.
"""

from __future__ import annotations

import pytest

from agmind.services.profile_sets import ALL_PROFILE_SETS, all_profile_names
from agmind.services.renderer import available_profiles, load_descriptors

pytestmark = pytest.mark.backend_any


# ---------------------------------------------------------------------------
# Primary drift guard
# ---------------------------------------------------------------------------


def test_all_profile_sets_match_live_profiles() -> None:
    """ALL_PROFILE_SETS flattened == live profile union from load_descriptors().

    Fails if:
    - A new profile is added to a descriptor without being added to
      ALL_PROFILE_SETS (the new profile would be in live but not declared).
    - A profile is removed from ALL_PROFILE_SETS without removing it from all
      descriptors that use it (declared but absent from live union).
    """
    descriptors = load_descriptors()
    live_profiles = available_profiles(descriptors)
    declared_profiles = all_profile_names()

    declared_only = sorted(declared_profiles - live_profiles)
    live_only = sorted(live_profiles - declared_profiles)

    assert not declared_only and not live_only, (
        "ALL_PROFILE_SETS diverges from the live descriptor catalog.\n"
        f"  declared but not in catalog: {declared_only}\n"
        f"  in catalog but not declared: {live_only}\n"
        "Fix: update agmind/services/profile_sets.py to match."
    )


def test_all_profile_sets_covers_all_12_profiles() -> None:
    """Spot check: exactly 12 profiles are declared in ALL_PROFILE_SETS."""
    assert len(ALL_PROFILE_SETS) == 12, (
        f"Expected 12 profile-set lanes in ALL_PROFILE_SETS, got {len(ALL_PROFILE_SETS)}.\n"
        f"Current: {[','.join(ps) for ps in ALL_PROFILE_SETS]}"
    )


def test_service_profile_enum_matches_catalog_profiles() -> None:
    """The ServiceProfile enum is a THIRD profile source (besides the descriptors and
    ALL_PROFILE_SETS); cross-check it so a profile added to the catalog without the enum
    can't make services_for_profile raise while the renderer accepts it (audit L#43)."""
    from agmind.services.registry import ServiceProfile

    enum_values = {p.value for p in ServiceProfile}
    assert enum_values == all_profile_names(), (
        "ServiceProfile enum diverges from ALL_PROFILE_SETS/catalog: "
        f"enum-only={enum_values - all_profile_names()}, "
        f"catalog-only={all_profile_names() - enum_values}"
    )


def test_every_profile_is_boot_smoked_or_explicitly_excluded() -> None:
    """Drift guard: every selectable profile must be EITHER in the docker boot-smoke lane
    (_SMOKE_PROFILES) OR in an explicit documented exclusion set — so a new profile silently
    skipping boot validation is visible, not silent (audit 2026-06-04: ops slipped through
    config-validate-only)."""
    from tests.integration.test_compose_up_smoke import _SMOKE_PROFILES

    smoked = {p for entry in _SMOKE_PROFILES for p in entry.split(",")}
    # Profiles intentionally NOT in the docker boot-smoke lane, each with its reason:
    excluded = {
        "full",  # superset of all profiles — covered piecewise; too heavy to boot whole
        "ui",  # openwebui has no standalone boot value without a model backend
        "proxmox",  # proxmox-exporter needs a real Proxmox VE host to boot meaningfully
        # komodo needs docker.sock + /proc + /opt/agmind/stacks binds and KOMODO secrets the
        # smoke harness does not stage; the 3-svc handshake was boot-proven manually 2026-06-04.
        "ops",
    }
    uncovered = all_profile_names() - smoked - excluded
    assert not uncovered, (
        f"profiles neither boot-smoked nor explicitly excluded: {sorted(uncovered)}. "
        "Add to _SMOKE_PROFILES (tests/integration/test_compose_up_smoke.py) or to the "
        "documented exclusion set in this test with a reason."
    )


def test_all_profile_sets_are_single_profile_isolation_lanes() -> None:
    """Every entry in ALL_PROFILE_SETS is a single-profile isolation lane."""
    multi = [ps for ps in ALL_PROFILE_SETS if len(ps) != 1]
    assert not multi, (
        "ALL_PROFILE_SETS entries must each contain exactly one profile.\n"
        f"Multi-profile entries found: {multi}"
    )


# ---------------------------------------------------------------------------
# Mutation verification (inline — does NOT modify templates/services/)
# ---------------------------------------------------------------------------


def test_profile_sets_guard_fails_on_undeclared_12th_profile() -> None:
    """Mutation-verify: guard fails closed if a 12th profile appears in catalog.

    Injects a synthetic ghost profile into the live set and confirms that the
    parity assertion raises AssertionError.  No monkeypatching of the real
    function needed — we compute the augmented set manually.
    """
    descriptors = load_descriptors()
    live = available_profiles(descriptors)
    # Synthetic mutation: add a 12th profile that is not declared in ALL_PROFILE_SETS
    augmented_live = live | {"test-only-12th"}
    declared = all_profile_names()

    live_only = sorted(augmented_live - declared)
    declared_only: list[str] = sorted(declared - augmented_live)

    # Confirm the ghost profile is in the "live-only" gap set
    assert "test-only-12th" in live_only, (
        f"Synthetic injection failed — 'test-only-12th' not in live_only: {live_only}"
    )

    # Confirm the parity assertion WOULD fail when the gap is present
    with pytest.raises(AssertionError, match="ALL_PROFILE_SETS diverges"):
        assert not declared_only and not live_only, (
            "ALL_PROFILE_SETS diverges from the live descriptor catalog.\n"
            f"  declared but not in catalog: {declared_only}\n"
            f"  in catalog but not declared: {live_only}\n"
            "Fix: update agmind/services/profile_sets.py to match."
        )
