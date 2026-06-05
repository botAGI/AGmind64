"""Vulkan compute backend для AMD Strix Halo (gfx1151).

Primary backend на gfx1151. Engine — llama_cpp с GGML_VULKAN=ON.

См. R2-vulkan-radv-vs-amdvlk.md (RADV mandatory, AMDVLK forbidden) и
R3-llama-cpp-vulkan-hip.md (build flags, runtime envs).

Hard requirements (assert at startup):
- vulkaninfo доступен в PATH
- RADV driver (не AMDVLK)
- Mesa ≥ 25.2.8
- Vulkan extensions: VK_KHR_cooperative_matrix, shader_float16_int8,
  integer_dot_product, buffer_device_address, external_memory_host

Mandatory env (set by Backend.make()):
- AMD_VULKAN_ICD=RADV
- VK_DRIVER_FILES=<first present RADV ICD manifest> — the filename differs by distro
  (radeon_icd.json in the server-vulkan container, radeon_icd.x86_64.json on Debian
  multiarch). We pin only a manifest that EXISTS; pinning a missing path overrides the
  loader's default discovery and silently disables the GPU (live-audit 2026-06-05).
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from agmind.compute.base import Backend, DeviceInfo, LLMHandle
from agmind.compute.detect import (
    AMDVLK_ICD_FILES,
    detect_host,
)
from agmind.core.logging import logger

log = logger(__name__)


_SUPPORTED_ENGINES = ("llama_cpp",)

_RADV_ICD_CANDIDATES = (
    "/usr/share/vulkan/icd.d/radeon_icd.x86_64.json",
    "/usr/share/vulkan/icd.d/radeon_icd.json",  # plain Mesa name (server-vulkan container)
    "/usr/share/vulkan/icd.d/radeon_icd.aarch64.json",  # audit: allow legacy ISA path constant
)


class VulkanBackend(Backend):
    """Vulkan RADV backend на gfx1151."""

    name = "vulkan"

    def __init__(self, engine: str) -> None:
        if engine not in _SUPPORTED_ENGINES:
            raise ValueError(
                f"Vulkan backend engine={engine!r} not supported. Allowed: {_SUPPORTED_ENGINES}"
            )
        self._engine = engine
        _apply_radv_env()

    @classmethod
    def available(cls) -> bool:
        """Lightweight check: есть vulkaninfo + AMD GPU + нет AMDVLK leak."""
        if not shutil.which("vulkaninfo"):
            return False
        host = detect_host()
        if host.gpu is None or host.gpu.vendor != "amd":
            return False
        # Hard fail on AMDVLK — он silently перехватывает loader
        if host.vulkan.amdvlk_files_present:
            log.warning(
                "AMDVLK detected on system: %s. Vulkan backend disabled. "
                "Remove AMDVLK files (см. docs/HARDWARE.md).",
                host.vulkan.amdvlk_files_present,
            )
            return False
        return host.vulkan.available

    @classmethod
    def make(cls, engine: str = "auto") -> VulkanBackend:
        if engine == "auto":
            engine = "llama_cpp"
        backend = cls(engine=engine)
        backend._assert_no_amdvlk()
        return backend

    def device_info(self) -> DeviceInfo:
        host = detect_host()
        vk = host.vulkan
        gpu = host.gpu

        caps: dict[str, Any] = {
            "vulkan_api_version": (
                ".".join(map(str, vk.api_version)) if vk.api_version else "unknown"
            ),
            "driver_name": vk.driver_name,
            "driver_id": vk.driver_id,
            "mesa_version": (".".join(map(str, vk.mesa_version)) if vk.mesa_version else "unknown"),
            "cooperative_matrix": vk.has_cooperative_matrix,
            "external_memory_host": vk.has_external_memory_host,
            "is_strix_halo": gpu.is_strix_halo if gpu else False,
            "bios_uma_gib": (gpu.bios_uma_bytes / 1024**3) if gpu else 0.0,
            "engine_implementation": "llama-cpp-python (GGML_VULKAN)",
        }

        total_mem = gpu.gtt_total_bytes if gpu else 0
        return DeviceInfo(
            backend=self.name,
            engine=self._engine,
            device_id=0,
            name=gpu.name if gpu else "Vulkan device",
            total_memory_bytes=total_mem,
            capabilities=caps,
        )

    def load_llm(self, model_path: str, **kwargs: Any) -> LLMHandle:
        from agmind.compute.backends._engines.http_helper import try_http_handle

        http_handle = try_http_handle(model_path)
        if http_handle is not None:
            log.info("Vulkan backend: using HTTP llama-server")
            return http_handle

        if not _llama_cpp_installed_with_vulkan():
            raise RuntimeError(
                "llama-cpp-python is not installed with Vulkan support. "
                "Either set AGMIND_LLAMA_SERVER_URL or rebuild: "
                "CMAKE_ARGS='-DGGML_VULKAN=ON -DGGML_NATIVE=OFF' "
                "pip install --force-reinstall --no-binary llama-cpp-python "
                "'llama-cpp-python>=0.3.23'"
            )
        from agmind.compute.backends._engines.llama_cpp_vulkan import (
            LlamaCppVulkanEngine,
        )

        return LlamaCppVulkanEngine().load(model_path, **kwargs)

    def embed(self, texts: Sequence[str], model: str) -> list[list[float]]:
        from agmind.compute.backends._engines.http_helper import try_http_embed

        result = try_http_embed(texts, model)
        if result is not None:
            return result
        if not _llama_cpp_installed_with_vulkan():
            raise RuntimeError("llama-cpp-python (Vulkan) is not installed")
        from agmind.compute.backends._engines.llama_cpp_vulkan import (
            LlamaCppVulkanEngine,
        )

        return LlamaCppVulkanEngine().embed(texts, model)

    def rerank(self, query: str, documents: Sequence[str]) -> list[float]:
        from agmind.compute.backends._engines.http_helper import try_http_rerank

        result = try_http_rerank(query, documents)
        if result is not None:
            return result
        if not _llama_cpp_installed_with_vulkan():
            raise RuntimeError("llama-cpp-python (Vulkan) is not installed")
        from agmind.compute.backends._engines.llama_cpp_vulkan import (
            LlamaCppVulkanEngine,
        )

        return LlamaCppVulkanEngine().rerank(query, documents)

    # ---- private helpers ----

    def _assert_no_amdvlk(self) -> None:
        leaked = tuple(f for f in AMDVLK_ICD_FILES if Path(f).exists())
        if leaked:
            raise RuntimeError(
                f"AMDVLK ICD files detected: {leaked}. "
                "AMDVLK is officially discontinued (Sep 2025) and has a 2 GiB "
                "VkDeviceMemory cap that breaks LLM ≥30B dense. "
                "Run: sudo rm -f " + " ".join(leaked)
            )


def _apply_radv_env() -> None:
    """Idempotent: install RADV env vars at process level."""
    os.environ.setdefault("AMD_VULKAN_ICD", "RADV")
    # Pin a manifest ONLY if it exists on disk. Forcing VK_DRIVER_FILES to a missing path
    # overrides the loader's default ICD discovery → RADV never loads → GPU silently disabled
    # (live-audit 2026-06-05). If none of the candidates are present, leave discovery alone.
    radv = next((p for p in _RADV_ICD_CANDIDATES if Path(p).exists()), None)
    if radv is not None:
        os.environ.setdefault("VK_DRIVER_FILES", radv)
        os.environ.setdefault("VK_ICD_FILENAMES", radv)
    os.environ.setdefault("GGML_VK_VISIBLE_DEVICES", "0")


def _llama_cpp_installed_with_vulkan() -> bool:
    """Best-effort check that llama-cpp-python is present AND built with a GPU backend.

    We verify the BUILD, not just the import: ``llama_supports_gpu_offload()`` returns True
    only when llama.cpp was compiled with a GPU backend (here ``GGML_VULKAN=ON``). On AMD
    Strix Halo gfx1151 the only GPU backend is Vulkan/RADV, so GPU-offload capability is the
    Vulkan build. A CPU-only wheel therefore returns False — the previous check returned True
    on mere import, so the "not installed with Vulkan support" error could fire falsely or be
    suppressed. If the symbol is absent (older wheel) we fall back to import presence and let
    the caller's smoke test on a real model be the final arbiter.
    """
    import importlib.util

    if importlib.util.find_spec("llama_cpp") is None:
        return False
    try:
        import llama_cpp
    except Exception:  # noqa: BLE001 — a broken/partial install is "not usable"
        return False
    supports_gpu = getattr(llama_cpp, "llama_supports_gpu_offload", None)
    if callable(supports_gpu):
        try:
            return bool(supports_gpu())
        except Exception:  # noqa: BLE001 — treat a probe error as "not usable"
            return False
    # Symbol unavailable on this wheel — fall back to import presence; smoke test decides.
    return True
