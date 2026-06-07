"""AGmind cluster — multi-node coordination layer.

Currently SHIPS: mDNS peer discovery (``detect``) + a local cluster/runtime inspector
(``inspect``) + the ``Peer`` data type. The inference request-router / health-probe / static
cluster-config layer was aspirational with no live caller and was removed as dead vaporware
(de-slop 2026-06-07 SLOP-H1). See ansible/inventory/cluster.yml and docs/CLUSTER.md.
"""

from __future__ import annotations

from agmind.cluster.inspect import ClusterInspectReport, inspect_cluster
from agmind.cluster.peer import Peer

__all__ = [
    "ClusterInspectReport",
    "Peer",
    "inspect_cluster",
]
