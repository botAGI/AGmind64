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


def test_rag_milvus_renders_internal_data_net() -> None:
    d = load_descriptors()
    sel = [d[n] for n in ("milvus", "etcd", "milvus-minio")]
    compose = render_compose(sel, traefik_enabled=False)
    nets = compose["networks"]
    assert nets["data-net"]["internal"] is True
    # etcd/milvus-minio joined only data-net; milvus on both
    assert list(compose["services"]["etcd"]["networks"]) == ["data-net"]
    assert set(compose["services"]["milvus"]["networks"]) == {"default", "data-net"}
