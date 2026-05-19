"""Tests для agmind.cluster.router — routing strategies."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from agmind.cluster import (
    ClusterConfig,
    Peer,
    PeerHealth,
    RoutingStrategy,
    choose_peer,
    load_cluster_config,
)

pytestmark = pytest.mark.backend_any


def _make_peers(n: int = 3) -> list[Peer]:
    return [Peer(url=f"http://w{i:02d}:8080", name=f"w{i:02d}") for i in range(n)]


def _alive(peers: list[Peer], alive_mask: list[bool] | None = None) -> list[PeerHealth]:
    if alive_mask is None:
        alive_mask = [True] * len(peers)
    return [
        PeerHealth(peer=p, is_alive=alive_mask[i])
        for i, p in enumerate(peers)
    ]


# ---- choose_peer basic ----


def test_choose_peer_empty_returns_none() -> None:
    assert choose_peer([]) is None


def test_choose_peer_all_dead_returns_none() -> None:
    peers = _make_peers(3)
    healths = _alive(peers, [False, False, False])
    assert choose_peer(healths) is None


def test_choose_peer_returns_peer_instance() -> None:
    peers = _make_peers(2)
    healths = _alive(peers)
    result = choose_peer(healths)
    assert isinstance(result, Peer)
    assert result in peers


# ---- round-robin ----


def test_round_robin_alternates() -> None:
    """Round-robin должен циклически выбирать alive peers."""
    peers = _make_peers(3)
    healths = _alive(peers)
    selections = [choose_peer(healths, strategy="round-robin") for _ in range(6)]
    # Must include all 3 over 6 calls
    unique = {p.name for p in selections}
    assert len(unique) == 3


def test_round_robin_skips_dead() -> None:
    peers = _make_peers(3)
    healths = _alive(peers, [True, False, True])
    selections = [choose_peer(healths, strategy="round-robin") for _ in range(6)]
    # Только w00 + w02 (w01 dead) — w01 не должен быть в selections
    names = {p.name for p in selections}
    assert "w01" not in names


# ---- least-loaded ----


def test_least_loaded_picks_min_inflight() -> None:
    peers = _make_peers(3)
    healths = _alive(peers)
    healths[0].inflight = 5
    healths[1].inflight = 1
    healths[2].inflight = 3
    result = choose_peer(healths, strategy=RoutingStrategy.LEAST_LOADED)
    assert result is not None
    assert result.name == "w01"  # inflight=1, минимум


def test_least_loaded_zero_inflight() -> None:
    peers = _make_peers(2)
    healths = _alive(peers)
    healths[0].inflight = 0
    healths[1].inflight = 0
    result = choose_peer(healths, strategy="least-loaded")
    # Tie-break: stable (Python's min returns first)
    assert result is not None
    assert result.name in ("w00", "w01")


# ---- sticky session ----


def test_sticky_session_deterministic() -> None:
    peers = _make_peers(3)
    healths = _alive(peers)
    p1 = choose_peer(healths, strategy="sticky-session", session_id="user-abc")
    p2 = choose_peer(healths, strategy="sticky-session", session_id="user-abc")
    p3 = choose_peer(healths, strategy="sticky-session", session_id="user-abc")
    assert p1 == p2 == p3, "Sticky session must return same peer for same id"


def test_sticky_session_different_ids_can_differ() -> None:
    """Different session ids → possibly different peers (statistical)."""
    peers = _make_peers(5)
    healths = _alive(peers)
    results = set()
    for i in range(20):
        p = choose_peer(healths, strategy="sticky-session", session_id=f"user-{i}")
        results.add(p.name)
    # 20 sessions × 5 peers → should hit >=3 peers (sha256 uniform)
    assert len(results) >= 3


def test_sticky_session_no_id_falls_back() -> None:
    peers = _make_peers(3)
    healths = _alive(peers)
    # Empty session_id → fallback to round-robin (just check it doesn't crash)
    p = choose_peer(healths, strategy="sticky-session", session_id="")
    assert p is not None


# ---- random ----


def test_random_strategy_returns_alive() -> None:
    peers = _make_peers(3)
    healths = _alive(peers, [True, False, True])
    for _ in range(20):
        p = choose_peer(healths, strategy=RoutingStrategy.RANDOM)
        assert p is not None
        assert p.name in ("w00", "w02")  # only alive ones


# ---- unknown strategy fallback ----


def test_unknown_strategy_fallback_to_round_robin() -> None:
    """Unknown string strategy → log warning, use round-robin."""
    peers = _make_peers(2)
    healths = _alive(peers)
    p = choose_peer(healths, strategy="weirdunknownstrategy")
    assert p is not None
    assert p in peers


# ---- load_cluster_config ----


def test_load_cluster_config_missing_returns_single_node(tmp_path: Path) -> None:
    cfg = load_cluster_config(tmp_path / "missing.yaml")
    assert isinstance(cfg, ClusterConfig)
    assert cfg.role == "single-node"
    assert cfg.peers == []


def test_load_cluster_config_parses_peers(tmp_path: Path) -> None:
    yaml_content = dedent("""
        cluster:
          role: master
          master_endpoint: "http://master:8080"
          workers:
            - url: "http://w01:8080"
              name: "worker-01"
              weight: 2
            - url: "http://w02:8080"
              name: "worker-02"
          routing:
            strategy: least-loaded
            health_check_interval_s: 60
    """).strip()
    p = tmp_path / "cluster.yaml"
    p.write_text(yaml_content)
    cfg = load_cluster_config(p)
    assert cfg.role == "master"
    assert cfg.master_endpoint == "http://master:8080"
    assert len(cfg.peers) == 2
    assert cfg.peers[0].url == "http://w01:8080"
    assert cfg.peers[0].weight == 2
    assert cfg.routing_strategy == "least-loaded"
    assert cfg.health_check_interval_s == 60


# ---- Peer + PeerHealth dataclasses ----


def test_peer_frozen() -> None:
    p = Peer(url="http://x:8080")
    with pytest.raises((AttributeError, Exception)):
        p.url = "http://y:8080"  # type: ignore[misc]


def test_peer_defaults() -> None:
    p = Peer(url="http://x:8080")
    assert p.name == ""
    assert p.weight == 1
    assert p.tags == ()


def test_peer_health_mutable() -> None:
    """PeerHealth — mutable (inflight counter обновляется runtime)."""
    p = Peer(url="http://x:8080")
    h = PeerHealth(peer=p)
    assert h.inflight == 0
    h.inflight += 1
    assert h.inflight == 1


def test_routing_strategy_enum_values() -> None:
    assert RoutingStrategy.ROUND_ROBIN.value == "round-robin"
    assert RoutingStrategy.LEAST_LOADED.value == "least-loaded"
    assert RoutingStrategy.STICKY_SESSION.value == "sticky-session"
    assert RoutingStrategy.RANDOM.value == "random"
