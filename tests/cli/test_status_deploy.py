"""live-audit 2026-06-07 (UI-4 status-no-deploy-picture): `agmind status --deploy` gives a
non-interactive deployment health summary (was only behind the interactive --tui). Assert
BEHAVIOURALLY via --json (MEMORY: CI wraps rich/typer help/columns — never assert formatted text)."""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

pytestmark = pytest.mark.backend_any


def test_status_deploy_json_reports_running_healthy_problems(monkeypatch, tmp_path) -> None:
    from agmind.cli import _make_app
    from agmind.cli.tui import status_dashboard as sd

    snap = sd.ComposeStateSnapshot(
        services=(
            sd.ServiceState("traefik", "running", "healthy", "Up", "img", "agmind-traefik-1"),
            sd.ServiceState("postgres", "running", "unhealthy", "Up", "img", "agmind-postgres-1"),
            sd.ServiceState("dify-api", "exited", "", "Exited (1)", "img", "agmind-dify-api-1"),
        ),
        error=None,
        compose_file=tmp_path / "docker-compose.yml",
    )
    monkeypatch.setattr(sd, "query_compose_state", lambda _d: snap)

    result = CliRunner().invoke(
        _make_app(), ["status", "--deploy", "--json", "--install-dir", str(tmp_path)]
    )
    assert result.exit_code == 0, result.output
    dep = json.loads(result.output[result.output.index("{") :])["deploy"]
    assert dep["total"] == 3 and dep["running"] == 2
    assert dep["healthy"] == 1 and dep["unhealthy"] == 1
    assert "postgres" in dep["problems"] and "dify-api" in dep["problems"]


def test_status_deploy_surfaces_compose_error(monkeypatch, tmp_path) -> None:
    from agmind.cli import _make_app
    from agmind.cli.tui import status_dashboard as sd

    snap = sd.ComposeStateSnapshot(
        services=(), error="no deployment found", compose_file=tmp_path / "x"
    )
    monkeypatch.setattr(sd, "query_compose_state", lambda _d: snap)
    result = CliRunner().invoke(
        _make_app(), ["status", "--deploy", "--json", "--install-dir", str(tmp_path)]
    )
    assert result.exit_code == 0, result.output
    assert (
        json.loads(result.output[result.output.index("{") :])["deploy"]["error"]
        == "no deployment found"
    )
