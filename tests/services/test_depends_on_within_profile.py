"""Fail-closed dep-graph guard: depends_on and consumes must be satisfiable
within each service's declared profiles.

Правила Карпатого item #12:
  A service hard-pointing at a backend host via ``depends_on`` MUST guarantee
  co-deploy: the dependency must share at least one compose profile with the
  consumer, OR be reachable via a *_stack capability closure.

This guard enforces that constraint over the full live catalog.  A 43rd service
added with a cross-profile broken ``depends_on`` or ``consumes`` will fail CI
here before reaching a crash-loop on ``agmind install``.

Mutation-verify RED contracts (documented for CI traceability):
  - Inject a synthetic descriptor with ``depends_on=["nonexistent-svc"]`` in
    profile ``core`` → guard FAILS naming the (service, dep, profile) triple.
  - Inject a synthetic descriptor that ``consumes=["llm_inference"]`` in
    profile ``security`` (no llm provider shares that profile) → guard FAILS.
  - Both proofs run with synthetic descriptors only; templates/services/ is
    never modified.
"""

from __future__ import annotations

import pytest

from agmind.schemas import ServiceDescriptor
from agmind.services.renderer import load_descriptors
from agmind.services.topology_checks import (
    check_consumes_within_profile,  # type: ignore[attr-defined]   # added in GREEN
    check_depends_on_within_profile,  # type: ignore[attr-defined]  # added in GREEN
)

pytestmark = pytest.mark.backend_any


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_descriptor(
    name: str,
    profiles: list[str],
    depends_on: list[str] | None = None,
    consumes: list[str] | None = None,
    provides: list[str] | None = None,
) -> ServiceDescriptor:
    return ServiceDescriptor(
        name=name,
        image=f"{name}:test",
        tier="storage",
        purpose=f"test {name}",
        profiles=profiles,
        depends_on=depends_on or [],
        consumes=consumes or [],
        provides=provides or [],
    )


# ---------------------------------------------------------------------------
# Real catalog: depends_on within profile (GREEN gate)
# ---------------------------------------------------------------------------


def test_depends_on_within_profile_real_catalog() -> None:
    """Every depends_on target shares at least one profile with its consumer.

    For each descriptor D in the real catalog and each name N in D.depends_on:
    - N must exist in the catalog.
    - D.profiles ∩ all_descriptors[N].profiles must be non-empty.

    Violations are aggregated and reported as a named list.
    """
    descriptors = load_descriptors()
    violations = check_depends_on_within_profile(descriptors)

    assert not violations, (
        "depends_on cross-profile violations detected in real catalog:\n"
        + "\n".join(f"  {v}" for v in sorted(violations))
    )


# ---------------------------------------------------------------------------
# Real catalog: consumes satisfiability within profile (GREEN gate)
# ---------------------------------------------------------------------------


def test_consumes_within_profile_real_catalog() -> None:
    """Every consumed capability has a provider sharing at least one profile.

    For each descriptor D and each capability C in D.consumes, there must exist
    at least one descriptor P in the catalog such that:
    - C ∈ P.provides
    - D.profiles ∩ P.profiles is non-empty

    Violations are aggregated and reported as a named list.
    """
    descriptors = load_descriptors()
    violations = check_consumes_within_profile(descriptors)

    assert not violations, (
        "consumes/provides cross-profile violations detected in real catalog:\n"
        + "\n".join(f"  {v}" for v in sorted(violations))
    )


# ---------------------------------------------------------------------------
# Mutation-verify: cross-profile broken depends_on → guard fails closed
# ---------------------------------------------------------------------------


def test_depends_on_guard_fails_closed_on_cross_profile_broken_dep() -> None:
    """Mutation-verify: a depends_on target missing from consumer's profiles → FAIL.

    Synthetic descriptor ``fake-consumer`` declares profile ``security`` and
    depends_on ``["nonexistent-svc"]``.  The real catalog has no service named
    ``nonexistent-svc`` at all, so the guard must report a violation.
    """
    real_descriptors = load_descriptors()
    descriptors = dict(real_descriptors)
    descriptors["fake-consumer"] = _make_descriptor(
        name="fake-consumer",
        profiles=["security"],
        depends_on=["nonexistent-svc"],
    )

    violations = check_depends_on_within_profile(descriptors)

    matching = [v for v in violations if "fake-consumer" in v and "nonexistent-svc" in v]
    assert matching, (
        "check_depends_on_within_profile did NOT catch the synthetic cross-profile "
        f"broken dep (fake-consumer→nonexistent-svc). violations={violations}"
    )


def test_depends_on_guard_fails_closed_on_shared_profile_missing() -> None:
    """Mutation-verify: depends_on target exists but shares no profile → FAIL.

    ``fake-consumer`` is in profile ``security``.
    ``fake-provider`` exists but is ONLY in profile ``core``.
    They share no profile → guard must report a violation.
    """
    real_descriptors = load_descriptors()
    descriptors = dict(real_descriptors)
    descriptors["fake-provider"] = _make_descriptor(
        name="fake-provider",
        profiles=["core"],
    )
    descriptors["fake-consumer"] = _make_descriptor(
        name="fake-consumer",
        profiles=["security"],
        depends_on=["fake-provider"],
    )

    violations = check_depends_on_within_profile(descriptors)

    matching = [v for v in violations if "fake-consumer" in v and "fake-provider" in v]
    assert matching, (
        "check_depends_on_within_profile did NOT catch shared-profile-missing violation "
        f"(fake-consumer∈security→fake-provider∈core). violations={violations}"
    )


# ---------------------------------------------------------------------------
# Mutation-verify: cross-profile broken consumes → guard fails closed
# ---------------------------------------------------------------------------


def test_consumes_guard_fails_closed_on_cross_profile_broken_capability() -> None:
    """Mutation-verify: consumed capability has no provider in consumer's profiles → FAIL.

    ``fake-consumer`` is in profile ``security`` and consumes ``llm_inference``.
    All real ``llm_inference`` providers (llama-llm) are in profile ``core``.
    ``security`` ∩ ``core`` = ∅ → guard must report a violation.
    """
    real_descriptors = load_descriptors()
    descriptors = dict(real_descriptors)
    descriptors["fake-consumer"] = _make_descriptor(
        name="fake-consumer",
        profiles=["security"],
        consumes=["llm_inference"],
    )

    violations = check_consumes_within_profile(descriptors)

    matching = [v for v in violations if "fake-consumer" in v and "llm_inference" in v]
    assert matching, (
        "check_consumes_within_profile did NOT catch the synthetic cross-profile "
        f"broken consumes (fake-consumer∈security→llm_inference). violations={violations}"
    )
