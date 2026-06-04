"""Phase 10-03 (M8): the cluster docs must state that Alloy log collection is per-node.

Alloy tails the local docker.sock, so a master-only Alloy misses worker container logs. A
multi-node deploy needs an Alloy per node pushing to the central Loki — this guidance must be
documented so a cluster operator doesn't silently lose worker logs."""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.backend_any

_ROOT = Path(__file__).resolve().parents[2]


def test_cluster_md_documents_per_node_alloy() -> None:
    text = _ROOT.joinpath("docs", "CLUSTER.md").read_text(encoding="utf-8").lower()
    assert "per-node" in text
    assert "alloy" in text
    assert "worker container logs" in text


def test_alloy_config_header_notes_per_node() -> None:
    text = (
        _ROOT.joinpath("templates", "observability", "alloy", "config.alloy")
        .read_text(encoding="utf-8")
        .lower()
    )
    assert "per-node" in text
    assert "every node" in text or "each node" in text
