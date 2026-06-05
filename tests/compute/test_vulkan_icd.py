"""Live-audit 2026-06-05 (HIGH gpu-disabled-vulkan-icd-path): `_apply_radv_env`
must never PIN VK_DRIVER_FILES to a path that does not exist — doing so overrides
the Vulkan loader's default ICD discovery and silently disables the GPU (RADV
never loads, llama embed/rerank fall back to CPU)."""

from __future__ import annotations

import os

import pytest

from agmind.compute.backends import vulkan

pytestmark = pytest.mark.backend_any

_ENV_KEYS = ("AMD_VULKAN_ICD", "VK_DRIVER_FILES", "VK_ICD_FILENAMES")


def _clear(monkeypatch: pytest.MonkeyPatch) -> None:
    for k in _ENV_KEYS:
        monkeypatch.delenv(k, raising=False)


def test_apply_radv_env_does_not_force_nonexistent_icd(monkeypatch: pytest.MonkeyPatch) -> None:
    """No candidate ICD on disk -> set AMD_VULKAN_ICD=RADV but DO NOT pin a missing
    VK_DRIVER_FILES (default discovery must remain in effect)."""
    monkeypatch.setattr(
        vulkan,
        "_RADV_ICD_CANDIDATES",
        ("/nonexistent/radeon_a.json", "/nonexistent/radeon_b.json"),
    )
    _clear(monkeypatch)
    vulkan._apply_radv_env()
    assert os.environ.get("AMD_VULKAN_ICD") == "RADV"
    assert "VK_DRIVER_FILES" not in os.environ
    assert "VK_ICD_FILENAMES" not in os.environ


def test_apply_radv_env_prefers_existing_icd(
    monkeypatch: pytest.MonkeyPatch, tmp_path: object
) -> None:
    """When a candidate ICD exists, pin it (the container ships radeon_icd.json)."""
    present = os.path.join(str(tmp_path), "radeon_icd.json")
    with open(present, "w", encoding="utf-8") as fh:
        fh.write("{}")
    monkeypatch.setattr(
        vulkan,
        "_RADV_ICD_CANDIDATES",
        (os.path.join(str(tmp_path), "missing.x86_64.json"), present),
    )
    _clear(monkeypatch)
    vulkan._apply_radv_env()
    assert os.environ.get("VK_DRIVER_FILES") == present
    assert os.environ.get("VK_ICD_FILENAMES") == present


def test_radv_candidates_include_plain_radeon_icd() -> None:
    """The plain Mesa filename (radeon_icd.json — what the server-vulkan image ships)
    must be a candidate, not only the Debian-multiarch radeon_icd.x86_64.json."""
    assert "/usr/share/vulkan/icd.d/radeon_icd.json" in vulkan._RADV_ICD_CANDIDATES
