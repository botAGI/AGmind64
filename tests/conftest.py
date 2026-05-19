"""Pytest config + fixtures для agmind."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Очистить AGMIND_* env vars для теста — изоляция от user env."""
    for key in list(os.environ.keys()):
        if key.startswith("AGMIND_"):
            monkeypatch.delenv(key, raising=False)


@pytest.fixture
def has_vulkan() -> bool:
    """True если vulkaninfo доступен на host (физический GPU есть)."""
    return shutil.which("vulkaninfo") is not None


@pytest.fixture
def has_rocm() -> bool:
    """True если rocminfo доступен."""
    return shutil.which("rocminfo") is not None


@pytest.fixture
def has_strix_halo() -> bool:
    """True если детектирован gfx1151 PCI device."""
    for card in Path("/sys/class/drm").glob("card[0-9]*"):
        try:
            vendor = (card / "device/vendor").read_text().strip()
            device = (card / "device/device").read_text().strip()
            if int(vendor, 0) == 0x1002 and int(device, 0) in (0x1586, 0x150E):
                return True
        except (OSError, ValueError):
            continue
    return False


@pytest.fixture
def has_llama_cpp() -> bool:
    """True если llama-cpp-python установлен (любой backend)."""
    import importlib.util

    return importlib.util.find_spec("llama_cpp") is not None
