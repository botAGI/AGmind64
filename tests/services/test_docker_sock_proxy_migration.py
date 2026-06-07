"""Live-audit 2026-06-05 (HIGH docker-sock-raw-mount-blast-radius): read-only Docker-API
consumers reach the API through the docker-socket-proxy, NOT a raw /var/run/docker.sock bind
(which is a root-equivalent escape). Throwaway-validated: cadvisor/traefik/alloy all work via
the tecnativa proxy over tcp. This guards the cadvisor migration."""

from __future__ import annotations

import pytest

from agmind.services.renderer import load_descriptors

pytestmark = pytest.mark.backend_any


def test_cadvisor_uses_socket_proxy_not_raw_sock() -> None:
    d = load_descriptors()["cadvisor"]
    assert not any("docker.sock" in v for v in d.volumes), "cadvisor must not bind the raw socket"
    assert "--docker=tcp://docker-socket-proxy:2375" in d.command
    assert "docker_api" in d.consumes  # pulls the proxy into the closure
    assert "docker-socket-proxy" in d.depends_on


def test_alloy_uses_socket_proxy_not_raw_sock() -> None:
    d = load_descriptors()["alloy"]
    assert not any("docker.sock" in v for v in d.volumes), "alloy must not bind the raw socket"
    assert "docker_api" in d.consumes
    assert "docker-socket-proxy" in d.depends_on


def test_alloy_config_points_at_proxy() -> None:
    from pathlib import Path

    cfg = Path(__file__).resolve().parents[2] / "templates/observability/alloy/config.alloy"
    text = cfg.read_text()
    assert "unix:///var/run/docker.sock" not in text  # no raw socket
    assert text.count('host             = "tcp://docker-socket-proxy:2375"') == 2


def test_traefik_uses_socket_proxy_not_raw_sock() -> None:
    d = load_descriptors()["traefik"]
    assert not any("docker.sock" in v for v in d.volumes), "traefik must not bind the raw socket"
    assert "--providers.docker.endpoint=tcp://docker-socket-proxy:2375" in d.command
    assert "docker_api" in d.consumes


def test_proxy_co_deploys_with_traefik_in_core() -> None:
    """The proxy must be profile-listed in core/full (the --no-tui render path includes profile
    members only) so traefik always has its Docker-API gateway."""
    p = load_descriptors()["docker-socket-proxy"]
    assert "core" in p.profiles and "full" in p.profiles
    # read-only: traefik's watch needs EVENTS; everything mutating stays denied
    assert p.env["EVENTS"] == "1" and p.env["POST"] == "0"


def test_no_unexpected_service_binds_raw_docker_socket() -> None:
    """Guard the docker-sock blast radius. The 3 high-value read-only consumers (traefik edge,
    cadvisor, alloy) are migrated to the proxy. Remaining raw-socket holders are either WRITE
    managers/updaters the read-only proxy can't serve, or read-only consumers still pending
    migration (tracked so the set can't silently grow). live-audit docker-sock-blast-radius."""
    descriptors = load_descriptors()
    # ONLY services that need WRITE (pull/recreate/manage containers) — the read-only proxy can't
    # serve them — or the proxy itself may bind the raw socket. EVERY read-only API consumer is
    # migrated to the proxy. live-audit docker-sock-blast-radius.
    write_holders = {"portainer", "komodo-periphery", "watchtower", "docker-socket-proxy"}
    for name, d in descriptors.items():
        if any("/var/run/docker.sock" in v for v in d.volumes):
            assert name in write_holders, (
                f"{name} binds the raw docker socket but is not a WRITE manager / the proxy"
            )
    # every read-only consumer is off the raw socket (via the proxy)
    for migrated in ("traefik", "cadvisor", "alloy", "netdata", "homarr", "dozzle"):
        assert not any("/var/run/docker.sock" in v for v in descriptors[migrated].volumes), migrated


def test_netdata_uses_socket_proxy_not_raw_sock() -> None:
    d = load_descriptors()["netdata"]
    assert not any("docker.sock" in v for v in d.volumes), "netdata must not bind the raw socket"
    assert d.env["DOCKER_HOST"] == "tcp://docker-socket-proxy:2375"
    assert "docker_api" in d.consumes


def test_dozzle_uses_socket_proxy_not_raw_sock() -> None:
    d = load_descriptors()["dozzle"]
    assert not any("docker.sock" in v for v in d.volumes), "dozzle must not bind the raw socket"
    assert d.env["DOCKER_HOST"] == "tcp://docker-socket-proxy:2375"
    assert "docker_api" in d.consumes
    assert d.depends_on == []  # NO depends_on (cross-profile hard-raise); consume pulls the proxy


def test_socket_proxy_isolated_on_mgmt_net() -> None:
    """live-audit 2026-06-07 (SEC-1): the env-leaking socket-proxy is caged on the internal
    mgmt-net so no shared-net app can reach it; only its docker_api consumers are dual-homed."""
    from agmind.services.renderer import _EXTRA_NETWORK_ATTRS

    d = load_descriptors()
    assert _EXTRA_NETWORK_ATTRS["mgmt-net"]["internal"] is True
    assert d["docker-socket-proxy"].networks == ["mgmt-net"]  # NOT on default
    assert d["watchtower"].networks == ["mgmt-net"]  # raw-socket holder, isolated
    for consumer in ("traefik", "prometheus", "cadvisor", "alloy", "netdata", "dozzle"):
        nets = set(d[consumer].networks)
        assert nets == {"default", "mgmt-net"}, f"{consumer} must dual-home default+mgmt-net"
