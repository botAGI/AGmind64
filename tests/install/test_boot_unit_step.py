"""BootUnitStep — install+enable the agmind-stack.service boot unit on the REAL python path.

Root cause it closes (live 2026-06-13: after an unclean power-loss the host came back but
prometheus stayed Exited(255) and Grafana showed "No data" for everything): the ordered boot
bring-up unit lived ONLY in the ansible `services` role, which `agmind install` never runs (it
invokes ansible `--tags bootstrap` only — the SAME gap class as GpuMetricsStep). So no install
survived a reboot ordered. And even the ansible-installed unit ran a bare `docker compose up -d`,
which skips EVERY profiled service (the rendered compose tags all services with `profiles:`), so
it would bring up zero services.

These tests assert BootUnitStep:
  * is wired into default_steps() after deploy (no orphaned-step trap),
  * no-ops (success) without a sudo password or without a selected service set,
  * writes /etc/systemd/system/agmind-stack.service + daemon-reload + `enable` (NOT --now: it must
    never restart the just-deployed stack — only arm it for the next boot),
  * renders an ExecStart carrying the deployed `--profile` flags so the profiled compose starts,
  * never fails the install if a sudo command errors (degraded boot-survival, not a deploy blocker).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agmind.install import steps as steps_mod
from agmind.install.orchestrator import InstallConfig
from agmind.install.steps import BootUnitStep, default_steps

pytestmark = pytest.mark.backend_any


def _config(tmp_path: Path, services: list[str]) -> InstallConfig:
    install_dir = tmp_path / "opt" / "agmind"
    install_dir.mkdir(parents=True, exist_ok=True)
    (install_dir / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    return InstallConfig(
        domain="lab.example.com",
        cf_api_token="X" * 40,
        services=services,
        install_dir=install_dir,
        models_dir=tmp_path / "var" / "models",
        sudo_password="sup3rs3cret",
    )


class _SudoRecorder:
    """Capture every sudo runtime command the step issues (no real sudo)."""

    def __init__(self) -> None:
        self.commands: list[list[str]] = []

    def __call__(self, config, cmd, callback, step_id):  # noqa: ANN001
        self.commands.append(list(cmd))


def test_boot_unit_in_default_pipeline() -> None:
    """Must be in default_steps() after deploy so the live install path actually arms it."""
    ids = [s.step_id for s in default_steps()]
    assert "boot_unit" in ids, "BootUnitStep not wired into default_steps (orphaned-step trap)"
    assert ids.index("boot_unit") > ids.index("deploy")


def test_skips_without_sudo_password(tmp_path: Path, monkeypatch) -> None:
    rec = _SudoRecorder()
    monkeypatch.setattr(steps_mod, "_run_sudo_runtime_command", rec)
    cfg = _config(tmp_path, services=["prometheus", "grafana"])
    cfg.sudo_password = None
    result = BootUnitStep().run(lambda _e: None, cfg)
    assert result.success
    assert rec.commands == [], "must not touch the host without a sudo password"


def test_skips_without_services(tmp_path: Path, monkeypatch) -> None:
    rec = _SudoRecorder()
    monkeypatch.setattr(steps_mod, "_run_sudo_runtime_command", rec)
    cfg = _config(tmp_path, services=[])
    result = BootUnitStep().run(lambda _e: None, cfg)
    assert result.success
    assert "skipped" in result.message.lower()
    assert rec.commands == []


def test_installs_and_enables_boot_unit(tmp_path: Path, monkeypatch) -> None:
    rec = _SudoRecorder()
    monkeypatch.setattr(steps_mod, "_run_sudo_runtime_command", rec)
    cfg = _config(tmp_path, services=["prometheus", "grafana", "node-exporter"])
    result = BootUnitStep().run(lambda _e: None, cfg)

    assert result.success, result.message
    flat = [" ".join(c) for c in rec.commands]
    # unit file written to /etc/systemd/system/agmind-stack.service
    assert any(steps_mod._AGMIND_STACK_SERVICE_PATH in c for c in flat), flat
    # systemd reloaded + unit ENABLED for boot — but NOT --now (would restart the live stack)
    assert ["systemctl", "daemon-reload"] in rec.commands
    assert ["systemctl", "enable", "agmind-stack.service"] in rec.commands
    assert not any("--now" in c for c in flat), "enable --now would restart the just-deployed stack"


def test_execstart_activates_profiles() -> None:
    unit = steps_mod._agmind_stack_unit("/opt/agmind", ["core", "observability"])
    assert "--profile core" in unit
    assert "--profile observability" in unit
    assert "up -d" in unit
    assert "/opt/agmind/docker-compose.yml" in unit
    assert "Requires=docker.service" in unit
    assert "After=docker.service" in unit
    assert "Type=oneshot" in unit
    assert "RemainAfterExit=yes" in unit
    assert "WantedBy=multi-user.target" in unit


def test_profiles_resolved_from_selected_services() -> None:
    """prometheus/grafana are observability-profile services → the unit must activate it."""
    profiles = steps_mod._selected_compose_profiles(["prometheus", "grafana"])
    assert "observability" in profiles


def test_never_fails_install_on_sudo_error(tmp_path: Path, monkeypatch) -> None:
    def boom(*_a, **_k):  # noqa: ANN002, ANN003
        raise OSError("sudo command failed rc=1: install")

    monkeypatch.setattr(steps_mod, "_run_sudo_runtime_command", boom)
    cfg = _config(tmp_path, services=["prometheus"])
    result = BootUnitStep().run(lambda _e: None, cfg)
    assert result.success, "a failed boot-unit install must not roll back a deployed stack"
    assert "skipped" in result.message.lower()
