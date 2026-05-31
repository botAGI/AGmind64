"""Catalog-sweep fixes (2026-05-30 read-only audit follow-ups).

- docling: mount target must match where the image actually writes its cache
  (DOCLING_SERVE_ARTIFACTS_PATH=/opt/app-root/src/.cache/...), not /root/.cache —
  otherwise the bind persists nothing even after the perms fix.
- milvus: upstream standalone compose ships `security_opt: [seccomp:unconfined]`;
  jemalloc/Knowhere issue syscalls Docker's default seccomp can restrict.
"""

from __future__ import annotations

import pytest

from agmind.services.renderer import load_descriptors

pytestmark = pytest.mark.backend_any


def test_docling_cache_mount_targets_image_cache_path() -> None:
    d = load_descriptors()["docling"]
    assert "/var/lib/agmind/docling-cache:/opt/app-root/src/.cache" in d.volumes
    assert not any(v.endswith(":/root/.cache") for v in d.volumes), (
        "docling image HOME is /opt/app-root/src, not /root — /root/.cache persists nothing"
    )


def test_milvus_has_seccomp_unconfined() -> None:
    d = load_descriptors()["milvus"]
    assert "seccomp:unconfined" in d.security_opt
