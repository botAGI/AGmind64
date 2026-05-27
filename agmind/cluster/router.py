"""Routing strategies для cluster — выбор peer'а из пула живых.

Strategies:
- round-robin   — циклически по индексу
- least-loaded  — peer с минимальным `inflight` counter
- sticky-session — same session_id → same peer (для KV cache reuse)
- random        — uniform random (для load tests)
"""

from __future__ import annotations

import hashlib
import itertools
import random
from enum import Enum

from agmind.cluster.peer import Peer, PeerHealth
from agmind.core.logging import logger

log = logger(__name__)


class RoutingStrategy(str, Enum):
    ROUND_ROBIN = "round-robin"
    LEAST_LOADED = "least-loaded"
    STICKY_SESSION = "sticky-session"
    RANDOM = "random"


# Mutable counter для round-robin. Module-global; safe for single-thread CLI.
# Для multi-thread server обернуть в threading.Lock или asyncio.Lock.
_rr_counter = itertools.count()


def choose_peer(
    healths: list[PeerHealth],
    *,
    strategy: RoutingStrategy | str = RoutingStrategy.ROUND_ROBIN,
    session_id: str = "",
) -> Peer | None:
    """Select peer per strategy. Returns None если нет alive peers."""
    if isinstance(strategy, str):
        try:
            strategy = RoutingStrategy(strategy)
        except ValueError:
            log.warning("unknown routing strategy %r — falling back to round-robin", strategy)
            strategy = RoutingStrategy.ROUND_ROBIN

    alive = [h for h in healths if h.is_alive]
    if not alive:
        return None

    if strategy == RoutingStrategy.ROUND_ROBIN:
        idx = next(_rr_counter) % len(alive)
        return alive[idx].peer

    if strategy == RoutingStrategy.LEAST_LOADED:
        return min(alive, key=lambda h: h.inflight).peer

    if strategy == RoutingStrategy.STICKY_SESSION:
        if not session_id:
            # Fall back to round-robin
            idx = next(_rr_counter) % len(alive)
            return alive[idx].peer
        # Hash session_id → consistent peer index
        digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
        idx = int(digest[:8], 16) % len(alive)
        return alive[idx].peer

    if strategy == RoutingStrategy.RANDOM:
        return random.choice(alive).peer

    # Unreachable
    return alive[0].peer
