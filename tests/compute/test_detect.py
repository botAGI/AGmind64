"""Tests для agmind.compute.detect — hardware detection."""

from __future__ import annotations

import pytest

from agmind.compute.detect import (
    AMDVLK_ICD_FILES,
    GIB,
    GPUInfo,
    HostInfo,
    ROCmInfo,
    VulkanInfo,
    detect_host,
    detect_vulkan,
    detect_rocm,
    detect_gpu,
)


pytestmark = pytest.mark.backend_any


def test_gib_constant() -> None:
    assert GIB == 1024**3


def test_detect_host_returns_hostinfo() -> None:
    info = detect_host()
    assert isinstance(info, HostInfo)
    assert info.cpu_cores >= 1
    assert info.kernel_version
    assert info.system_ram_bytes > 0


def test_detect_host_cpu_model_or_unknown() -> None:
    """CPU model is non-empty on Linux (или unknown в exotic env)."""
    info = detect_host()
    assert isinstance(info.cpu_model, str)


def test_detect_vulkan_returns_vulkaninfo() -> None:
    vk = detect_vulkan()
    assert isinstance(vk, VulkanInfo)
    # Available может быть False (no vulkaninfo). Это ОК.
    if vk.available:
        # Если available — driver_name должен быть осмыслен
        assert vk.driver_name in {"radv", "amdvlk", "anv", "nvidia", "llvmpipe", ""}


def test_detect_rocm_returns_rocminfo() -> None:
    rocm = detect_rocm()
    assert isinstance(rocm, ROCmInfo)


def test_amdvlk_files_constant_is_tuple() -> None:
    assert isinstance(AMDVLK_ICD_FILES, tuple)
    assert len(AMDVLK_ICD_FILES) == 4
    for f in AMDVLK_ICD_FILES:
        assert "amd_icd" in f or "amd_icd32" in f


@pytest.mark.skipif(
    not __import__("pathlib").Path("/sys/class/drm").exists(),
    reason="Not on Linux or no DRM subsystem",
)
def test_detect_gpu_on_strix_halo(has_strix_halo: bool) -> None:
    """На реальной gfx1151 — GPU должен детектиться корректно."""
    gpu = detect_gpu()
    if not has_strix_halo:
        pytest.skip("Not on Strix Halo — gpu detection is not testable")
    assert gpu is not None
    assert gpu.is_strix_halo is True
    assert gpu.pci_id in (0x1586, 0x150E)
    assert gpu.vendor == "amd"
    # На правильно настроенной Linux машине BIOS UMA = 0.5 GiB (минимум)
    # либо ≤ 2 GiB. >2 GiB — warning, но не fail.
    assert gpu.bios_uma_bytes >= 0
    # GTT должен быть ≥ BIOS UMA (GTT включает UMA)
    assert gpu.gtt_total_bytes >= gpu.bios_uma_bytes


def test_detect_host_warnings_when_amdvlk_present() -> None:
    """Если AMDVLK ICD файлы существуют — warnings должны их упомянуть."""
    info = detect_host()
    if info.vulkan.amdvlk_files_present:
        assert any(
            "AMDVLK" in w or "amdvlk" in w
            for w in info.warnings
        )
