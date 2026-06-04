"""Fail-closed docker.sock + /var/run:rw allowlist contract tests.

Lifted verbatim from RESEARCH-ADVANCED-2026-05-31.md §2 C3 + C3b sketches,
adjusted import path and SOCK_ALLOW constant to match the verified live
catalog (6 direct sock-mounters today: traefik, watchtower, dozzle, portainer,
homarr, netdata; cadvisor is NOT a direct socket-mounter today — it mounts
/var/run:rw — and will be added to SOCK_ALLOW in Task 2 after hardening).

Mutation-verified RED (recorded in commit): adding
  /var/run/docker.sock:/var/run/docker.sock:ro
to grafana.yaml (a non-allowlisted service) made test_docker_sock_mount_allowlist
FAIL naming grafana, then the mutation was reverted clean.

Purpose: a future PR cannot silently grant a 7th service the docker socket or
rw /var/run write access without failing CI.
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
# 8 direct sock-mounters (verified 2026-06-02):
# traefik.yaml:15, watchtower.yaml:11, dozzle.yaml:13, portainer.yaml:12,
# homarr.yaml:13, netdata.yaml:21 — all mount the socket :ro (Task 1 baseline).
# cadvisor now mounts /var/run/docker.sock:ro (hardened in Task 2) instead of /var/run:rw.
# alloy added 2026-06-02: config.alloy uses discovery.docker + loki.source.docker
# (host=unix:///var/run/docker.sock) for container log shipping — dead without the socket.
SOCK_ALLOW = {
    "traefik",
    "watchtower",
    "dozzle",
    "portainer",
    "homarr",
    "netdata",
    "cadvisor",
    "alloy",
    # Komodo periphery agent: manages docker on the node for the ops console (opt-in
    # ops profile). Mounts the socket :ro per contract; same risk class as portainer.
    "komodo-periphery",
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
    """Only SOCK_ALLOW services may mount /var/run/docker.sock, and only :ro.

    C3 from RESEARCH-ADVANCED-2026-05-31.md §2 — fail-closed: any service outside
    the allowlist that mounts the docker socket causes an immediate CI failure.
    A 7th service silently gaining the socket fails this test.
    """
    descriptors = _all_descriptors()
    for name, d in descriptors.items():
        mounts = [v for v in d.volumes if v.split(":")[0] == SOCK]
        if mounts:
            assert name in SOCK_ALLOW, (
                f"{name}: mounts docker.sock but is not in SOCK_ALLOW "
                f"({sorted(SOCK_ALLOW)!r}) — add to allowlist only after security review"
            )
            for m in mounts:
                assert m.endswith(":ro"), (
                    f"{name}: docker.sock must be mounted read-only, got {m!r} "
                    "(NOTE: :ro on the socket does NOT make Docker API read-only — "
                    "POST /containers/create still works)"
                )


def test_var_run_rw_mount_allowlist() -> None:
    """Rw mount of /var/run is allowed only for cadvisor (sole exception today).

    C3b from RESEARCH-ADVANCED-2026-05-31.md §2 — after Task 2 hardens cadvisor
    to mount the socket :ro instead of /var/run:rw, this test will find zero rw
    /var/run mounts (the assert on name == 'cadvisor' will no longer trigger).
    """
    descriptors = _all_descriptors()
    for name, d in descriptors.items():
        for v in d.volumes:
            src, *rest = v.split(":")
            mode = rest[-1] if rest and rest[-1] in ("ro", "rw") else "rw"
            if src in ("/var/run", "/var/run/") and mode == "rw":
                assert name == "cadvisor", (
                    f"{name}: rw /var/run mount only allowed for cadvisor (pre-Task-2), got {v!r}"
                )
