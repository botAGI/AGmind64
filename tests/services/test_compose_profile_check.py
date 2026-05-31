"""WR-01: compose_profile_check.py must be wired into CI/tests — not dead code.

``scripts/checks/compose_profile_check.py`` validates that every AGmind compose
profile renders without error across all 13 isolation lanes.  Before this fix
the script had zero references outside itself: not in DEFAULT_CHECKS, not in any
CI job, not called by any test.  A profile that rendered to an empty set or raised
an exception would be silently invisible to CI.

This module closes that gap by calling ``validate_compose_profiles()`` in pytest,
making it part of the ``-m backend_any`` suite and therefore gated by CI
``test-cpu``.

Mutation-verify RED contract (inline, no filesystem modification):
  - Inject a synthetic profile-set tuple (``("nonexistent-profile-xyz",)``) into
    the profile_sets argument.  ``select_services`` returns an empty set for an
    unknown profile → ``validate_compose_profiles`` marks that lane FAILED (ok=False).
  - The test MUST go RED (assertion error) when any lane is FAILED.
  - Restore → GREEN.
"""

from __future__ import annotations

import pytest

from agmind.services.profile_sets import ALL_PROFILE_SETS

pytestmark = pytest.mark.backend_any


# ---------------------------------------------------------------------------
# Primary gate: all 13 isolation lanes must render without error
# ---------------------------------------------------------------------------


def test_all_13_profile_lanes_render_ok() -> None:
    """validate_compose_profiles() must report ok=True for all 13 isolation lanes.

    Wires compose_profile_check into the test suite so it actually gates CI.
    The script was previously dead code (WR-01): not in DEFAULT_CHECKS, not in
    any CI job, not referenced by any test.

    Fail-closed guarantee:
      - A profile lane that renders to an empty service set fails this test.
      - A profile lane that raises a render exception fails this test.
    """
    from scripts.checks.compose_profile_check import validate_compose_profiles

    report = validate_compose_profiles(ALL_PROFILE_SETS)

    failed_lanes = [lane for lane in report.lanes if not lane.ok]
    assert not failed_lanes, (
        "One or more compose profile lanes failed to render:\n"
        + "\n".join(f"  {','.join(lane.profiles)}: {lane.error}" for lane in failed_lanes)
    )
    assert report.ok, (
        f"compose_profile_check reports {report.error_count} failed lane(s); "
        "all 13 isolation profiles must render without error."
    )


def test_compose_profile_check_covers_all_declared_lanes() -> None:
    """validate_compose_profiles() must exercise exactly the 13 declared lanes."""
    from scripts.checks.compose_profile_check import validate_compose_profiles

    report = validate_compose_profiles(ALL_PROFILE_SETS)
    assert len(report.lanes) == len(ALL_PROFILE_SETS), (
        f"Expected {len(ALL_PROFILE_SETS)} lanes in the report, got {len(report.lanes)}."
    )


def test_compose_profile_check_each_lane_has_services() -> None:
    """Every rendered isolation lane must select at least one service.

    An empty selection is a catalog/profile drift bug: a profile declared in
    ALL_PROFILE_SETS but absent from every descriptor would silently produce
    zero services.
    """
    from scripts.checks.compose_profile_check import validate_compose_profiles

    report = validate_compose_profiles(ALL_PROFILE_SETS)
    empty = [lane for lane in report.lanes if lane.ok and lane.service_count == 0]
    assert not empty, (
        "Profile lanes rendered OK but selected zero services: "
        + ", ".join(",".join(lane.profiles) for lane in empty)
        + ". Check that the profile is assigned to at least one descriptor."
    )


# ---------------------------------------------------------------------------
# Mutation-verify: gate goes RED when a bad lane is injected
# ---------------------------------------------------------------------------


def test_compose_profile_check_fails_on_nonexistent_profile() -> None:
    """Mutation-verify: inject a nonexistent profile lane → report.ok is False.

    This confirms the gate is not vacuous: a real render failure propagates to
    report.ok=False and the primary test would catch it.

    The mutation is the addition of a bogus profile tuple; no filesystem
    changes needed.
    """
    from scripts.checks.compose_profile_check import validate_compose_profiles

    # Inject a lane with a profile that no descriptor carries.
    mutated_sets = ALL_PROFILE_SETS + (("nonexistent-profile-xyz-mutation",),)
    report = validate_compose_profiles(mutated_sets)

    # The injected lane must fail (empty selection).
    failed = [lane for lane in report.lanes if not lane.ok]
    assert failed, (
        "Mutation injection failed — nonexistent profile lane was not reported as failed. "
        "The gate is not catching empty-selection render failures."
    )
    assert not report.ok, (
        "report.ok must be False when a lane fails — the primary test gate would not "
        "catch this regression."
    )

    # Confirm exactly the mutated lane failed and the real 13 lanes passed.
    bad_profiles = [",".join(lane.profiles) for lane in failed]
    assert "nonexistent-profile-xyz-mutation" in bad_profiles, (
        f"Expected the mutated lane to be in failed list, got: {bad_profiles}"
    )
    real_failed = [lane for lane in failed if "nonexistent-profile-xyz-mutation" not in lane.profiles]
    assert not real_failed, (
        f"Real profile lanes also failed during mutation test: "
        + ", ".join(",".join(lane.profiles) for lane in real_failed)
    )
