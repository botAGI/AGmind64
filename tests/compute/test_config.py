"""Tests для agmind.compute.config — env vars parsing."""

from __future__ import annotations

import pytest

from agmind.compute.config import ComputeConfig, Profile, read_config

pytestmark = pytest.mark.backend_any


def test_read_config_defaults() -> None:
    cfg = read_config()
    assert isinstance(cfg, ComputeConfig)
    assert cfg.backend == "auto"
    assert cfg.engine == "auto"
    assert cfg.device_id == 0
    assert cfg.profile == Profile.MIXED


def test_read_config_explicit_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGMIND_BACKEND", "vulkan")
    cfg = read_config()
    assert cfg.backend == "vulkan"


def test_read_config_invalid_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGMIND_BACKEND", "invalidbackend")
    with pytest.raises(ValueError, match="AGMIND_BACKEND"):
        read_config()


def test_read_config_explicit_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGMIND_ENGINE", "llama_cpp")
    cfg = read_config()
    assert cfg.engine == "llama_cpp"


def test_read_config_invalid_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGMIND_ENGINE", "wrong-engine")
    with pytest.raises(ValueError, match="AGMIND_ENGINE"):
        read_config()


def test_read_config_device_id_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGMIND_DEVICE_ID", "0")
    cfg = read_config()
    assert cfg.device_id == 0


def test_read_config_device_id_positive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGMIND_DEVICE_ID", "3")
    cfg = read_config()
    assert cfg.device_id == 3


def test_read_config_device_id_negative_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGMIND_DEVICE_ID", "-1")
    with pytest.raises(ValueError, match="AGMIND_DEVICE_ID"):
        read_config()


def test_read_config_device_id_not_int(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGMIND_DEVICE_ID", "abc")
    with pytest.raises(ValueError, match="AGMIND_DEVICE_ID"):
        read_config()


@pytest.mark.parametrize("name", ["tg", "pp", "mixed", "embed_single", "embed_batch"])
def test_read_config_profiles(name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGMIND_BACKEND_PROFILE", name)
    cfg = read_config()
    assert cfg.profile.value == name


def test_read_config_invalid_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGMIND_BACKEND_PROFILE", "weirdprofile")
    with pytest.raises(ValueError, match="AGMIND_BACKEND_PROFILE"):
        read_config()


def test_profile_enum_values() -> None:
    """Profile names — stable; смена ломает CI configs."""
    assert {p.value for p in Profile} == {"tg", "pp", "mixed", "embed_single", "embed_batch"}


def test_compute_config_is_frozen() -> None:
    cfg = ComputeConfig()
    with pytest.raises((AttributeError, Exception)):
        cfg.backend = "rocm"  # type: ignore[misc]


def test_read_config_case_insensitive_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGMIND_BACKEND", "VULKAN")
    cfg = read_config()
    assert cfg.backend == "vulkan"


def test_read_config_strips_whitespace(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGMIND_BACKEND", "  rocm  ")
    cfg = read_config()
    assert cfg.backend == "rocm"
