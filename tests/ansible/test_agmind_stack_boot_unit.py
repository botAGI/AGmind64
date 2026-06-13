"""Live-audit 2026-06-05 (MED no-systemd-boot-unit-reboot-ordering): containers carry
restart=unless-stopped but that does NOT order them after a host reboot. The services role
installs+enables an agmind-stack.service oneshot that re-runs `docker compose up -d`
(depends_on-ordered) on boot. Guard the unit's contract + that the role enables it."""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.backend_any

_REPO = Path(__file__).resolve().parents[2]
_UNIT = _REPO / "ansible" / "roles" / "services" / "templates" / "agmind-stack.service.j2"
_TASKS = _REPO / "ansible" / "roles" / "services" / "tasks" / "main.yml"


def test_boot_unit_orders_after_docker_and_runs_compose_up() -> None:
    unit = _UNIT.read_text(encoding="utf-8")
    assert "Requires=docker.service" in unit
    assert "After=docker.service" in unit
    assert "Type=oneshot" in unit
    assert "RemainAfterExit=yes" in unit
    # ordered bring-up: compose up -d (respects depends_on), not a per-container restart
    assert "docker compose" in unit and "up -d" in unit
    assert "WantedBy=multi-user.target" in unit
    # behavioural contract, not just "a line that says up -d": the rendered compose tags every
    # service with `profiles:`, so a bare `up -d` brings up ZERO services. The unit MUST pass the
    # deployed --profile set (live 2026-06-13 reboot: profile-blind unit would have stayed empty).
    assert "--profile" in unit, "boot unit's up -d is profile-blind → would start no profiled svc"
    assert "agmind_profiles" in unit, "unit must render the deployed agmind_profiles selection"


def test_services_role_installs_and_enables_boot_unit() -> None:
    tasks = _TASKS.read_text(encoding="utf-8")
    assert "agmind-stack.service.j2" in tasks  # template shipped
    assert "/etc/systemd/system/agmind-stack.service" in tasks  # to the right path
    assert "enabled: true" in tasks  # enabled for boot
