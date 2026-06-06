"""Live-audit 2026-06-05 (HIGH etcd-no-auth exposure / flat-network): stateful backends move onto
an internal `data-net` so a compromised internet-facing app on the shared net cannot reach them.
First increment: etcd + milvus-minio caged on data-net (only milvus, dual-homed, reaches them)."""

from __future__ import annotations

import pytest

from agmind.services.renderer import _EXTRA_NETWORK_ATTRS, load_descriptors, render_compose

pytestmark = pytest.mark.backend_any


def test_data_net_is_registered_internal() -> None:
    assert _EXTRA_NETWORK_ATTRS["data-net"]["internal"] is True


def test_etcd_and_milvus_minio_caged_on_data_net_only() -> None:
    d = load_descriptors()
    assert d["etcd"].networks == ["data-net"]
    assert d["milvus-minio"].networks == ["data-net"]
    # neither on the shared net -> unreachable from a compromised internet-facing app
    assert "default" not in d["etcd"].networks
    assert "default" not in d["milvus-minio"].networks


def test_milvus_dual_homed_default_and_data_net() -> None:
    d = load_descriptors()
    assert set(d["milvus"].networks) == {"default", "data-net"}


def test_mysql_and_elasticsearch_caged_ragflow_dual_homed() -> None:
    """K-2a: ragflow's backends (mysql + elasticsearch, consumed only by ragflow) are caged on
    data-net; ragflow is dual-homed so the edge still reaches it."""
    d = load_descriptors()
    assert d["mysql"].networks == ["data-net"]
    assert d["elasticsearch"].networks == ["data-net"]
    assert set(d["ragflow"].networks) == {"default", "data-net"}


def test_postgres_redis_caged_consumers_dual_homed() -> None:
    """K-2b: postgres + redis caged on data-net; every consumer dual-homed [default, data-net]."""
    d = load_descriptors()
    assert d["postgres"].networks == ["data-net"]
    assert d["redis"].networks == ["data-net"]
    for consumer in (
        "dify-api",
        "dify-worker",
        "dify-plugin-daemon",
        "postgres-exporter",
        "redis-exporter",
        "authelia",
    ):
        assert set(d[consumer].networks) == {"default", "data-net"}, consumer


def test_qdrant_caged_and_host_ports_dropped() -> None:
    """K-3a: qdrant caged on data-net (consumed by dify over the network); its 127.0.0.1 host
    ports are dropped (an internal net has no host route; consumers use the DNS name)."""
    d = load_descriptors()
    assert d["qdrant"].networks == ["data-net"]
    assert d["qdrant"].ports == []  # no host publish on an internal-only net


def test_komodo_mongo_caged_core_dual_homed() -> None:
    """K-3b: komodo-mongo caged on data-net (consumed only by komodo-core, dual-homed)."""
    d = load_descriptors()
    assert d["komodo-mongo"].networks == ["data-net"]
    assert set(d["komodo-core"].networks) == {"default", "data-net"}


def test_rag_milvus_renders_internal_data_net() -> None:
    d = load_descriptors()
    sel = [d[n] for n in ("milvus", "etcd", "milvus-minio")]
    compose = render_compose(sel, traefik_enabled=False)
    nets = compose["networks"]
    assert nets["data-net"]["internal"] is True
    # etcd/milvus-minio joined only data-net; milvus on both
    assert list(compose["services"]["etcd"]["networks"]) == ["data-net"]
    assert set(compose["services"]["milvus"]["networks"]) == {"default", "data-net"}
