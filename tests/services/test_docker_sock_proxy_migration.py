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
