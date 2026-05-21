"""Phase M3.S.2: tests for multi-step wizard screens."""

from __future__ import annotations

import pytest

from agmind.cli.tui.setup_wizard import (
    AgmindSetupApp,
    DetectedHardware,
    SetupState,
)

pytestmark = pytest.mark.backend_any


def _detected() -> DetectedHardware:
    return DetectedHardware(
        ram_gb=128.0, gpu_name="x", is_strix_halo=True,
        vulkan_present=True, rocm_present=True, docker_present=True,
        recommended_tier="full",
    )


def test_multistep_default_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """M4.1: multi-step теперь DEFAULT."""
    for k in ("AGMIND_WIZARD_LEGACY", "AGMIND_WIZARD_MULTISTEP"):
        monkeypatch.delenv(k, raising=False)
    app = AgmindSetupApp(detected=_detected())
    assert app.multi_step is True


def test_legacy_env_forces_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """AGMIND_WIZARD_LEGACY=1 → single-screen (M4.1 escape hatch)."""
    monkeypatch.setenv("AGMIND_WIZARD_LEGACY", "1")
    monkeypatch.delenv("AGMIND_WIZARD_MULTISTEP", raising=False)
    app = AgmindSetupApp(detected=_detected())
    assert app.multi_step is False


def test_legacy_multistep_zero_also_disables(monkeypatch: pytest.MonkeyPatch) -> None:
    """Backward compat: старый AGMIND_WIZARD_MULTISTEP=0 продолжает работать."""
    monkeypatch.setenv("AGMIND_WIZARD_MULTISTEP", "0")
    monkeypatch.delenv("AGMIND_WIZARD_LEGACY", raising=False)
    app = AgmindSetupApp(detected=_detected())
    assert app.multi_step is False


def test_multistep_via_kwarg_true() -> None:
    app = AgmindSetupApp(detected=_detected(), multi_step=True)
    assert app.multi_step is True


def test_multistep_via_kwarg_false() -> None:
    app = AgmindSetupApp(detected=_detected(), multi_step=False)
    assert app.multi_step is False


def test_kwarg_overrides_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGMIND_WIZARD_LEGACY", "1")
    app = AgmindSetupApp(detected=_detected(), multi_step=True)
    assert app.multi_step is True


# ---- Pilot tests (Pilot + AGMIND_LOGO_DISABLE_ANIMATION=1) ----


@pytest.mark.asyncio
async def test_multistep_pushes_domain_screen_on_mount(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On_mount push'ит DomainScreen в multi-step mode."""
    monkeypatch.setenv("AGMIND_LOGO_DISABLE_ANIMATION", "1")
    app = AgmindSetupApp(detected=_detected(), multi_step=True)
    async with app.run_test(size=(140, 60)) as pilot:
        await pilot.pause(0.1)
        # Top of stack должен быть DomainScreen
        from agmind.cli.tui.wizard_screens import DomainScreen
        assert isinstance(app.screen, DomainScreen)


@pytest.mark.asyncio
async def test_multistep_domain_screen_has_inputs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGMIND_LOGO_DISABLE_ANIMATION", "1")
    app = AgmindSetupApp(detected=_detected(), multi_step=True)
    async with app.run_test(size=(140, 60)) as pilot:
        await pilot.pause(0.1)
        domain_input = app.screen.query_one("#domain-input")
        token_input = app.screen.query_one("#cf-token-input")
        assert domain_input is not None
        assert token_input is not None


@pytest.mark.asyncio
async def test_multistep_navigate_domain_to_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGMIND_LOGO_DISABLE_ANIMATION", "1")
    initial = SetupState(
        domain="lab.example.com",
        cf_api_token="X" * 40,
    )
    app = AgmindSetupApp(detected=_detected(), initial_state=initial, multi_step=True)
    async with app.run_test(size=(140, 60)) as pilot:
        await pilot.pause(0.1)
        from agmind.cli.tui.wizard_screens import DomainScreen, ModelScreen
        assert isinstance(app.screen, DomainScreen)
        # Press Next button через keypress alt+n
        await pilot.press("alt+n")
        await pilot.pause(0.1)
        # Top should now be ModelScreen
        assert isinstance(app.screen, ModelScreen)


@pytest.mark.asyncio
async def test_multistep_back_button_returns_to_previous(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGMIND_LOGO_DISABLE_ANIMATION", "1")
    initial = SetupState(domain="lab.example.com", cf_api_token="X" * 40)
    app = AgmindSetupApp(detected=_detected(), initial_state=initial, multi_step=True)
    async with app.run_test(size=(140, 60)) as pilot:
        await pilot.pause(0.1)
        await pilot.press("alt+n")  # Domain → Model
        await pilot.pause(0.1)
        await pilot.press("alt+b")  # Model → Domain
        await pilot.pause(0.1)
        from agmind.cli.tui.wizard_screens import DomainScreen
        assert isinstance(app.screen, DomainScreen)
