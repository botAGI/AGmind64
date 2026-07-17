"""P0.3 / 15-04 (SPEC-15.3): fail-closed authelia topology gate.

A selection whose routed descriptors use `chain-llm` / `chain-internal` (both
forwardAuth to authelia:9091 per templates/traefik/dynamic/middlewares.yml) but
that does NOT include authelia would deploy healthy containers whose protected
routes all 500 — an absent auth boundary nobody catches. The gate turns that
into a loud render-time ValueError, guarded by traefik_enabled so local renders
and the 14 CI isolation lanes (traefik_enabled=False) are unaffected.
"""

from __future__ import annotations

import pytest

from agmind.schemas import ServiceDescriptor
from agmind.services.renderer import load_descriptors, render_compose
from agmind.services.topology_checks import AUTHELIA_GATED_CHAINS, check_authelia_required

pytestmark = pytest.mark.backend_any


def _routed_descriptor(name: str, chain: str) -> ServiceDescriptor:
    return ServiceDescriptor.model_validate(
        {
            "name": name,
            "image": f"example/{name}:1.0.0",
            "tier": "app",
            "purpose": "Synthetic routed service",
            "ports": ["127.0.0.1:8080:8080"],
            "routing": {"host": f"{name}.agmind.dev", "middleware_chain": chain},
        }
    )


def _plain_descriptor(name: str) -> ServiceDescriptor:
    return ServiceDescriptor.model_validate(
        {
            "name": name,
            "image": f"example/{name}:1.0.0",
            "tier": "app",
            "purpose": "Synthetic unrouted service",
            "ports": ["127.0.0.1:8081:8081"],
        }
    )


# ---------- check_authelia_required (pure function) ----------


def test_gated_chains_are_the_authelia_forwardauth_chains() -> None:
    # chain-public carries no authelia middleware — it must stay outside the gate.
    assert AUTHELIA_GATED_CHAINS == {"chain-llm", "chain-internal"}


def test_check_flags_gated_chain_without_authelia() -> None:
    for chain in sorted(AUTHELIA_GATED_CHAINS):
        selected = {"svc-x": _routed_descriptor("svc-x", chain)}
        violations = check_authelia_required(selected)
        assert len(violations) == 1
        assert "svc-x" in violations[0]
        assert chain in violations[0]


def test_check_clean_when_authelia_selected() -> None:
    descriptors = load_descriptors()
    selected = {
        "svc-x": _routed_descriptor("svc-x", "chain-llm"),
        "authelia": descriptors["authelia"],
    }
    assert check_authelia_required(selected) == []


def test_check_skips_public_chain_and_unrouted_services() -> None:
    selected = {
        "svc-pub": _routed_descriptor("svc-pub", "chain-public"),
        "svc-plain": _plain_descriptor("svc-plain"),
    }
    assert check_authelia_required(selected) == []


# ---------- render_compose wiring (real descriptors) ----------


def test_render_compose_raises_without_authelia() -> None:
    descriptors = load_descriptors()
    selected = [descriptors["traefik"], descriptors["llama-llm"]]  # llama-llm = chain-llm
    with pytest.raises(ValueError, match="authelia"):
        render_compose(selected, traefik_enabled=True)


def test_render_compose_error_is_actionable() -> None:
    descriptors = load_descriptors()
    selected = [descriptors["traefik"], descriptors["llama-llm"]]
    with pytest.raises(ValueError, match="security.*profile|--no-traefik"):
        render_compose(selected, traefik_enabled=True)


def test_render_compose_passes_with_authelia_and_redis() -> None:
    descriptors = load_descriptors()
    selected = [
        descriptors["traefik"],
        descriptors["llama-llm"],
        descriptors["authelia"],
        descriptors["redis"],  # authelia depends_on redis (session store)
    ]
    compose = render_compose(selected, traefik_enabled=True)
    assert "llama-llm" in compose["services"]
    assert "authelia" in compose["services"]


def test_render_compose_gate_skipped_when_traefik_disabled() -> None:
    descriptors = load_descriptors()
    selected = [descriptors["traefik"], descriptors["llama-llm"]]
    compose = render_compose(selected, traefik_enabled=False)
    assert "llama-llm" in compose["services"]
