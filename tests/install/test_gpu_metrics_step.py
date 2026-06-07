"""GpuMetricsStep — port the AMD GPU textfile-exporter systemd timer onto the REAL install path.

Root cause it closes (audit grafana-dashboards CRITICAL-2 / installer-gaps INST-1): the
amdgpu-metrics service+timer that runs scripts/ops/amdgpu_textfile.sh existed ONLY in the
ansible observability role, which `agmind install` never runs (it invokes ansible with
`--tags bootstrap` only). So /var/lib/node_exporter/textfile/amdgpu.prom was never produced
and the Grafana GPU dashboard was permanently blank.

These tests assert the step:
  * is wired into default_steps() so the live path actually runs it (no orphaned-step trap),
  * no-ops (success) when observability/node-exporter is not selected,
  * no-ops (success) on a non-AMD host (CPU-only worker must not run the 15s timer),
  * installs the script + both unit files + enables the timer when observability + AMD,
  * never fails the install if a sudo command errors (degraded obs, not a deploy blocker).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agmind.install import steps as steps_mod
from agmind.install.orchestrator import InstallConfig
from agmind.install.steps import GpuMetricsStep, default_steps

pytestmark = pytest.mark.backend_any


def _config(tmp_path: Path, services: list[str]) -> InstallConfig:
    return InstallConfig(
        domain="lab.example.com",
        cf_api_token="X" * 40,
        services=services,
        install_dir=tmp_path / "opt" / "agmind",
        models_dir=tmp_path / "var" / "models",
        sudo_password="sup3rs3cret",
    )


class _SudoRecorder:
    """Capture every sudo runtime command the step issues (no real sudo)."""

    def __init__(self) -> None:
        self.commands: list[list[str]] = []

    def __call__(self, config, cmd, callback, step_id):  # noqa: ANN001
        self.commands.append(list(cmd))


def test_gpu_metrics_step_in_default_pipeline() -> None:
    """The step must be in default_steps() between deploy and credentials (live path runs it)."""
    ids = [s.step_id for s in default_steps()]
    assert "gpu_metrics" in ids, "GpuMetricsStep not wired into default_steps (orphaned-step trap)"
    assert ids.index("gpu_metrics") > ids.index("deploy")
    assert ids.index("gpu_metrics") < ids.index("credentials")


def test_skips_when_node_exporter_not_selected(tmp_path: Path, monkeypatch) -> None:
    rec = _SudoRecorder()
    monkeypatch.setattr(steps_mod, "_run_sudo_runtime_command", rec)
    monkeypatch.setattr(steps_mod, "_host_has_amd_gpu", lambda: True)

    cfg = _config(tmp_path, services=["traefik", "llama-llm"])
    result = GpuMetricsStep().run(lambda _e: None, cfg)

    assert result.success
    assert "skipped" in result.message.lower()
    assert rec.commands == [], "must not touch the host when observability is not selected"


def test_skips_on_non_amd_host(tmp_path: Path, monkeypatch) -> None:
    rec = _SudoRecorder()
    monkeypatch.setattr(steps_mod, "_run_sudo_runtime_command", rec)
    monkeypatch.setattr(steps_mod, "_host_has_amd_gpu", lambda: False)

    cfg = _config(tmp_path, services=["node-exporter", "prometheus", "grafana"])
    result = GpuMetricsStep().run(lambda _e: None, cfg)

    assert result.success
    assert "amd" in result.message.lower()
    assert rec.commands == [], "the 15s timer must not be installed on a CPU-only node"


def test_skips_without_sudo_password(tmp_path: Path, monkeypatch) -> None:
    rec = _SudoRecorder()
    monkeypatch.setattr(steps_mod, "_run_sudo_runtime_command", rec)
    monkeypatch.setattr(steps_mod, "_host_has_amd_gpu", lambda: True)

    cfg = _config(tmp_path, services=["node-exporter"])
    cfg.sudo_password = None
    result = GpuMetricsStep().run(lambda _e: None, cfg)

    assert result.success
    assert rec.commands == []


def test_installs_script_units_and_enables_timer(tmp_path: Path, monkeypatch) -> None:
    rec = _SudoRecorder()
    monkeypatch.setattr(steps_mod, "_run_sudo_runtime_command", rec)
    monkeypatch.setattr(steps_mod, "_host_has_amd_gpu", lambda: True)

    cfg = _config(tmp_path, services=["node-exporter", "prometheus", "grafana"])
    result = GpuMetricsStep().run(lambda _e: None, cfg)

    assert result.success, result.message
    flat = [" ".join(c) for c in rec.commands]

    # textfile dir pre-created
    assert any("install -d" in c and steps_mod._GPU_METRICS_TEXTFILE_DIR in c for c in flat)
    # exporter script shipped to {install_dir}/scripts/ops/amdgpu_textfile.sh
    dest_script = str(cfg.install_dir / "scripts" / "ops" / "amdgpu_textfile.sh")
    assert any(dest_script in c and "0755" in c for c in flat), flat
    # both systemd unit files written to /etc/systemd/system
    assert any(steps_mod._GPU_METRICS_SERVICE_PATH in c for c in flat)
    assert any(steps_mod._GPU_METRICS_TIMER_PATH in c for c in flat)
    # systemd reloaded + timer enabled now
    assert ["systemctl", "daemon-reload"] in rec.commands
    assert ["systemctl", "enable", "--now", "amdgpu-metrics.timer"] in rec.commands


def test_service_unit_execstart_points_at_install_dir_script(tmp_path: Path) -> None:
    dest = str(tmp_path / "opt" / "agmind" / "scripts" / "ops" / "amdgpu_textfile.sh")
    unit = steps_mod._gpu_metrics_service_unit(dest)
    assert f"ExecStart={dest}" in unit
    assert f"Environment=TEXTFILE_DIR={steps_mod._GPU_METRICS_TEXTFILE_DIR}" in unit
    assert "Type=oneshot" in unit
    # hardening carried over from the ansible .j2
    assert "ProtectSystem=strict" in unit
    assert f"ReadWritePaths={steps_mod._GPU_METRICS_TEXTFILE_DIR}" in unit


def test_timer_unit_runs_every_15s() -> None:
    unit = steps_mod._GPU_METRICS_TIMER_UNIT
    assert "OnUnitActiveSec=15s" in unit
    assert "Unit=amdgpu-metrics.service" in unit
    assert "WantedBy=timers.target" in unit


def test_never_fails_install_on_sudo_error(tmp_path: Path, monkeypatch) -> None:
    def boom(*_a, **_k):  # noqa: ANN002, ANN003
        raise OSError("sudo command failed rc=1: install")

    monkeypatch.setattr(steps_mod, "_run_sudo_runtime_command", boom)
    monkeypatch.setattr(steps_mod, "_host_has_amd_gpu", lambda: True)

    cfg = _config(tmp_path, services=["node-exporter"])
    result = GpuMetricsStep().run(lambda _e: None, cfg)

    assert result.success, "a failed GPU-timer install must not roll back a deployed stack"
    assert "skipped" in result.message.lower()


def test_host_has_amd_gpu_reads_vendor(tmp_path: Path) -> None:
    drm = tmp_path / "drm"
    amd = drm / "card0" / "device"
    amd.mkdir(parents=True)
    (amd / "vendor").write_text("0x1002\n", encoding="utf-8")
    assert steps_mod._host_has_amd_gpu(drm) is True


def test_host_has_amd_gpu_false_for_non_amd(tmp_path: Path) -> None:
    drm = tmp_path / "drm"
    other = drm / "card0" / "device"
    other.mkdir(parents=True)
    (other / "vendor").write_text("0x10de\n", encoding="utf-8")  # not AMD
    assert steps_mod._host_has_amd_gpu(drm) is False


def test_host_has_amd_gpu_false_when_no_drm(tmp_path: Path) -> None:
    assert steps_mod._host_has_amd_gpu(tmp_path / "absent") is False
