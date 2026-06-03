"""The observability role must install a systemd timer that periodically runs the AMD GPU
textfile exporter (scripts/ops/amdgpu_textfile.sh) so node-exporter's textfile collector always
has a fresh amdgpu.prom to scrape. Without the timer the script never runs on the host and the
GPU panels stay empty even though node-exporter is now pointed at the textfile dir.

amdgpu_textfile.sh itself is correct (atomic mv within the textfile dir) and unchanged; this
gap is purely the missing host-side scheduler + script delivery + dir pre-create.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.backend_any

_REPO = Path(__file__).resolve().parents[2]
_ROLE = _REPO / "ansible" / "roles" / "observability"
_TASKS = _ROLE / "tasks" / "main.yml"
_SERVICE_TPL = _ROLE / "templates" / "amdgpu-metrics.service.j2"
_TIMER_TPL = _ROLE / "templates" / "amdgpu-metrics.timer.j2"
_TEXTFILE_DIR = "/var/lib/node_exporter/textfile"


def test_timer_and_service_templates_exist() -> None:
    assert _SERVICE_TPL.is_file(), _SERVICE_TPL
    assert _TIMER_TPL.is_file(), _TIMER_TPL


def test_timer_runs_on_a_short_cadence() -> None:
    timer = _TIMER_TPL.read_text(encoding="utf-8")
    assert "[Timer]" in timer and "[Install]" in timer
    # GPU metrics need a short cadence (parent + the script header intend ~15s); 15-min would
    # make panels look frozen. Pin a sub-minute OnUnitActiveSec.
    assert "OnUnitActiveSec=15s" in timer


def test_service_is_hardened_oneshot_running_the_shipped_script() -> None:
    service = _SERVICE_TPL.read_text(encoding="utf-8")
    assert "Type=oneshot" in service
    assert "amdgpu_textfile.sh" in service  # ExecStart runs the shipped exporter
    assert "ReadWritePaths=" in service  # sandboxed write target
    assert _TEXTFILE_DIR in service


def test_role_ships_script_precreates_dir_and_enables_timer() -> None:
    text = _TASKS.read_text(encoding="utf-8")
    assert "amdgpu_textfile.sh" in text  # a copy task ships the script to the host
    assert "amdgpu-metrics.timer" in text  # a systemd task enables/starts the timer
    assert _TEXTFILE_DIR in text  # textfile dir pre-created before node-exporter's :ro mount


def test_role_tasks_parse_and_enable_timer_via_systemd() -> None:
    tasks = yaml.safe_load(_TASKS.read_text(encoding="utf-8"))
    assert isinstance(tasks, list)
    systemd_tasks = [
        t
        for t in tasks
        if isinstance(t, dict)
        and any(k in t for k in ("ansible.builtin.systemd", "ansible.builtin.systemd_service"))
    ]
    assert any(
        "amdgpu-metrics.timer" in str(t) for t in systemd_tasks
    ), "no systemd task enabling amdgpu-metrics.timer"
