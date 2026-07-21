"""AMD GPU metrics exporter + systemd timer install step.

Split out of the historical single-file ``agmind/install/steps.py``; every name
here is re-exported from the package ``__init__``.
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from datetime import timedelta
from pathlib import Path

from agmind.install.orchestrator import (
    InstallConfig,
    InstallStep,
    InstallStepResult,
    ProgressCallback,
)

# `_run_sudo_runtime_command`, `_host_has_amd_gpu` and `DEFAULT_REPO_ROOT` are monkeypatched
# on the PACKAGE object by tests/install/test_gpu_metrics_step.py; resolve them through it at
# call time so the patches still reach this step after the package split (see configs.py).
_steps = sys.modules["agmind.install.steps"]

# ---- AMD Strix Halo GPU metrics: host textfile exporter + systemd timer ----
# node-exporter mounts /var/lib/node_exporter/textfile :ro and reads amdgpu.prom from it
# (templates/services/node-exporter.yaml). The script that refreshes that file lives in
# scripts/ops/amdgpu_textfile.sh, but the systemd service+timer that runs it on a 15s cadence
# previously existed ONLY in ansible/roles/observability — a role the Python installer never
# runs (it only invokes `ansible-playbook --tags bootstrap`). So on the supported install path
# the textfile was never produced and the Grafana GPU dashboard stayed blank. GpuMetricsStep
# (below) ports that timer install into default_steps() so the REAL path provisions it. The unit
# bodies are byte-equivalent to the ansible .j2 templates (agmind_install_dir → install_dir).
_GPU_METRICS_TEXTFILE_DIR = "/var/lib/node_exporter/textfile"
_GPU_METRICS_SERVICE_PATH = "/etc/systemd/system/amdgpu-metrics.service"
_GPU_METRICS_TIMER_PATH = "/etc/systemd/system/amdgpu-metrics.timer"


def _gpu_metrics_service_unit(script_path: str) -> str:
    """systemd oneshot that runs the AMD GPU textfile exporter (mirrors the ansible .j2)."""
    return f"""\
# Managed by AGmind installer (GpuMetricsStep) — do not edit by hand.
# AMD Strix Halo (gfx1151) GPU metrics → node-exporter textfile collector.
[Unit]
Description=AGmind AMD GPU metrics exporter (node-exporter textfile)
Documentation=file://{script_path}
ConditionPathIsDirectory={_GPU_METRICS_TEXTFILE_DIR}

[Service]
Type=oneshot
Environment=TEXTFILE_DIR={_GPU_METRICS_TEXTFILE_DIR}
ExecStart={script_path}
# Hardening — the exporter only reads /sys and writes the textfile dir.
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
ReadWritePaths={_GPU_METRICS_TEXTFILE_DIR}
"""


_GPU_METRICS_TIMER_UNIT = """\
# Managed by AGmind installer (GpuMetricsStep) — do not edit by hand.
[Unit]
Description=Run the AGmind AMD GPU metrics exporter every 15s

[Timer]
OnBootSec=15s
OnUnitActiveSec=15s
AccuracySec=1s
Unit=amdgpu-metrics.service

