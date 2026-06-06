"""Live-audit 2026-06-05 (MED shared-minio-root-cred-two-trust-domains): milvus-minio (milvus
backend) must NOT share the app minio's root credential. Its password is a SEPARATE secret so a
leak of one MinIO root does not grant the other."""

from __future__ import annotations

import pytest

from agmind.services.renderer import load_descriptors

pytestmark = pytest.mark.backend_any


def test_milvus_minio_has_separate_root_password() -> None:
    d = load_descriptors()
    mm = d["milvus-minio"].env["MINIO_ROOT_PASSWORD"]
    assert "MILVUS_MINIO_ROOT_PASSWORD" in mm
    assert "${MINIO_ROOT_PASSWORD" not in mm  # NOT the shared app-minio secret
    # milvus client points at the same separate credential
    assert "MILVUS_MINIO_ROOT_PASSWORD" in d["milvus"].env["MINIO_SECRET_ACCESS_KEY"]


def test_app_minio_keeps_its_own_root_password() -> None:
    d = load_descriptors()
    assert "MINIO_ROOT_PASSWORD" in d["minio"].env["MINIO_ROOT_PASSWORD"]
    assert "MILVUS_MINIO" not in d["minio"].env["MINIO_ROOT_PASSWORD"]


def test_milvus_minio_password_is_generated_init_only() -> None:
    from agmind.install.secret_keys import INIT_ONLY, RUNTIME_SECRET_KEYS

    assert "MILVUS_MINIO_ROOT_PASSWORD" in RUNTIME_SECRET_KEYS
    assert "MILVUS_MINIO_ROOT_PASSWORD" in INIT_ONLY
