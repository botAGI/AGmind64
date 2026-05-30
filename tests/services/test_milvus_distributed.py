"""Crash-loop blocker: milvus had no command (tini got no program) and no backing
store. Standalone Milvus v2.6 needs a metadata store (etcd) + object storage (MinIO).

Distributed mode: add a dedicated `etcd` + a scoped `milvus-minio` to the catalog
(rag-milvus profile), give milvus its `milvus run standalone` command + the env that
points it at etcd:2379 / milvus-minio:9000, and own the two new services in the
stateful-services component contract. The scoped minio reuses the existing
MINIO_ROOT_USER/PASSWORD secrets (no new secret plumbing).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from agmind.services.renderer import load_descriptors

pytestmark = pytest.mark.backend_any

_REPO_ROOT = Path(__file__).resolve().parents[2]


def test_etcd_descriptor_present_and_internal() -> None:
    d = load_descriptors()["etcd"]
    assert d.image == "quay.io/coreos/etcd:v3.5.18"
    assert d.profiles == ["rag-milvus"]
    assert d.command and d.command[0] == "etcd"
    assert any(v.endswith(":/etcd") for v in d.volumes)
    assert not d.ports, "etcd is internal-only (reached over the compose network)"


def test_milvus_minio_is_scoped_and_reuses_existing_secrets() -> None:
    d = load_descriptors()["milvus-minio"]
    assert d.profiles == ["rag-milvus"]
    assert d.command[:2] == ["server", "/data"]
    # Dedicated data volume — isolated from the ragflow minio.
    assert any("/var/lib/agmind/milvus-minio:/data" == v for v in d.volumes)
    assert not d.ports, "internal-only; milvus reaches it over the compose network"
    # Reuse the already-wired MINIO_ROOT_* secrets (no new CI/.env secret plumbing).
    assert "MINIO_ROOT_USER" in d.env["MINIO_ROOT_USER"]
    assert "MINIO_ROOT_PASSWORD" in d.env["MINIO_ROOT_PASSWORD"]


def test_milvus_runs_standalone_with_external_backends() -> None:
    d = load_descriptors()["milvus"]
    assert list(d.command) == ["milvus", "run", "standalone"]
    assert d.env.get("ETCD_ENDPOINTS") == "etcd:2379"
    assert d.env.get("MINIO_ADDRESS") == "milvus-minio:9000"
    assert set(d.depends_on) >= {"etcd", "milvus-minio"}


def test_new_services_owned_by_stateful_component() -> None:
    contract = yaml.safe_load(
        (_REPO_ROOT / "templates" / "components" / "stateful-services.yaml").read_text("utf-8")
    )
    owned = set(contract["runtime"]["service_descriptors"])
    assert {"etcd", "milvus-minio"} <= owned