[Install]
WantedBy=timers.target
"""


def _host_has_amd_gpu(drm_root: Path = Path("/sys/class/drm")) -> bool:
    """True iff any ``<drm_root>/card*/device/vendor`` reports AMD (0x1002).

    Mirrors the self-skip in scripts/ops/amdgpu_textfile.sh so the systemd timer is
    only installed on AMD hosts (the CPU-only worker beelink must not run it every 15s).
    ``drm_root`` is parameterized for tests; the live default is the sysfs DRM dir.
    """
    try:
        cards = list(drm_root.glob("card[0-9]*"))
    except OSError:
        return False
    for card in cards:
        vendor_file = card / "device" / "vendor"
        try:
            vendor = vendor_file.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if vendor == "0x1002":
            return True
    return False


class GpuMetricsStep(InstallStep):
    """Install the AMD GPU textfile exporter + 15s systemd timer on the host.

    node-exporter (observability profile) mounts ``/var/lib/node_exporter/textfile`` :ro and
    serves ``amdgpu.prom`` from it, but nothing on the supported Python install path kept that
    file fresh — the systemd timer that runs ``scripts/ops/amdgpu_textfile.sh`` lived only in
    ``ansible/roles/observability``, which ``agmind install`` never executes (it runs ansible with
    ``--tags bootstrap`` only). Result: ``amdgpu_*`` metrics had 0 series and 7/8 Grafana GPU
    panels were blank (audit grafana-dashboards CRITICAL-2 / installer-gaps INST-1).

    This step closes the gap on the REAL install path. It is a no-op (success) unless BOTH:
      * ``node-exporter`` is in the selected services (observability profile selected), AND
      * the host has an AMD GPU (``/sys/class/drm/card*/device/vendor`` == ``0x1002``)
    so it never runs on the CPU-only worker node. Never fails the install — the stack is already
    deployed; a missing GPU timer is degraded observability, not a deploy blocker.
    """

    step_id = "gpu_metrics"
    label = "Install AMD GPU metrics timer"

    SCRIPT_RELATIVE = "scripts/ops/amdgpu_textfile.sh"

    def run(self, callback: ProgressCallback, config: InstallConfig) -> InstallStepResult:
        start = time.monotonic()

        def _ok(message: str) -> InstallStepResult:
            return InstallStepResult(
                step_id=self.step_id,
                success=True,
                message=message,
                elapsed=timedelta(seconds=time.monotonic() - start),
            )

        if "node-exporter" not in config.services:
            return _ok("skipped (node-exporter / observability not selected)")
        if not _steps._host_has_amd_gpu():
            return _ok("skipped (no AMD GPU on this host)")
        if config.sudo_password is None:
            return _ok("skipped (no sudo password — cannot install systemd timer)")

        source_script = _steps.DEFAULT_REPO_ROOT / self.SCRIPT_RELATIVE
        if not source_script.is_file():
            return _ok(f"skipped (exporter script not found: {source_script})")

        dest_script = config.install_dir / "scripts" / "ops" / "amdgpu_textfile.sh"
        scripts_ops_dir = dest_script.parent

        try:
            # 1. Pre-create the node-exporter textfile dir (the :ro mount source) and the
            #    install scripts/ops dir, then ship the exporter script. All via sudo: the
            #    install dir + /var/lib are root-owned by the runtime-payload step.
            _steps._run_sudo_runtime_command(
                config,
                ["install", "-d", "-m", "0755", _GPU_METRICS_TEXTFILE_DIR],
                callback,
                self.step_id,
            )
            _steps._run_sudo_runtime_command(
                config,
                ["install", "-d", "-m", "0755", str(scripts_ops_dir)],
                callback,
                self.step_id,
            )
            _steps._run_sudo_runtime_command(
                config,
                ["install", "-m", "0755", str(source_script), str(dest_script)],
                callback,
                self.step_id,
            )

            # 2. Write the systemd service + timer unit files (root:root 0644).
            self._sudo_write_unit(
                config,
                callback,
                _GPU_METRICS_SERVICE_PATH,
                _gpu_metrics_service_unit(str(dest_script)),
            )
            self._sudo_write_unit(
                config,
                callback,
                _GPU_METRICS_TIMER_PATH,
                _GPU_METRICS_TIMER_UNIT,
            )

            # 3. Reload systemd and enable+start the timer now (also runs the service once).
            _steps._run_sudo_runtime_command(
                config, ["systemctl", "daemon-reload"], callback, self.step_id
            )
            _steps._run_sudo_runtime_command(
                config,
                ["systemctl", "enable", "--now", "amdgpu-metrics.timer"],
                callback,
                self.step_id,
            )
        except (OSError, PermissionError) as exc:
            # Degraded observability, not a deploy blocker — never roll back the stack.
            return _ok(f"GPU metrics timer skipped ({exc})")

        return _ok("amdgpu-metrics.timer enabled (GPU dashboard live)")

    def _sudo_write_unit(
        self,
        config: InstallConfig,
        callback: ProgressCallback,
        dest: str,
        content: str,
    ) -> None:
        """Stage a unit body to a user-writable temp, then place it root:root 0644 via sudo."""
        fd, tmp = tempfile.mkstemp(prefix=".agmind-amdgpu-unit-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(content)
            os.chmod(tmp, 0o644)
            _steps._run_sudo_runtime_command(
                config,
                ["install", "-m", "0644", "-o", "root", "-g", "root", tmp, dest],
                callback,
                self.step_id,
            )
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass
