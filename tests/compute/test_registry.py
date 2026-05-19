"""Tests для agmind.compute._registry — auto-select per §1.2.6.

Selection rules:
    profile=tg / mixed / embed_single → vulkan → rocm → cpu
    profile=pp / embed_batch          → rocm → vulkan → cpu
"""

from __future__ import annotations

import pytest

from agmind.compute._registry import _select_auto, list_available_backends
from agmind.compute.config import ComputeConfig, Profile

pytestmark = pytest.mark.backend_any


def _cfg(profile: Profile) -> ComputeConfig:
    return ComputeConfig(backend="auto", engine="auto", device_id=0, profile=profile)


def test_select_auto_no_backends_raises() -> None:
    with pytest.raises(RuntimeError, match="No compute backends"):
        _select_auto(_cfg(Profile.MIXED), [])


def test_select_auto_cpu_only() -> None:
    assert _select_auto(_cfg(Profile.MIXED), ["cpu"]) == "cpu"
    assert _select_auto(_cfg(Profile.TG), ["cpu"]) == "cpu"
    assert _select_auto(_cfg(Profile.PP), ["cpu"]) == "cpu"


def test_select_auto_vulkan_only() -> None:
    assert _select_auto(_cfg(Profile.MIXED), ["vulkan", "cpu"]) == "vulkan"
    assert _select_auto(_cfg(Profile.TG), ["vulkan", "cpu"]) == "vulkan"


def test_select_auto_rocm_preferred_for_pp() -> None:
    assert _select_auto(_cfg(Profile.PP), ["vulkan", "rocm", "cpu"]) == "rocm"


def test_select_auto_rocm_preferred_for_embed_batch() -> None:
    assert _select_auto(
        _cfg(Profile.EMBED_BATCH), ["vulkan", "rocm", "cpu"]
    ) == "rocm"


def test_select_auto_vulkan_preferred_for_tg() -> None:
    assert _select_auto(_cfg(Profile.TG), ["vulkan", "rocm", "cpu"]) == "vulkan"


def test_select_auto_vulkan_preferred_for_mixed() -> None:
    assert _select_auto(
        _cfg(Profile.MIXED), ["vulkan", "rocm", "cpu"]
    ) == "vulkan"


def test_select_auto_vulkan_preferred_for_embed_single() -> None:
    assert _select_auto(
        _cfg(Profile.EMBED_SINGLE), ["vulkan", "rocm", "cpu"]
    ) == "vulkan"


def test_select_auto_pp_fallback_to_vulkan_when_no_rocm() -> None:
    assert _select_auto(_cfg(Profile.PP), ["vulkan", "cpu"]) == "vulkan"


def test_select_auto_embed_batch_fallback_to_vulkan() -> None:
    assert _select_auto(
        _cfg(Profile.EMBED_BATCH), ["vulkan", "cpu"]
    ) == "vulkan"


def test_select_auto_rocm_only_for_tg() -> None:
    assert _select_auto(_cfg(Profile.TG), ["rocm", "cpu"]) == "rocm"


def test_list_available_backends_always_contains_cpu() -> None:
    """CPU — гарантированный fallback на любой машине."""
    assert "cpu" in list_available_backends()


def test_list_available_backends_is_subset_of_priority() -> None:
    avail = set(list_available_backends())
    assert avail.issubset({"vulkan", "rocm", "cpu", "npu"})
