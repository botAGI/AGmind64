"""Live-audit 2026-06-05 (HIGH gpu-disabled-vulkan-icd-path): the llama descriptors
pinned VK_DRIVER_FILES to radeon_icd.x86_64.json, which does NOT exist in the
ghcr.io/ggml-org/llama.cpp server-vulkan image (it ships radeon_icd.json). The
Vulkan loader then uses ONLY that missing path, RADV never loads, and embed/rerank
run on CPU. Guard the verified-present ICD path on every GPU llama descriptor."""

from __future__ import annotations

import pytest

from agmind.services.renderer import load_descriptors

pytestmark = pytest.mark.backend_any

_GPU_LLAMA = ("llama-embed", "llama-rerank", "llama-llm")
# The file the server-vulkan image actually ships (verified live 2026-06-05):
_PRESENT_ICD = "/usr/share/vulkan/icd.d/radeon_icd.json"
_ABSENT_ICD = "/usr/share/vulkan/icd.d/radeon_icd.x86_64.json"


def test_llama_descriptors_pin_present_vulkan_icd() -> None:
    descriptors = load_descriptors()
    for name in _GPU_LLAMA:
        d = descriptors[name]
        assert d.env.get("AMD_VULKAN_ICD") == "RADV", f"{name}: must select RADV"
        icd = d.env.get("VK_DRIVER_FILES")
        assert icd != _ABSENT_ICD, (
            f"{name}: VK_DRIVER_FILES pins the ABSENT {_ABSENT_ICD} -> GPU disabled (CPU fallback)"
        )
        # If pinned at all, it must be the file the image actually ships.
        if icd is not None:
            assert icd == _PRESENT_ICD, f"{name}: VK_DRIVER_FILES={icd!r} is not the present ICD"
