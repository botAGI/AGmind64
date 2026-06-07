"""de-slop 2026-06-07 — guards that the hand-maintained lists which used to ripple ~8 gates per
descriptor stay single-sourced / derived, so they can't silently diverge."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.backend_any


def test_k3s_excludes_at_least_the_compose_only_frozenset() -> None:
    """SLOP-H2: templates/deploy-targets/k3s.yaml excluded_services overlapped the renderer's
    COMPOSE_ONLY_DOCKER_SOCKET_SERVICES. The renderer already omits the compose-only set, so the
    real invariant is a SUPERSET: k3s must exclude every compose-only service (it may exclude more
    — e.g. alloy, k8s-renderable but routed to a k8s-native collector). Gate the relationship so a
    compose-only service can't silently fall out of the k3s exclusion."""
    from agmind.services.kubernetes_renderer import COMPOSE_ONLY_DOCKER_SOCKET_SERVICES

    k3s = yaml.safe_load(
        (Path(__file__).resolve().parents[2] / "templates/deploy-targets/k3s.yaml").read_text()
    )
    excluded = set(k3s["runtime"]["excluded_services"])
    assert set(COMPOSE_ONLY_DOCKER_SOCKET_SERVICES) <= excluded


def test_docker_api_not_hand_listed_in_cross_profile() -> None:
    """SLOP-H3: docker_api cross-profile consumes are derived (CLOSURE_PULLED_CAPABILITIES), not
    6 hand-listed pairs that rippled on every new consumer."""
    from agmind.services.topology_checks import (
        CLOSURE_PULLED_CAPABILITIES,
        KNOWN_CROSS_PROFILE_CONSUMES,
    )

    assert "docker_api" in CLOSURE_PULLED_CAPABILITIES
    assert not any(cap == "docker_api" for _, cap in KNOWN_CROSS_PROFILE_CONSUMES)
