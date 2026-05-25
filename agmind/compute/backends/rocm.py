"""ROCm/HIP compute backend для AMD Strix Halo (gfx1151).

Secondary backend на gfx1151. Используется для long-context pp,
concurrent batch ≥16, GDN-моделей, tool-calling (M2 через vLLM).

См. R3-llama-cpp-vulkan-hip.md (HIP build flags), R1-pytorch-rocm-docker.md
(env vars), R4-vllm-rocm-engines.md (engine selection).

Hard requirements:
- rocminfo доступен (gfx1151 listed)
- ROCm ≥ 7.2 (НЕ 7.0.x — crashes ROCm/#5534)
- Kernel ≥ 6.18.4 mainline / 6.17.0-19 HWE
- /dev/kfd + /dev/dri доступны
- User в render+video группах

Mandatory env (set by Backend.make()):
- PYTORCH_ROCM_ARCH=gfx1151
- PYTORCH_ALLOC_CONF=expandable_segments:True
- TORCH_ROCM_AOTRITON_ENABLE_EXPERIMENTAL=1 (critical +19× SDPA)
- ROCBLAS_USE_HIPBLASLT=1 (+15% pp)
- HSA_ENABLE_SDMA=0

ВАЖНО: HSA_OVERRIDE_GFX_VERSION=11.5.1 НЕ ставится с AMD nightly wheels
(они уже содержат native gfx1151 kernels — override вызовет subtle bugs).
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Sequence
from typing import Any

from agmind.compute.base import Backend, DeviceInfo, LLMHandle
from agmind.compute.detect import detect_host
from agmind.log import logger

log = logger(__name__)


_SUPPORTED_ENGINES = ("llama_cpp",)  # vllm и infinity — M2 upgrade
_M2_ENGINES = frozenset({"vllm", "infinity"})


class ROCmBackend(Backend):
    """ROCm/HIP backend на gfx1151."""

    name = "rocm"

    def __init__(self, engine: str) -> None:
        if engine in _M2_ENGINES:
            raise NotImplementedError(
                f"ROCm backend engine={engine!r} is planned for M2 upgrade. "
                "See docs/adr/0002-compute-backend-abstraction.md §«Update 2026-05-19». "
                "For M1 use engine='llama_cpp'."
            )
        if engine not in _SUPPORTED_ENGINES:
            raise ValueError(
                f"ROCm backend engine={engine!r} not supported. M1 allowed: {_SUPPORTED_ENGINES}"
            )
        self._engine = engine
        _apply_rocm_env()

    @classmethod
    def available(cls) -> bool:
        """Lightweight: rocminfo есть + gfx1151 в targets + /dev/kfd доступен."""
        if not shutil.which("rocminfo"):
            return False
        host = detect_host()
        if host.gpu is None or not host.gpu.is_strix_halo:
            return False
        if "gfx1151" not in host.rocm.gfx_targets and "gfx11-generic" not in host.rocm.gfx_targets:
            log.debug("ROCm available but gfx1151 not in targets: %s", host.rocm.gfx_targets)
            return False
        return True

    @classmethod
    def make(cls, engine: str = "auto") -> ROCmBackend:
        if engine == "auto":
            engine = "llama_cpp"
        return cls(engine=engine)

    def device_info(self) -> DeviceInfo:
        host = detect_host()
        gpu = host.gpu
        rocm = host.rocm

        caps: dict[str, Any] = {
            "rocm_version": rocm.rocm_version,
            "gfx_targets": list(rocm.gfx_targets),
            "is_strix_halo": gpu.is_strix_halo if gpu else False,
            "bios_uma_gib": (gpu.bios_uma_bytes / 1024**3) if gpu else 0.0,
            "engine_implementation": "llama-cpp-python (GGML_HIP)",
        }
        total_mem = gpu.gtt_total_bytes if gpu else 0
        return DeviceInfo(
            backend=self.name,
            engine=self._engine,
            device_id=0,
            name=gpu.name if gpu else "ROCm device",
            total_memory_bytes=total_mem,
            capabilities=caps,
        )

    def load_llm(self, model_path: str, **kwargs: Any) -> LLMHandle:
        from agmind.compute.backends._engines.http_helper import try_http_handle

        http_handle = try_http_handle(model_path)
        if http_handle is not None:
            log.info("ROCm backend: using HTTP llama-server")
            return http_handle

        if not _llama_cpp_installed_with_hip():
            raise RuntimeError(
                "llama-cpp-python is not installed with HIP support. "
                "Either set AGMIND_LLAMA_SERVER_URL or rebuild: "
                "CMAKE_ARGS='-DGGML_HIP=ON -DAMDGPU_TARGETS=gfx1151 "
                "-DGPU_TARGETS=gfx1151 -DGGML_HIP_NO_VMM=ON "
                "-DGGML_HIP_ROCWMMA_FATTN=ON -DGGML_HIP_MMQ_MFMA=ON "
                "-DGGML_NATIVE=OFF' "
                "pip install --force-reinstall --no-binary llama-cpp-python "
                "'llama-cpp-python>=0.3.23'"
            )
        from agmind.compute.backends._engines.llama_cpp_hip import (
            LlamaCppHIPEngine,
        )

        return LlamaCppHIPEngine().load(model_path, **kwargs)

    def embed(self, texts: Sequence[str], model: str) -> list[list[float]]:
        from agmind.compute.backends._engines.http_helper import try_http_embed

        result = try_http_embed(texts, model)
        if result is not None:
            return result
        if not _llama_cpp_installed_with_hip():
            raise RuntimeError("llama-cpp-python (HIP) is not installed")
        from agmind.compute.backends._engines.llama_cpp_hip import (
            LlamaCppHIPEngine,
        )

        return LlamaCppHIPEngine().embed(texts, model)

    def rerank(self, query: str, documents: Sequence[str]) -> list[float]:
        from agmind.compute.backends._engines.http_helper import try_http_rerank

        result = try_http_rerank(query, documents)
        if result is not None:
            return result
        if not _llama_cpp_installed_with_hip():
            raise RuntimeError("llama-cpp-python (HIP) is not installed")
        from agmind.compute.backends._engines.llama_cpp_hip import (
            LlamaCppHIPEngine,
        )

        return LlamaCppHIPEngine().rerank(query, documents)


def _apply_rocm_env() -> None:
    """Idempotent: install ROCm runtime env vars at process level.

    НЕ устанавливаем HSA_OVERRIDE_GFX_VERSION — с AMD nightly gfx1151
    wheels это вызывает subtle bugs на attention/conv. Если пользователь
    явно установил override — уважаем.
    """
    os.environ.setdefault("PYTORCH_ROCM_ARCH", "gfx1151")
    os.environ.setdefault("PYTORCH_ALLOC_CONF", "expandable_segments:True")
    os.environ.setdefault("TORCH_ROCM_AOTRITON_ENABLE_EXPERIMENTAL", "1")
    os.environ.setdefault("ROCBLAS_USE_HIPBLASLT", "1")
    os.environ.setdefault("HSA_ENABLE_SDMA", "0")
    os.environ.setdefault("HIP_PLATFORM", "amd")
    os.environ.setdefault("MIOPEN_LOG_LEVEL", "3")


def _llama_cpp_installed_with_hip() -> bool:
    """Check llama_cpp + HIP backend (assume rebuild was done).

    llama-cpp-python не expose'ит backend info — best-effort через наличие
    модуля. Реальный check — через smoke-тест с моделью.
    """
    import importlib.util

    return importlib.util.find_spec("llama_cpp") is not None
