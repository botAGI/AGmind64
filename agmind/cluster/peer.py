"""Peer discovery + health для AGmind cluster.

Two modes:
1. **Static config** — `/etc/agmind/cluster.yaml` (предзаписано Ansible)
2. **mDNS** — avahi `agmind-worker-*.local` (не реализовано в M1)

Каждый peer = `LlamaServerClient` к worker llama-server endpoint.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from agmind.core.logging import logger

log = logger(__name__)


@dataclass(frozen=True)
class Peer:
    """One cluster peer (worker node)."""

    url: str
    """llama-server URL, e.g. 'http://agmind-worker-01.local:8080'."""

    name: str = ""
    """Optional friendly name (from inventory)."""

    weight: int = 1
    """Relative weight для weighted round-robin."""

    tags: tuple[str, ...] = ()
    """Optional capability tags ("gpu", "high-mem", "vision")."""


@dataclass
class PeerHealth:
    """Runtime health status одного peer'а."""

    peer: Peer
    is_alive: bool = False
    last_check_at: float = 0.0
    last_error: str = ""
    consecutive_failures: int = 0
    inflight: int = 0
    """Current concurrent requests (для least-loaded routing)."""


@dataclass
class ClusterConfig:
    """Cluster configuration."""

    role: str = "single-node"
    """'single-node' | 'master' | 'worker'."""

    master_endpoint: str = ""
    peers: list[Peer] = field(default_factory=list)
    routing_strategy: str = "round-robin"
    health_check_interval_s: int = 30
    health_check_timeout_s: int = 5
    fallback_to_local: bool = True
    """Если все peers недоступны — fallback к local llama-server."""


_DEFAULT_CONFIG_PATH = "/etc/agmind/cluster.yaml"


def load_cluster_config(path: str | Path | None = None) -> ClusterConfig:
    """Load cluster.yaml. Если missing — return single-node default."""
    p = Path(path) if path else Path(os.environ.get("AGMIND_CLUSTER_CONFIG", _DEFAULT_CONFIG_PATH))
    if not p.exists():
        log.debug("cluster config not found at %s — single-node mode", p)
        return ClusterConfig(role="single-node")

    try:
        from agmind.services.registry import _parse_yaml
    except ImportError:  # pragma: no cover
        return ClusterConfig(role="single-node")

    raw = _parse_yaml(p.read_text(encoding="utf-8"))
    cluster = raw.get("cluster") or {}

    workers_raw = cluster.get("workers") or []
    peers: list[Peer] = []
    for w in workers_raw:
        if not isinstance(w, dict):
            continue
        peers.append(
            Peer(
                url=str(w.get("url", "")),
                name=str(w.get("name", "")),
                weight=int(w.get("weight") or 1),
                tags=tuple(str(t) for t in (w.get("tags") or ())),
            )
        )

    routing = cluster.get("routing") or {}
    return ClusterConfig(
        role=str(cluster.get("role", "single-node")),
        master_endpoint=str(cluster.get("master_endpoint", "")),
        peers=peers,
        routing_strategy=str(routing.get("strategy", "round-robin")),
        health_check_interval_s=int(routing.get("health_check_interval_s") or 30),
        health_check_timeout_s=int(routing.get("health_check_timeout_s") or 5),
        fallback_to_local=bool(routing.get("fallback_to_local", True)),
    )


def probe_peer(peer: Peer, timeout: float = 5.0) -> PeerHealth:
    """Active health check одного peer'а (synchronous).

    Returns PeerHealth с свежим статусом. Безопасно: never raises.
    """
    from agmind.compute.clients import LlamaServerClient, LlamaServerError

    health = PeerHealth(peer=peer)
    client = LlamaServerClient(peer.url, timeout=timeout)
    try:
        if client.is_alive():
            health.is_alive = True
            health.consecutive_failures = 0
        else:
            health.consecutive_failures = 1
            health.last_error = "is_alive() returned False"
    except LlamaServerError as exc:
        health.is_alive = False
        health.consecutive_failures = 1
        health.last_error = str(exc)
    except Exception as exc:  # noqa: BLE001
        health.is_alive = False
        health.consecutive_failures = 1
        health.last_error = f"{type(exc).__name__}: {exc}"

    health.last_check_at = time.time()
    return health


def probe_all(
    peers: list[Peer],
    timeout: float = 5.0,
) -> list[PeerHealth]:
    """Probe all peers sequentially. Returns list в том же порядке."""
    return [probe_peer(p, timeout=timeout) for p in peers]


def alive_peers(healths: list[PeerHealth]) -> list[Peer]:
    """Filter to only alive peers."""
    return [h.peer for h in healths if h.is_alive]
