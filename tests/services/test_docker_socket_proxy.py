"""Live-audit 2026-06-05 (HIGH prometheus-no-docker-socket + docker-sock-mounted-by-5-root-
containers): prometheus docker_sd must discover targets via a READ-ONLY docker-socket-proxy,
never the raw root socket (which is root-equivalent host control). Fixes the empty-Grafana
symptom without expanding the raw-socket blast radius."""

from __future__ import annotations

from pathlib import Path

import pytest

from agmind.services.renderer import load_descriptors

pytestmark = pytest.mark.backend_any

_PROM_CONF = Path(__file__).resolve().parents[2] / "templates" / "observability" / "prometheus.yml"


def test_docker_socket_proxy_is_read_only() -> None:
    env = load_descriptors()["docker-socket-proxy"].env
    assert env["CONTAINERS"] == "1"
    assert env["NETWORKS"] == "1"
    for write_key in (
        "POST",
        "EXEC",
        "CONTAINERS_CREATE",
        "VOLUMES",
        "SECRETS",
        "SERVICES",
        "IMAGES",
    ):
        assert env[write_key] == "0", f"{write_key} must be denied on the socket proxy"


def test_docker_socket_proxy_mounts_socket_read_only() -> None:
    vols = load_descriptors()["docker-socket-proxy"].volumes
    assert "/var/run/docker.sock:/var/run/docker.sock:ro" in vols


def test_prometheus_scrapes_via_socket_proxy_not_raw_socket() -> None:
    conf = _PROM_CONF.read_text(encoding="utf-8")
    assert "tcp://docker-socket-proxy:2375" in conf
    assert "unix:///var/run/docker.sock" not in conf


def test_docker_socket_proxy_in_observability_profile() -> None:
    """Co-selected with prometheus via the observability profile (no hard depends_on, which
    would break isolated prometheus renders; docker_sd reconnects once the proxy is up)."""
    d = load_descriptors()["docker-socket-proxy"]
    assert "observability" in d.profiles
