"""Phase J.2: tests for agmind.cli.tui.status_dashboard."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from agmind.cli.tui.status_dashboard import (
    ComposeStateSnapshot,
    ServiceState,
    StatusDashboardApp,
    query_compose_state,
)

pytestmark = pytest.mark.backend_any


# ---------- query_compose_state: error paths ----------


def test_no_compose_file_returns_error(tmp_path: Path) -> None:
    snap = query_compose_state(tmp_path)
    assert snap.error is not None
    assert "no deployment" in snap.error
    assert snap.services == ()
    assert snap.total == 0


def test_compose_command_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")

    def fake_run(*_args: object, **_kwargs: object) -> object:
        raise FileNotFoundError("docker")

    monkeypatch.setattr(subprocess, "run", fake_run)
    snap = query_compose_state(tmp_path)
    assert snap.error is not None
    assert "docker command not found" in snap.error


def test_compose_command_nonzero_rc(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")

    class FakeProc:
        stdout = ""
        stderr = "boom"
        returncode = 1

    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: FakeProc())
    snap = query_compose_state(tmp_path)
    assert snap.error is not None
    assert "rc=1" in snap.error
    assert "boom" in snap.error


def test_compose_command_timeout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")

    def fake_run(*_args: object, **_kwargs: object) -> Any:
        raise subprocess.TimeoutExpired(cmd="docker", timeout=10.0)

    monkeypatch.setattr(subprocess, "run", fake_run)
    snap = query_compose_state(tmp_path)
    assert snap.error is not None
    assert "timed out" in snap.error


# ---------- query_compose_state: parse happy path ----------


def _mock_run_with_stdout(monkeypatch: pytest.MonkeyPatch, stdout: str) -> None:
    class FakeProc:
        def __init__(self) -> None:
            self.stdout = stdout
            self.stderr = ""
            self.returncode = 0

    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: FakeProc())


def test_parse_compose_ps_jsonl(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    rows = [
        {
            "Service": "traefik",
            "Name": "agmind-traefik-1",
            "Image": "traefik:v3.0",
            "State": "running",
            "Health": "healthy",
            "Status": "Up 2 hours (healthy)",
        },
        {
            "Service": "llama-llm",
            "Name": "agmind-llama-llm-1",
            "Image": "ghcr.io/ggerganov/llama.cpp:server",
            "State": "running",
            "Health": "starting",
            "Status": "Up 1 minute",
        },
        {
            "Service": "qdrant",
            "Name": "agmind-qdrant-1",
            "Image": "qdrant/qdrant:v1.14.0",
            "State": "exited",
            "Health": "",
            "Status": "Exited (0) 3 seconds ago",
        },
    ]
    _mock_run_with_stdout(monkeypatch, "\n".join(json.dumps(r) for r in rows))

    snap = query_compose_state(tmp_path)
    assert snap.error is None
    assert snap.total == 3
    assert snap.running == 2
    assert snap.healthy == 1
    assert snap.unhealthy == 0
    names = [s.service for s in snap.services]
    assert names == sorted(names)  # sorted output
    assert "traefik" in names


def test_parse_handles_invalid_json_lines(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    payload = (
        '{"Service": "ok", "Name": "ok-1", "State": "running", "Health": "healthy"}\n'
        "this is not json\n"
        '{"Service": "ok2", "Name": "ok2-1", "State": "running", "Health": "healthy"}\n'
    )
    _mock_run_with_stdout(monkeypatch, payload)
    snap = query_compose_state(tmp_path)
    assert snap.error is None
    assert snap.total == 2
    assert {s.service for s in snap.services} == {"ok", "ok2"}


def test_parse_handles_empty_lines(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    _mock_run_with_stdout(monkeypatch, "\n\n\n")
    snap = query_compose_state(tmp_path)
    assert snap.error is None
    assert snap.services == ()
    assert snap.total == 0


def test_unhealthy_count(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    rows = [
        {"Service": "a", "Name": "a-1", "State": "running", "Health": "unhealthy"},
        {"Service": "b", "Name": "b-1", "State": "running", "Health": "unhealthy"},
        {"Service": "c", "Name": "c-1", "State": "running", "Health": "healthy"},
    ]
    _mock_run_with_stdout(monkeypatch, "\n".join(json.dumps(r) for r in rows))
    snap = query_compose_state(tmp_path)
    assert snap.unhealthy == 2
    assert snap.healthy == 1


# ---------- StatusDashboardApp: instantiation smoke ----------


def test_dashboard_app_creates_without_running(tmp_path: Path) -> None:
    app = StatusDashboardApp(install_dir=tmp_path, refresh_interval=10.0)
    assert app.install_dir == tmp_path
    assert app.refresh_interval == 10.0
    assert app._snapshot is None  # noqa: SLF001 — accessing internal for smoke


def test_dashboard_default_install_dir() -> None:
    app = StatusDashboardApp()
    assert str(app.install_dir).endswith("agmind")


def test_service_state_immutable() -> None:
    s = ServiceState(
        service="x", state="running", health="healthy", uptime="Up 2h", image="i", name="n"
    )
    with pytest.raises(Exception):
        s.service = "y"  # type: ignore[misc]


# ---- Phase M4.5: filter/sort/pause ----


def test_dashboard_filter_default_all(tmp_path: Path) -> None:
    app = StatusDashboardApp(install_dir=tmp_path)
    assert app._filter_name == "all"
    assert app._sort_name == "name"
    assert app._paused is False


def test_dashboard_action_toggle_pause(tmp_path: Path) -> None:
    app = StatusDashboardApp(install_dir=tmp_path)
    assert app._paused is False
    app.action_toggle_pause()
    assert app._paused is True
    app.action_toggle_pause()
    assert app._paused is False


def test_dashboard_action_cycle_filter(tmp_path: Path) -> None:
    app = StatusDashboardApp(install_dir=tmp_path)
    initial = app._filter_name
    app.action_cycle_filter()
    second = app._filter_name
    assert second != initial
    # Cycle через всех 4 → returns to first
    for _ in range(3):
        app.action_cycle_filter()
    assert app._filter_name == initial


def test_dashboard_action_cycle_sort(tmp_path: Path) -> None:
    app = StatusDashboardApp(install_dir=tmp_path)
    initial = app._sort_name
    app.action_cycle_sort()
    second = app._sort_name
    assert second != initial


def test_dashboard_paused_refresh_state_noop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """refresh_state() пропускает probe если paused."""
    app = StatusDashboardApp(install_dir=tmp_path)
    app._paused = True

    called: list[bool] = []
    monkeypatch.setattr(
        "agmind.cli.tui.status_dashboard.query_compose_state",
        lambda _i: called.append(True) or None,
    )
    app.refresh_state()
    assert called == []


def test_snapshot_computed_props() -> None:
    snap = ComposeStateSnapshot(
        services=(
            ServiceState("a", "running", "healthy", "Up 1h", "img1", "a-1"),
            ServiceState("b", "running", "unhealthy", "Up 2h", "img2", "b-1"),
            ServiceState("c", "exited", "", "Exited", "img3", "c-1"),
        ),
        error=None,
        compose_file=Path("/tmp/none"),
    )
    assert snap.total == 3
    assert snap.running == 2
    assert snap.healthy == 1
    assert snap.unhealthy == 1
