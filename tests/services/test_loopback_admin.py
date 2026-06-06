"""Live-audit 2026-06-05 (MED openwebui-first-admin-loopback / n8n-owner-unclaimed-loopback):
the open first-admin/owner-setup must not be reachable via an unauthenticated 127.0.0.1 host
port — access is ONLY through traefik behind Authelia. So these services publish NO host port."""

from __future__ import annotations

import pytest

from agmind.services.renderer import load_descriptors

pytestmark = pytest.mark.backend_any


@pytest.mark.parametrize("svc,host", [("openwebui", "chat.agmind.dev"), ("n8n", "n8n.agmind.dev")])
def test_admin_signup_service_has_no_host_port_only_authelia(svc: str, host: str) -> None:
    d = load_descriptors()[svc]
    assert d.ports == [], f"{svc} must not publish a host port (forces traefik+Authelia)"
    assert d.routing is not None and d.routing.host == host  # still reachable via the edge
