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
