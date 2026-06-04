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


def test_bundle_helper_probes_both_module_paths() -> None:
    """Review MEDIUM bundle-manifest-wheel-module-path: the helper must work in BOTH install
    modes — a wheel/pip install exposes the module as `agmind.scripts.bundle_manifest`, a
    source checkout as `scripts.bundle_manifest`. The helper probes importability (no blind
    `||` that would mask a real runtime error) and runs whichever resolves."""
    from pathlib import Path

    text = Path("scripts/bundle-images.sh").read_text(encoding="utf-8")
    assert "agmind.scripts.bundle_manifest" in text
    assert "scripts.bundle_manifest" in text
    # Probe must use a side-effect-free import check, not run-and-fallback.
    assert "import agmind.scripts.bundle_manifest" in text


def test_bundle_manifest_runs_as_a_module_subprocess() -> None:
    """Behavioral: the manifest actually runs as `python -m scripts.bundle_manifest` (source
    checkout form) and emits digest-pinned refs — not just importable."""
    import subprocess
    import sys

    out = subprocess.run(
        [sys.executable, "-m", "scripts.bundle_manifest", "--profile", "core"],
        capture_output=True,
        text=True,
        check=True,
    )
    lines = [line for line in out.stdout.splitlines() if line.strip()]
    assert lines, "module run must emit at least one image"
    assert all("@sha256:" in line for line in lines), lines
