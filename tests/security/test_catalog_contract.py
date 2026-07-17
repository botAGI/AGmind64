"""Fail-closed docker.sock + /var/run:rw allowlist contract tests.

Lifted verbatim from RESEARCH-ADVANCED-2026-05-31.md §2 C3 + C3b sketches,
since made bidirectional: SOCK_ALLOW must equal the actual set of raw-socket
mounters, not just bound it from above. Only 3 services mount the raw socket
today (verified live by grep of every descriptor's `volumes:` block): portainer,
watchtower, docker-socket-proxy. The other 6 (traefik, dozzle, homarr, netdata,
cadvisor, alloy) migrated to the tcp docker-socket-proxy and carry explicit "NO
raw /var/run/docker.sock" comments in their own descriptors — corroborated
independently by tests/services/test_docker_sock_proxy_migration.py.

Mutation-verified RED (recorded in commit): adding
  /var/run/docker.sock:/var/run/docker.sock:ro
to grafana.yaml (a non-allowlisted service) made test_docker_sock_mount_allowlist
FAIL naming grafana, then the mutation was reverted clean. Separately, re-adding
"traefik" to SOCK_ALLOW (a phantom) made the same test FAIL naming traefik as a
phantom entry, then the mutation was reverted clean.

Purpose: a future PR cannot silently grant a 4th service the docker socket, nor
can the allowlist rot with a stale phantom entry, without failing CI.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from agmind.schemas import ServiceDescriptor

pytestmark = pytest.mark.backend_any

SERVICES_DIR = Path(__file__).resolve().parents[2] / "templates" / "services"

# ---- C3: docker.sock mount allowlist ----
SOCK = "/var/run/docker.sock"
# 3 real raw-socket mounters (verified this session by grep of every descriptor's
# `volumes:` block — `grep -rln '^\s*-\s*/var/run/docker\.sock' templates/services/*.yaml`
# returns exactly these 3 files): portainer.yaml, watchtower.yaml, docker-socket-proxy.yaml.
# All other services that talk to the Docker API (traefik, dozzle, homarr, netdata,
# cadvisor, alloy) reach it over tcp via the docker-socket-proxy below and carry an
# explicit "NO raw /var/run/docker.sock" comment in their own descriptor.
SOCK_ALLOW = {
    "watchtower",
    "portainer",
    # Read-only Docker-API gateway: mounts the socket :ro and re-exposes ONLY the GET endpoints
    # its consumers need (CONTAINERS/NETWORKS/INFO=1, all write/exec/create=0). Its whole
    # purpose is to keep the raw root socket off every other container (live-audit 2026-06-05).
    "docker-socket-proxy",
}


def _all_descriptors() -> dict[str, ServiceDescriptor]:
    """Load every templates/services/*.yaml via ServiceDescriptor.model_validate."""
    result: dict[str, ServiceDescriptor] = {}
    for yaml_path in sorted(SERVICES_DIR.glob("*.yaml")):
        raw = yaml.safe_load(yaml_path.read_text())
        descriptor = ServiceDescriptor.model_validate(raw)
        result[descriptor.name] = descriptor
    return result


def test_docker_sock_mount_allowlist() -> None:
    """SOCK_ALLOW must equal the actual set of raw-socket mounters, in both directions.

    C3 from RESEARCH-ADVANCED-2026-05-31.md §2, made bidirectional — a one-directional
    `mounters ⊆ SOCK_ALLOW` gate lets the allowlist rot with phantom entries (services
    that migrated off the raw socket) while still catching a new undeclared mounter.
    Both directions are asserted:
      - SOCK_ALLOW - ACTUAL_SOCK_MOUNTERS == set(): no phantom entries (a service listed
        but not actually mounting the socket).
      - ACTUAL_SOCK_MOUNTERS - SOCK_ALLOW == set(): no undeclared mounter (a service
        mounting the socket without going through allowlist review).
    """
    descriptors = _all_descriptors()
    actual_sock_mounters = {
        name for name, d in descriptors.items() if any(v.split(":")[0] == SOCK for v in d.volumes)
    }
    phantoms = SOCK_ALLOW - actual_sock_mounters
    assert not phantoms, (
        f"SOCK_ALLOW lists {sorted(phantoms)!r} but they do not mount docker.sock — "
        "remove the phantom entry (or fix the descriptor if the mount was dropped by mistake)"
    )
    undeclared = actual_sock_mounters - SOCK_ALLOW
    assert not undeclared, (
        f"{sorted(undeclared)!r} mount docker.sock but are not in SOCK_ALLOW "
        f"({sorted(SOCK_ALLOW)!r}) — add to allowlist only after security review"
    )
    for name, d in descriptors.items():
        mounts = [v for v in d.volumes if v.split(":")[0] == SOCK]
        for m in mounts:
            assert m.endswith(":ro"), (
                f"{name}: docker.sock must be mounted read-only, got {m!r} "
                "(NOTE: :ro on the socket does NOT make Docker API read-only — "
                "POST /containers/create still works)"
            )


def test_var_run_rw_mount_allowlist() -> None:
    """Rw mount of /var/run would only ever be allowed for cadvisor (historical exception).

    C3b from RESEARCH-ADVANCED-2026-05-31.md §2 — cadvisor has since been hardened off
    an rw /var/run mount onto the read-only docker-socket-proxy (tcp), so this test
    currently finds zero rw /var/run mounts in the catalog; the name == "cadvisor"
    branch is a dormant guard, not an active exception.
    """
    descriptors = _all_descriptors()
    for name, d in descriptors.items():
        for v in d.volumes:
            src, *rest = v.split(":")
            mode = rest[-1] if rest and rest[-1] in ("ro", "rw") else "rw"
            if src in ("/var/run", "/var/run/") and mode == "rw":
                assert name == "cadvisor", (
                    f"{name}: rw /var/run mount only allowed for cadvisor, got {v!r}"
                )
