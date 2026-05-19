"""Tests для engine selection внутри backend.

Покрывает:
- CPU: только llama_cpp; неверные engine → ValueError
- Vulkan: только llama_cpp; неверные → ValueError
- ROCm: только llama_cpp в M1; M2 engines (vllm/infinity) → NotImplementedError
- NPU stub: any engine → подняться, но все методы NotImplementedError
"""

from __future__ import annotations

import pytest

from agmind.compute.backends.cpu import CPUBackend
from agmind.compute.backends.npu_stub import NPUStubBackend
from agmind.compute.backends.rocm import ROCmBackend
from agmind.compute.backends.vulkan import VulkanBackend

pytestmark = pytest.mark.backend_any


# ---- CPU backend ----


def test_cpu_make_default_engine() -> None:
    b = CPUBackend.make()
    assert b.device_info().engine == "llama_cpp"


def test_cpu_make_explicit_llama_cpp() -> None:
    b = CPUBackend.make(engine="llama_cpp")
    assert b.device_info().engine == "llama_cpp"


def test_cpu_make_reject_vulkan_engine() -> None:
    with pytest.raises(ValueError, match="not supported"):
        CPUBackend.make(engine="vllm")


def test_cpu_make_reject_infinity() -> None:
    with pytest.raises(ValueError, match="not supported"):
        CPUBackend.make(engine="infinity")


def test_cpu_make_reject_random_string() -> None:
    with pytest.raises(ValueError, match="not supported"):
        CPUBackend.make(engine="bogus")


def test_cpu_available_always_true() -> None:
    assert CPUBackend.available() is True


# ---- Vulkan backend ----


def test_vulkan_make_default_engine() -> None:
    """Vulkan default engine = llama_cpp (assert_no_amdvlk может сработать)."""
    try:
        b = VulkanBackend.make()
    except RuntimeError as exc:
        # ОК если на тестовой машине есть AMDVLK leak — assert правильный
        assert "AMDVLK" in str(exc)
        pytest.skip("AMDVLK present — Vulkan refuses to start")
    assert b.device_info().engine == "llama_cpp"


def test_vulkan_make_reject_vllm() -> None:
    with pytest.raises(ValueError, match="not supported"):
        VulkanBackend.make(engine="vllm")


def test_vulkan_make_reject_infinity() -> None:
    with pytest.raises(ValueError, match="not supported"):
        VulkanBackend.make(engine="infinity")


# ---- ROCm backend ----


def test_rocm_make_default_llama_cpp() -> None:
    b = ROCmBackend.make()
    assert b.device_info().engine == "llama_cpp"


def test_rocm_make_m2_vllm_not_implemented() -> None:
    with pytest.raises(NotImplementedError, match="M2"):
        ROCmBackend.make(engine="vllm")


def test_rocm_make_m2_infinity_not_implemented() -> None:
    with pytest.raises(NotImplementedError, match="M2"):
        ROCmBackend.make(engine="infinity")


def test_rocm_make_reject_random_engine() -> None:
    with pytest.raises(ValueError, match="not supported"):
        ROCmBackend.make(engine="totallymadeup")


# ---- NPU stub ----


def test_npu_available_returns_false() -> None:
    assert NPUStubBackend.available() is False


def test_npu_make_returns_stub_engine() -> None:
    b = NPUStubBackend.make()
    assert b.device_info().engine == "stub"


def test_npu_load_llm_not_implemented() -> None:
    b = NPUStubBackend.make()
    with pytest.raises(NotImplementedError, match="RyzenAI-SW"):
        b.load_llm("/any/path")


def test_npu_embed_not_implemented() -> None:
    b = NPUStubBackend.make()
    with pytest.raises(NotImplementedError):
        b.embed(["text"], "model")


def test_npu_rerank_not_implemented() -> None:
    b = NPUStubBackend.make()
    with pytest.raises(NotImplementedError):
        b.rerank("query", ["doc"])


# ---- DeviceInfo invariants ----


def test_device_info_backend_name_consistent() -> None:
    """backend в DeviceInfo соответствует class.name."""
    for backend_cls in (CPUBackend, NPUStubBackend):
        b = backend_cls.make()
        di = b.device_info()
        assert di.backend == backend_cls.name


def test_device_info_capabilities_is_dict() -> None:
    b = CPUBackend.make()
    di = b.device_info()
    assert isinstance(di.capabilities, dict)


def test_device_info_total_memory_non_negative() -> None:
    for backend_cls in (CPUBackend, NPUStubBackend):
        b = backend_cls.make()
        di = b.device_info()
        assert di.total_memory_bytes >= 0
