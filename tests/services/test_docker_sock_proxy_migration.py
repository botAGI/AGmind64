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
    # need WRITE (pull/recreate/manage) — the read-only proxy can't serve them; or ARE the proxy.
    write_holders = {"portainer", "komodo-periphery", "watchtower", "docker-socket-proxy"}
    # read-only consumers not yet migrated (tech-debt — none are in the default selection).
    pending_migration = {"netdata", "homarr", "dozzle"}
    allowed = write_holders | pending_migration
    for name, d in descriptors.items():
        if any("/var/run/docker.sock" in v for v in d.volumes):
            assert name in allowed, (
                f"{name} binds the raw docker socket but is not an allowed holder"
            )
    # the migrated edge/observability consumers must NOT bind it anymore
    for migrated in ("traefik", "cadvisor", "alloy"):
        assert not any("/var/run/docker.sock" in v for v in descriptors[migrated].volumes), migrated
