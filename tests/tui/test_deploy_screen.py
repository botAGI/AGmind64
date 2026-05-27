"""Tests for deploy progress screen runner wiring."""

from __future__ import annotations

from pathlib import Path

import pytest

from agmind.cli.tui import deploy_screen
from agmind.cli.tui.deploy_screen import DeployProgressScreen
from agmind.deploy.runner import DeployResult

pytestmark = pytest.mark.backend_any


def test_deploy_progress_screen_keeps_legacy_positional_constructor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: dict[str, object] = {}

    def fake_deploy(**kwargs: object) -> DeployResult:
        calls.update(kwargs)
        return DeployResult(success=True, message="ok")

    monkeypatch.setattr(deploy_screen, "deploy", fake_deploy)

    screen = DeployProgressScreen(["core"], "lab.example.com", tmp_path)

    result = screen._deploy(lambda _step, _msg: None)

    assert result.success is True
    assert calls["profiles"] == ["core"]
    assert calls["services"] is None
    assert calls["domain"] == "lab.example.com"
    assert calls["install_dir"] == tmp_path


def test_deploy_progress_screen_passes_explicit_services_to_runner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: dict[str, object] = {}

    def fake_deploy(**kwargs: object) -> DeployResult:
        calls.update(kwargs)
        return DeployResult(success=True, message="ok")

    monkeypatch.setattr(deploy_screen, "deploy", fake_deploy)

    screen = DeployProgressScreen(
        profiles=["stale-profile"],
        services=["traefik"],
        domain="lab.example.com",
        install_dir=tmp_path,
    )

    result = screen._deploy(lambda _step, _msg: None)

    assert result.success is True
    assert calls["profiles"] == ["stale-profile"]
    assert calls["services"] == ["traefik"]
    assert calls["apply"] is True
