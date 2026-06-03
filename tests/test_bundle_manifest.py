"""Tests for scripts/bundle_manifest.py — the offline image-bundle manifest.

The image list is generated from the live descriptor registry (never pasted into
the doc) so it cannot rot when a digest changes.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.backend_any


def _build(**kw):
    import scripts.bundle_manifest as bm

    return bm.build_manifest(**kw)


def test_manifest_images_are_digest_pinned_refs() -> None:
    manifest = _build()
    images = manifest["images"]
    assert images, "manifest must list images"
    for ref in images:
        assert "@sha256:" in ref, f"image not digest-pinned: {ref}"
    # deduped + sorted
    assert images == sorted(set(images))


def test_manifest_profile_scope_is_subset_of_full() -> None:
    full = set(_build()["images"])
    core = set(_build(profiles=["core"])["images"])
    assert core, "core profile must yield images"
    assert core < full, "a single profile must be a strict subset of the full catalog"


def test_manifest_unknown_profile_raises() -> None:
    with pytest.raises(ValueError):
        _build(profiles=["does-not-exist"])
