"""Ordered-boot systemd unit install step.

Split out of the historical single-file ``agmind/install/steps.py``; every name
here is re-exported from the package ``__init__``.
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
from datetime import timedelta

from agmind.install.orchestrator import (
    InstallConfig,
    InstallStep,
    InstallStepResult,
    ProgressCallback,
)

# `_run_sudo_runtime_command` is monkeypatched on the PACKAGE object by
# tests/install/test_boot_unit_step.py; resolve it through the package at call time so the
# patch still reaches this step after the package split (see configs.py).
_steps = sys.modules["agmind.install.steps"]

_AGMIND_STACK_SERVICE_PATH = "/etc/systemd/system/agmind-stack.service"


def _selected_compose_profiles(services: list[str]) -> list[str]:
    """Compose ``--profile`` set that activates exactly the deployed services.

    The renderer tags every service in docker-compose.yml with its ``profiles:``, so a bare
    ``docker compose up -d`` (no --profile / no named services) starts ZERO of them. The boot
    unit must pass these flags. The rendered compose contains ONLY the selected services, so
    activating the union of their profiles activates exactly the deployed set — never more.
    """
    from agmind.services.renderer import load_descriptors, select_services

    selected = select_services(load_descriptors(), services=list(services))
    return sorted({profile for d in selected.values() for profile in d.profiles})


def _agmind_stack_unit(install_dir: str, profiles: list[str]) -> str:
    """systemd oneshot that re-runs ``compose up -d`` with the deployed --profile set on boot.

    Containers carry restart=unless-stopped, but that does NOT order them after a host reboot
    (milvus needs etcd+minio first, dify needs postgres+redis), and an unclean power-loss can
    leave a stateful container Exited(255) that the policy never re-fires (live 2026-06-13:
    prometheus stayed down, Grafana lost every datasource). This oneshot brings the whole selected
    stack back, depends_on-ordered, once docker is up. Mirrors
    ``ansible/roles/services/templates/agmind-stack.service.j2`` — keep the two in sync.
    """
    compose = f"{install_dir}/docker-compose.yml"
    profile_flags = "".join(f"--profile {profile} " for profile in profiles)
    return (
        "# Managed by AGmind (install BootUnitStep) — do not edit by hand.\n"
        "# Ordered boot bring-up of the AGmind stack. A bare `compose up -d` skips every profiled\n"
        "# service, so the deployed --profile set is baked in. live 2026-06-13 reboot-survivability.\n"
        "[Unit]\n"
        "Description=AGmind stack (docker compose) — ordered boot bring-up\n"
        f"Documentation=file://{compose}\n"
        "Requires=docker.service\n"
        "After=docker.service network-online.target\n"
        "Wants=network-online.target\n"
        "\n"
        "[Service]\n"
        "Type=oneshot\n"
        "RemainAfterExit=yes\n"
        f"WorkingDirectory={install_dir}\n"
        f"ExecStart=/usr/bin/docker compose -f {compose} {profile_flags}up -d --remove-orphans\n"
        f"ExecStop=/usr/bin/docker compose -f {compose} {profile_flags}stop\n"
        "# compose itself waits on container health; give it room and never kill mid-bring-up.\n"
        "TimeoutStartSec=0\n"
        "\n"
        "[Install]\n"
        "WantedBy=multi-user.target\n"
    )


class BootUnitStep(InstallStep):
    """Install + enable the agmind-stack.service ordered-boot unit on the REAL install path.

    The unit lived ONLY in the ansible ``services`` role, which ``agmind install`` never runs (it
    invokes ansible ``--tags bootstrap`` only — the same gap class as GpuMetricsStep). So no install
    survived a reboot ordered: after an unclean power-loss a stateful container (e.g. prometheus)
    could come back Exited(255) with the unless-stopped policy never re-firing, leaving Grafana with
    no datasource (live 2026-06-13). This step closes the gap. It ``enable``\\ s (NOT --now) the unit
    so the stack is armed for the NEXT boot without restarting the just-deployed one. Never fails the
    install — a missing boot unit is degraded reboot-survivability, not a deploy blocker.
    """

    step_id = "boot_unit"
    label = "Arm stack boot unit"

    def run(self, callback: ProgressCallback, config: InstallConfig) -> InstallStepResult:
        start = time.monotonic()

        def _ok(message: str) -> InstallStepResult:
            return InstallStepResult(
                step_id=self.step_id,
                success=True,
                message=message,
                elapsed=timedelta(seconds=time.monotonic() - start),
            )

        if config.sudo_password is None:
            return _ok("skipped (no sudo password — cannot install systemd boot unit)")
        if not config.services:
            return _ok("skipped (no services selected)")
        if not (config.install_dir / "docker-compose.yml").is_file():
            return _ok("skipped (no rendered docker-compose.yml)")

        profiles = _selected_compose_profiles(config.services)
        if not profiles:
            return _ok("skipped (no profiles resolved for selected services)")

        try:
            self._sudo_write_unit(
                config,
                callback,
                _AGMIND_STACK_SERVICE_PATH,
                _agmind_stack_unit(str(config.install_dir), profiles),
            )
            _steps._run_sudo_runtime_command(
                config, ["systemctl", "daemon-reload"], callback, self.step_id
            )
            # enable (NOT --now): arm for the next boot; DeployStep already brought the stack up.
            _steps._run_sudo_runtime_command(
                config,
                ["systemctl", "enable", "agmind-stack.service"],
                callback,
                self.step_id,
            )
        except (OSError, PermissionError) as exc:
            # Degraded reboot-survivability, not a deploy blocker — never roll back the stack.
            return _ok(f"boot unit skipped ({exc})")

        return _ok("agmind-stack.service enabled (ordered boot bring-up armed)")

    def _sudo_write_unit(
        self,
        config: InstallConfig,
        callback: ProgressCallback,
        dest: str,
        content: str,
    ) -> None:
        """Stage a unit body to a user-writable temp, then place it root:root 0644 via sudo."""
        fd, tmp = tempfile.mkstemp(prefix=".agmind-stack-unit-", suffix=".tmp")
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
