"""Contract tests для backend ABC — параметризованные по backend.

Маркеры:
- backend_any — backend-agnostic, всегда выполняется
- backend_cpu — требует CPU backend (всегда available)
- backend_vulkan — требует Vulkan + gfx1151 + llama-cpp-python с GGML_VULKAN
- backend_rocm — требует ROCm + gfx1151 + llama-cpp-python с GGML_HIP

См. pyproject.toml [tool.pytest.ini_options] markers.
"""

from __future__ import annotations

import pytest

from agmind.compute import Backend, DeviceInfo, get_backend, list_available_backends
from agmind.compute.config import Profile, read_config

# ---- backend-agnostic тесты ----


@pytest.mark.backend_any
def test_list_available_backends_always_contains_cpu() -> None:
    """CPU backend всегда available — критический fallback."""
    assert "cpu" in list_available_backends()


@pytest.mark.backend_any
def test_get_backend_default_returns_backend() -> None:
    b = get_backend()
    assert isinstance(b, Backend)
    di = b.device_info()
    assert isinstance(di, DeviceInfo)
    assert di.backend in {"cpu", "vulkan", "rocm"}


@pytest.mark.backend_any
def test_get_backend_explicit_cpu(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGMIND_BACKEND", "cpu")
    b = get_backend()
    assert b.device_info().backend == "cpu"


@pytest.mark.backend_any
def test_get_backend_invalid_backend_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGMIND_BACKEND", "invalidbackend")
    with pytest.raises(ValueError, match="AGMIND_BACKEND"):
        get_backend()


@pytest.mark.backend_any
def test_get_backend_invalid_engine_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGMIND_ENGINE", "invalidengine")
    with pytest.raises(ValueError, match="AGMIND_ENGINE"):
        get_backend()


@pytest.mark.backend_any
def test_read_config_default_profile() -> None:
    cfg = read_config()
    assert cfg.profile == Profile.MIXED


@pytest.mark.backend_any
def test_read_config_explicit_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGMIND_BACKEND_PROFILE", "tg")
    cfg = read_config()
    assert cfg.profile == Profile.TG


@pytest.mark.backend_any
def test_read_config_invalid_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGMIND_BACKEND_PROFILE", "weird")
    with pytest.raises(ValueError, match="AGMIND_BACKEND_PROFILE"):
        read_config()


# ---- CPU backend tests (always available) ----


@pytest.mark.backend_cpu
def test_cpu_backend_available() -> None:
    from agmind.compute.backends.cpu import CPUBackend

    assert CPUBackend.available() is True


@pytest.mark.backend_cpu
def test_cpu_backend_device_info(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGMIND_BACKEND", "cpu")
    b = get_backend()
    di = b.device_info()
    assert di.backend == "cpu"
    assert di.engine == "llama_cpp"
    assert di.total_memory_bytes > 0
    assert "cpu_model" in di.capabilities


@pytest.mark.backend_cpu
def test_cpu_backend_engine_rejection() -> None:
    from agmind.compute.backends.cpu import CPUBackend

    with pytest.raises(ValueError, match="not supported"):
        CPUBackend.make(engine="vllm")


# ---- NPU stub ----


@pytest.mark.backend_any
def test_npu_stub_not_available() -> None:
    from agmind.compute.backends.npu_stub import NPUStubBackend

    assert NPUStubBackend.available() is False


@pytest.mark.backend_any
def test_npu_stub_load_llm_raises() -> None:
    from agmind.compute.backends.npu_stub import NPUStubBackend

    npu = NPUStubBackend.make()
    with pytest.raises(NotImplementedError, match="RyzenAI-SW"):
        npu.load_llm("/some/model")


# ---- Vulkan / ROCm backends (real hardware required) ----


@pytest.mark.backend_vulkan
def test_vulkan_backend_available_if_vulkaninfo_and_amd(
    has_vulkan: bool, has_strix_halo: bool
) -> None:
    from agmind.compute.backends.vulkan import VulkanBackend

    if not has_vulkan or not has_strix_halo:
        pytest.skip("vulkaninfo missing or not on Strix Halo")
    assert VulkanBackend.available() is True


@pytest.mark.backend_vulkan
def test_vulkan_backend_engine_only_llama_cpp() -> None:
    from agmind.compute.backends.vulkan import VulkanBackend

    with pytest.raises(ValueError, match="not supported"):
        VulkanBackend.make(engine="vllm")


@pytest.mark.backend_rocm
def test_rocm_backend_available_if_rocminfo_gfx1151(has_rocm: bool, has_strix_halo: bool) -> None:
    from agmind.compute.backends.rocm import ROCmBackend

    if not has_rocm or not has_strix_halo:
        pytest.skip("rocminfo missing or not on Strix Halo")
    assert ROCmBackend.available() is True


@pytest.mark.backend_rocm
def test_rocm_backend_m2_engines_not_implemented() -> None:
    from agmind.compute.backends.rocm import ROCmBackend

    with pytest.raises(NotImplementedError, match="M2"):
        ROCmBackend.make(engine="vllm")
    with pytest.raises(NotImplementedError, match="M2"):
        ROCmBackend.make(engine="infinity")


# ---- LLM load (requires llama_cpp + actual model) ----


@pytest.mark.backend_cpu
def test_cpu_load_llm_requires_llama_cpp(
    has_llama_cpp: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGMIND_BACKEND", "cpu")
    b = get_backend()
    if has_llama_cpp:
        # Без actual GGUF model — load_llm крашится позже на disk-not-found.
        # Здесь проверяем что метод не raise NotImplementedError.
        with pytest.raises((FileNotFoundError, OSError, ValueError, RuntimeError)):
            b.load_llm("/tmp/nonexistent-model.gguf")
    else:
        with pytest.raises(RuntimeError, match="llama-cpp-python is not installed"):
            b.load_llm("/tmp/nonexistent.gguf")
