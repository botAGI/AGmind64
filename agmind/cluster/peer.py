"""Cluster peer data type.

A ``Peer`` describes one worker node (a llama-server endpoint). Discovery is via mDNS
(``agmind.cluster.detect``). The multi-node inference router + health-probe layer
(``router.py``, ``probe_*``, ``ClusterConfig``/``load_cluster_config``) was aspirational and had
NO live caller — removed as dead vaporware (de-slop 2026-06-07 SLOP-H1). See docs/CLUSTER.md.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Peer:
    """One cluster peer (worker node)."""

    url: str
    """llama-server URL, e.g. 'http://agmind-worker-01.local:8080'."""

    name: str = ""
    """Optional friendly name (from inventory)."""

    weight: int = 1
    """Relative weight — RESERVED for future weighted routing; no router consumes it yet
    (the multi-node inference router is not wired — see docs/CLUSTER.md)."""

    tags: tuple[str, ...] = ()
    """Optional capability tags ("gpu", "high-mem", "vision")."""
