"""Live-audit 2026-06-05 (HIGH ssrf-proxy-src-based-acl): the ssrf-proxy squid ACL must
cage egress by DESTINATION (deny internal/RFC1918/link-local), not by SOURCE. The old rule
`http_access allow docker_nets` (src 172.16/10) let sandboxed untrusted code reach internal
LAN/cluster services on 80/443. Booting squid is live-gated; this guards the config contract."""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.backend_any

_SQUID_CONF = Path(__file__).resolve().parents[2] / "templates" / "squid" / "squid.conf"


def _conf() -> str:
    return _SQUID_CONF.read_text(encoding="utf-8")


def test_squid_denies_internal_destinations() -> None:
    conf = _conf()
    # every private/internal range must be a denied DESTINATION
    for cidr in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8", "169.254.0.0/16"):
        assert f"dst {cidr}" in conf, f"missing dst deny for {cidr}"
    assert "http_access deny to_internal" in conf
    # cloud metadata is denied first
    assert "http_access deny metadata" in conf


def test_squid_does_not_allow_by_source() -> None:
    """The src-based allow that defeated the cage must be gone."""
    conf = _conf()
    assert "allow docker_nets" not in conf
    assert "acl docker_nets src" not in conf


def test_squid_deny_to_internal_precedes_allow() -> None:
    """deny to_internal must come BEFORE the public allow, or internal would leak through."""
    conf = _conf()
    assert "http_access deny to_internal" in conf and "http_access allow Safe_ports" in conf
    assert conf.index("deny to_internal") < conf.index("allow Safe_ports")
