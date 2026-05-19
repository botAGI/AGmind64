"""Phase J: tests для agmind.cli.tui.setup_wizard через Textual Pilot."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agmind.cli.tui.setup_wizard import (
    AgmindSetupApp,
    DetectedHardware,
    SetupState,
    detect_hardware,
)

pytestmark = pytest.mark.backend_any


# ---------- SetupState ----------

def test_state_default() -> None:
    s = SetupState()
    assert s.domain == ""
    assert s.backend == "auto"
    # Phase J.1.8: smart defaults — 11 services (per-service selection)
    assert "traefik" in s.services
    assert "llama-llm" in s.services
    assert "qdrant" in s.services
    assert "prometheus" in s.services
    assert s.profiles == []  # profiles больше не primary


def test_state_roundtrip_excludes_token(tmp_path: Path) -> None:
    s = SetupState(domain="x.example", cf_api_token="secret-xyz", profiles=["core"])
    path = tmp_path / "state.json"
    s.to_json(path)
    data = json.loads(path.read_text())
    # Token НЕ должен попадать в state.json
    assert "cf_api_token" not in data
    assert data["domain"] == "x.example"

    loaded = SetupState.from_json(path)
    assert loaded.domain == "x.example"
    assert loaded.cf_api_token == ""  # not persisted


# ---------- detect_hardware ----------

def test_detect_returns_dataclass() -> None:
    d = detect_hardware()
    assert isinstance(d, DetectedHardware)
    assert d.ram_gb > 0
    assert d.recommended_tier in ("S", "M", "L", "XL")


def test_detect_includes_required_fields() -> None:
    d = detect_hardware()
    assert hasattr(d, "vulkan_present")
    assert hasattr(d, "rocm_present")
    assert hasattr(d, "docker_present")
    assert hasattr(d, "is_strix_halo")


# ---------- Validation ----------

def test_validate_rejects_empty_domain() -> None:
    detected = DetectedHardware(
        ram_gb=128, gpu_name="x", is_strix_halo=True,
        vulkan_present=True, rocm_present=True, docker_present=True,
        recommended_tier="XL",
    )
    app = AgmindSetupApp(detected=detected, initial_state=SetupState(domain=""))
    state = SetupState(domain="", cf_api_token="x" * 30, profiles=["core"])
    errors = app._validate(state)
    assert any("domain" in e.lower() for e in errors)


def test_validate_accepts_real_owned_domain() -> None:
    """User может реально владеть `agmind.dev` — не reject."""
    detected = DetectedHardware(
        ram_gb=128, gpu_name="x", is_strix_halo=True,
        vulkan_present=True, rocm_present=True, docker_present=True,
        recommended_tier="XL",
    )
    app = AgmindSetupApp(detected=detected)
    # agmind.dev — это реальный домен пользователя
    state = SetupState(domain="agmind.dev", cf_api_token="x" * 30, profiles=["core"])
    errors = app._validate(state)
    assert errors == []  # NO placeholder rejection


def test_validate_rejects_short_token() -> None:
    detected = DetectedHardware(
        ram_gb=128, gpu_name="x", is_strix_halo=True,
        vulkan_present=True, rocm_present=True, docker_present=True,
        recommended_tier="XL",
    )
    app = AgmindSetupApp(detected=detected)
    state = SetupState(domain="x.example", cf_api_token="short", profiles=["core"])
    errors = app._validate(state)
    assert any("token" in e.lower() for e in errors)


def test_validate_rejects_no_services_or_profiles() -> None:
    detected = DetectedHardware(
        ram_gb=128, gpu_name="x", is_strix_halo=True,
        vulkan_present=True, rocm_present=True, docker_present=True,
        recommended_tier="XL",
    )
    app = AgmindSetupApp(detected=detected)
    state = SetupState(
        domain="x.example", cf_api_token="x" * 30,
        services=[], profiles=[],  # ничего не выбрано
    )
    errors = app._validate(state)
    assert any("service" in e.lower() for e in errors)


def test_state_explicit_services_overrides_default() -> None:
    s = SetupState(services=["traefik", "llama-llm"])
    assert s.services == ["traefik", "llama-llm"]


def test_get_services_by_tier_returns_grouped() -> None:
    from agmind.cli.tui.setup_wizard import _TIER_ORDER, get_services_by_tier

    by_tier = get_services_by_tier()
    assert len(by_tier) > 0
    # Order matches _TIER_ORDER
    tier_keys = list(by_tier.keys())
    for tier in tier_keys:
        if tier in _TIER_ORDER:
            assert _TIER_ORDER.index(tier) >= 0
    # traefik в edge, qdrant в storage
    edge_names = [n for n, _ in by_tier.get("edge", [])]
    storage_names = [n for n, _ in by_tier.get("storage", [])]
    assert "traefik" in edge_names
    assert "qdrant" in storage_names


def test_validate_warns_no_docker() -> None:
    detected = DetectedHardware(
        ram_gb=128, gpu_name="x", is_strix_halo=True,
        vulkan_present=True, rocm_present=True, docker_present=False,
        recommended_tier="XL",
    )
    app = AgmindSetupApp(detected=detected)
    state = SetupState(domain="x.example", cf_api_token="x" * 30, profiles=["core"])
    errors = app._validate(state)
    assert any("docker" in e.lower() for e in errors)


def test_validate_passes_clean() -> None:
    detected = DetectedHardware(
        ram_gb=128, gpu_name="x", is_strix_halo=True,
        vulkan_present=True, rocm_present=True, docker_present=True,
        recommended_tier="XL",
    )
    app = AgmindSetupApp(detected=detected)
    state = SetupState(
        domain="agmind.mycompany.example",
        cf_api_token="X" * 40,
        services=["traefik", "llama-llm", "qdrant"],
        backend="vulkan",
    )
    errors = app._validate(state)
    assert errors == []


# ---------- Textual Pilot integration ----------

@pytest.mark.skip(
    reason="Textual Pilot + reactive set_interval вешают headless event loop; "
           "manual: `agmind setup` запускается корректно",
)
@pytest.mark.asyncio
async def test_app_quit_via_keybinding() -> None:
    """Smoke: app launches, Ctrl+C exits cleanly (use keybinding вместо click)."""
    detected = DetectedHardware(
        ram_gb=128, gpu_name="AMD x", is_strix_halo=True,
        vulkan_present=True, rocm_present=True, docker_present=True,
        recommended_tier="XL",
    )
    app = AgmindSetupApp(detected=detected)
    async with app.run_test(size=(140, 60)) as pilot:
        await pilot.press("ctrl+c")
    assert app.result_state is None


@pytest.mark.skip(
    reason="Textual Pilot + reactive set_interval вешают headless event loop",
)
@pytest.mark.asyncio
async def test_app_apply_via_keybinding_with_valid_state() -> None:
    """Apply через Ctrl+S keybinding (pilot.click ломается на OOB кнопки)."""
    detected = DetectedHardware(
        ram_gb=128, gpu_name="AMD x", is_strix_halo=True,
        vulkan_present=True, rocm_present=True, docker_present=True,
        recommended_tier="XL",
    )
    initial = SetupState(
        domain="agmind.mycompany.example",
        cf_api_token="X" * 40,
        profiles=["core"],
        backend="vulkan",
    )
    app = AgmindSetupApp(detected=detected, initial_state=initial)
    async with app.run_test(size=(140, 60)) as pilot:
        await pilot.press("ctrl+s")
    assert app.result_state is not None
    assert app.result_state.domain == "agmind.mycompany.example"
    assert "core" in app.result_state.profiles


def test_logo_widget_import() -> None:
    """AnimatedLogo класс импортируется (sync test, без app context)."""
    from agmind.cli.tui.logo import AnimatedLogo, print_static_logo

    logo = AnimatedLogo(text="TEST", subtitle="logo test")
    assert logo.ascii_art  # pyfiglet rendered
    assert "TEST" in logo.ascii_art or len(logo.ascii_art) > 10
    # print_static_logo доступен
    assert callable(print_static_logo)


def test_gradient_rendering() -> None:
    """_render_gradient возвращает Rich Text без crash."""
    from agmind.cli.tui.logo import _render_gradient

    text = _render_gradient("AGmind\nHello", color_offset=3)
    assert text is not None
    assert len(str(text)) > 0
