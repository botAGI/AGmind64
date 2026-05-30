"""Crash-loop blockers fixed in the service catalog (descriptor-only).

- proxmox-exporter: v3.x argparse rejects the old positional args
  ("unrecognized arguments: /etc/pve.yml 9221 0.0.0.0") — must use named flags.
- minio: no `command:` → image prints USAGE and exits — needs `server /data
  --console-address :9001`.
- watchtower: containrrr/watchtower:1.7.1's API client (1.25) is too old for
  Docker Engine 29 (min 1.44) — force DOCKER_API_VERSION=1.44.
"""

from __future__ import annotations

import pytest

from agmind.services.renderer import load_descriptors

pytestmark = pytest.mark.backend_any


def test_proxmox_exporter_uses_named_cli_flags() -> None:
    d = load_descriptors()["proxmox-exporter"]
    assert list(d.command) == [
        "--config.file=/etc/pve.yml",
        "--web.listen-address=0.0.0.0:9221",
    ]


def test_minio_has_server_subcommand() -> None:
    d = load_descriptors()["minio"]
    assert list(d.command) == ["server", "/data", "--console-address", ":9001"]


def test_watchtower_forces_compatible_docker_api_version() -> None:
    d = load_descriptors()["watchtower"]
    assert d.env.get("DOCKER_API_VERSION") == "1.44"
