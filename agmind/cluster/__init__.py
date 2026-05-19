"""AGmind cluster — multi-node coordination layer.

Master узел распределяет inference запросы между workers через
LlamaServerClient. Discovery — через mDNS (avahi) либо static YAML.

См. ansible/inventory/cluster.yml и ansible/roles/cluster/.
"""

from __future__ import annotations

from agmind.cluster.peer import (
    ClusterConfig,
    Peer,
    PeerHealth,
    load_cluster_config,
)
from agmind.cluster.router import RoutingStrategy, choose_peer

__all__ = [
    "ClusterConfig",
    "Peer",
    "PeerHealth",
    "RoutingStrategy",
    "choose_peer",
    "load_cluster_config",
]
