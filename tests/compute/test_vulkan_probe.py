"""Phase 09-05 (M8): the Vulkan capability probe must verify a GPU build, not just import.

`_llama_cpp_installed_with_vulkan()` used to return `find_spec("llama_cpp") is not None`, yet
the error it gated claimed "not installed with Vulkan support" — a claim the check never
proved (a CPU-only llama-cpp-python would pass). It now consults
`llama_supports_gpu_offload()` (True only when llama.cpp was compiled with a GPU backend; on
Strix Halo gfx1151 the only GPU backend is Vulkan/RADV)."""

from __future__ import annotations

import importlib.util
import sys
import types

import pytest

from agmind.compute.backends import vulkan

pytestmark = pytest.mark.backend_any

_SENTINEL = object()


def _fake_find_spec(name: str) -> object | None:
    return _SENTINEL if name == "llama_cpp" else None


def test_false_when_llama_cpp_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
    assert vulkan._llama_cpp_installed_with_vulkan() is False


def test_true_when_gpu_offload_supported(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = types.ModuleType("llama_cpp")
    mod.llama_supports_gpu_offload = lambda: True  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "llama_cpp", mod)
    monkeypatch.setattr(importlib.util, "find_spec", _fake_find_spec)
    assert vulkan._llama_cpp_installed_with_vulkan() is True


def test_false_when_present_but_cpu_only_build(monkeypatch: pytest.MonkeyPatch) -> None:
    """The real improvement: a CPU-only llama-cpp-python must NOT pass the Vulkan gate."""
    mod = types.ModuleType("llama_cpp")
    mod.llama_supports_gpu_offload = lambda: False  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "llama_cpp", mod)
    monkeypatch.setattr(importlib.util, "find_spec", _fake_find_spec)
    assert vulkan._llama_cpp_installed_with_vulkan() is False


def test_fallback_to_import_presence_when_symbol_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Older wheels without the symbol fall back to import presence (smoke test decides)."""
    mod = types.ModuleType("llama_cpp")  # no llama_supports_gpu_offload attr
    monkeypatch.setitem(sys.modules, "llama_cpp", mod)
    monkeypatch.setattr(importlib.util, "find_spec", _fake_find_spec)
    assert vulkan._llama_cpp_installed_with_vulkan() is True
