"""Pytest config + fixtures для agmind."""

from __future__ import annotations

import logging
import os
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Очистить AGMIND_* env vars для теста — изоляция от user env."""
    for key in list(os.environ.keys()):
        if key.startswith("AGMIND_"):
            monkeypatch.delenv(key, raising=False)


@pytest.fixture(autouse=True)
def _clear_detect_host_cache() -> None:
    """detect_host is @lru_cache(maxsize=1) (process-wide); clear it before each test so one
    test's hardware mock can't leak into the next via the cache."""
    from agmind.compute.detect import detect_host

    detect_host.cache_clear()


@pytest.fixture(autouse=True)
def _restore_root_logging() -> Iterator[None]:
    """Restore root logging handlers after each test.

    `agmind.core.logging.setup()` calls `logging.basicConfig(stream=sys.stderr,
    force=True)`, which snapshots the *current* `sys.stderr`. When that runs
    inside a CliRunner invocation, the installed handler binds to a transient
    capture buffer that is closed once the invocation ends. Left in place it
    corrupts later tests' captured stderr. Snapshot/restore keeps that leak from
    crossing the test boundary.
    """
    root = logging.getLogger()
    saved_handlers = root.handlers[:]
    saved_level = root.level
    try:
        yield
    finally:
        root.handlers[:] = saved_handlers
        root.setLevel(saved_level)


@pytest.fixture(autouse=True)
def _reset_i18n_cache() -> Iterator[None]:
    """Clear the i18n catalog cache per test.

    `agmind.i18n` memoises loaded catalogs in a module-level ``_loaded`` dict.
    A test that stubs or monkeypatches catalog contents would otherwise leak
    into later tests under pytest-randomly. Snapshot/restore isolates it.
    """
    import agmind.i18n as i18n

    saved = dict(i18n._loaded)
    try:
        yield
    finally:
        i18n._loaded.clear()
        i18n._loaded.update(saved)


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
