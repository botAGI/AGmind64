"""Phase 10-01 (M8): the renderer can namespace a compose project.

`project_name` / `data_root` / `config_root` let a second stack (a scenario, CI smoke) be
rendered isolated from a live `agmind` stack — distinct container names, network, and
bind-mount host roots. Defaults reproduce the historical single-stack output byte-for-byte."""

from __future__ import annotations

import pytest

from agmind.services import renderer
from agmind.services.renderer import (
    _rewrite_volume_host_root,
    load_descriptors,
    render_compose,
)

pytestmark = pytest.mark.backend_any


def _services(*names: str) -> list:
    d = load_descriptors()
    return [d[n] for n in names]


def test_default_render_is_byte_identical_namespacing() -> None:
    compose = render_compose(_services("qdrant"))
    assert "name" not in compose  # no top-level project name for the default stack
    assert compose["networks"]["default"]["name"] == "agmind"
    assert compose["services"]["qdrant"]["container_name"] == "agmind-qdrant"
    assert compose["services"]["qdrant"]["volumes"] == ["/var/lib/agmind/qdrant:/qdrant/storage"]


def test_project_name_namespaces_container_network_and_project() -> None:
    compose = render_compose(_services("qdrant"), project_name="agmind-dev")
    assert compose["name"] == "agmind-dev"
    assert compose["networks"]["default"]["name"] == "agmind-dev"
    assert compose["services"]["qdrant"]["container_name"] == "agmind-dev-qdrant"


def test_data_root_rewrites_bind_mount_host_path() -> None:
    compose = render_compose(_services("qdrant"), data_root="/srv/agmind-dev")
    assert compose["services"]["qdrant"]["volumes"] == ["/srv/agmind-dev/qdrant:/qdrant/storage"]


def test_config_root_rewrites_etc_bind_mount() -> None:
    compose = render_compose(_services("prometheus"), config_root="/srv/etc-dev")
    vols = compose["services"]["prometheus"]["volumes"]
    assert "/srv/etc-dev/prometheus:/etc/prometheus:ro" in vols
    # data_root left at default → /var/lib/agmind path untouched
    assert "/var/lib/agmind/prometheus:/prometheus" in vols


def test_full_namespacing_isolates_second_stack() -> None:
    compose = render_compose(
        _services("qdrant"),
        project_name="agmind-ci",
        data_root="/tmp/agmind-ci/data",
        config_root="/tmp/agmind-ci/etc",
    )
    qdrant = compose["services"]["qdrant"]
    assert compose["name"] == "agmind-ci"
    assert qdrant["container_name"] == "agmind-ci-qdrant"
    assert qdrant["volumes"] == ["/tmp/agmind-ci/data/qdrant:/qdrant/storage"]


@pytest.mark.parametrize(
    "volume,expected",
    [
        ("/var/lib/agmind/qdrant:/qdrant/storage", "/srv/d/qdrant:/qdrant/storage"),
        ("/etc/agmind/prometheus:/etc/prometheus:ro", "/srv/c/prometheus:/etc/prometheus:ro"),
        # boundary: a similarly-prefixed path must NOT be rewritten
        ("/var/lib/agmind-other/x:/x", "/var/lib/agmind-other/x:/x"),
        # unrelated host paths and named volumes untouched
        (
            "/var/run/docker.sock:/var/run/docker.sock:ro",
            "/var/run/docker.sock:/var/run/docker.sock:ro",
        ),
        ("named_vol:/data", "named_vol:/data"),
    ],
)
def test_rewrite_volume_host_root_boundaries(volume: str, expected: str) -> None:
    assert _rewrite_volume_host_root(volume, "/srv/d", "/srv/c") == expected


def test_default_roots_are_identity() -> None:
    v = "/var/lib/agmind/qdrant:/qdrant/storage"
    assert (
        _rewrite_volume_host_root(v, renderer.DEFAULT_DATA_ROOT, renderer.DEFAULT_CONFIG_ROOT) == v
    )
