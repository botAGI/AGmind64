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


def _fake_deploy_state(**overrides):
    from agmind.deploy.state import DeployState

    kwargs = {
        "agmind_version": "0.0.0-test",
        "profiles": ["core"],
        "requested_services": ["traefik", "postgres"],
        "resolved_services": ["traefik", "postgres"],
        "domain": "lab.example.com",
        "edge_mode": "local",
    }
    kwargs.update(overrides)
    return DeployState.new(**kwargs)


def test_status_deploy_json_reports_missing_service(monkeypatch, tmp_path) -> None:
    """D-07: a resolved_services entry absent from the live compose snapshot is `missing`."""
    from agmind.cli import _make_app, core_cmd
    from agmind.cli.tui import status_dashboard as sd

    snap = sd.ComposeStateSnapshot(
        services=(
            sd.ServiceState("traefik", "running", "healthy", "Up", "img", "agmind-traefik-1"),
        ),
        error=None,
        compose_file=tmp_path / "docker-compose.yml",
    )
    monkeypatch.setattr(sd, "query_compose_state", lambda _d: snap)
    monkeypatch.setattr(
        core_cmd,
        "load_deploy_state",
        lambda _d: _fake_deploy_state(resolved_services=["traefik", "postgres"]),
    )

    result = CliRunner().invoke(
        _make_app(), ["status", "--deploy", "--json", "--install-dir", str(tmp_path)]
    )
    assert result.exit_code == 0, result.output
    dep = json.loads(result.output[result.output.index("{") :])["deploy"]
    assert dep["missing"] == ["postgres"]
    assert dep["extra"] == []


def test_status_deploy_json_reports_extra_service(monkeypatch, tmp_path) -> None:
    """D-07: a live service not in resolved_services is `extra`."""
    from agmind.cli import _make_app, core_cmd
    from agmind.cli.tui import status_dashboard as sd

    snap = sd.ComposeStateSnapshot(
        services=(
            sd.ServiceState("traefik", "running", "healthy", "Up", "img", "agmind-traefik-1"),
            sd.ServiceState("stray-svc", "running", "healthy", "Up", "img", "agmind-stray-svc-1"),
        ),
        error=None,
        compose_file=tmp_path / "docker-compose.yml",
    )
    monkeypatch.setattr(sd, "query_compose_state", lambda _d: snap)
    monkeypatch.setattr(
        core_cmd,
        "load_deploy_state",
        lambda _d: _fake_deploy_state(resolved_services=["traefik"]),
    )

    result = CliRunner().invoke(
        _make_app(), ["status", "--deploy", "--json", "--install-dir", str(tmp_path)]
    )
    assert result.exit_code == 0, result.output
    dep = json.loads(result.output[result.output.index("{") :])["deploy"]
    assert dep["missing"] == []
    assert dep["extra"] == ["stray-svc"]


def test_status_deploy_json_includes_desired_when_state_exists(monkeypatch, tmp_path) -> None:
    """D-07: `desired` carries profiles/resolved_services/domain from deploy-state.json."""
    from agmind.cli import _make_app, core_cmd
    from agmind.cli.tui import status_dashboard as sd

    snap = sd.ComposeStateSnapshot(
        services=(
            sd.ServiceState("traefik", "running", "healthy", "Up", "img", "agmind-traefik-1"),
        ),
        error=None,
        compose_file=tmp_path / "docker-compose.yml",
    )
    monkeypatch.setattr(sd, "query_compose_state", lambda _d: snap)
    monkeypatch.setattr(
        core_cmd,
        "load_deploy_state",
        lambda _d: _fake_deploy_state(
            profiles=["core", "rag"], resolved_services=["traefik"], domain="lab.example.com"
        ),
    )

    result = CliRunner().invoke(
        _make_app(), ["status", "--deploy", "--json", "--install-dir", str(tmp_path)]
    )
    assert result.exit_code == 0, result.output
    dep = json.loads(result.output[result.output.index("{") :])["deploy"]
    assert dep["desired"] == {
        "profiles": ["core", "rag"],
        "resolved_services": ["traefik"],
        "domain": "lab.example.com",
    }


def test_status_deploy_json_no_state_degrades_gracefully(monkeypatch, tmp_path) -> None:
    """D-07: no deploy-state.json → desired is null, missing/extra empty, exit 0, no crash."""
    from agmind.cli import _make_app, core_cmd
    from agmind.cli.tui import status_dashboard as sd

    snap = sd.ComposeStateSnapshot(
        services=(
            sd.ServiceState("traefik", "running", "healthy", "Up", "img", "agmind-traefik-1"),
        ),
        error=None,
        compose_file=tmp_path / "docker-compose.yml",
    )
    monkeypatch.setattr(sd, "query_compose_state", lambda _d: snap)
    monkeypatch.setattr(core_cmd, "load_deploy_state", lambda _d: None)

    result = CliRunner().invoke(
        _make_app(), ["status", "--deploy", "--json", "--install-dir", str(tmp_path)]
    )
    assert result.exit_code == 0, result.output
    dep = json.loads(result.output[result.output.index("{") :])["deploy"]
    assert dep["desired"] is None
    assert dep["missing"] == []
    assert dep["extra"] == []


def _render_to_text(renderable) -> str:
    from rich.console import Console

    console = Console(width=200, record=True)
    console.print(renderable)
    return console.export_text()


def test_status_deploy_plain_shows_drift_summary(monkeypatch, tmp_path) -> None:
    """D-07: plain (non-JSON) `status --deploy` names missing/extra services + domain."""
    from agmind.cli import _make_app, core_cmd
    from agmind.cli.tui import status_dashboard as sd

    snap = sd.ComposeStateSnapshot(
        services=(
            sd.ServiceState("traefik", "running", "healthy", "Up", "img", "agmind-traefik-1"),
            sd.ServiceState("stray-svc", "running", "healthy", "Up", "img", "agmind-stray-svc-1"),
        ),
        error=None,
        compose_file=tmp_path / "docker-compose.yml",
    )
    monkeypatch.setattr(sd, "query_compose_state", lambda _d: snap)
    monkeypatch.setattr(
        core_cmd,
        "load_deploy_state",
        lambda _d: _fake_deploy_state(
            resolved_services=["traefik", "postgres"], domain="lab.example.com"
        ),
    )

    result = CliRunner().invoke(_make_app(), ["status", "--deploy", "--install-dir", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "lab.example.com" in result.output
    assert "postgres" in result.output
    assert "stray-svc" in result.output


def test_status_deploy_plain_no_state_no_drift_noise(monkeypatch, tmp_path) -> None:
    """D-07: no deploy-state → plain output has no desired/drift noise, no crash, exit 0."""
    from agmind.cli import _make_app, core_cmd
    from agmind.cli.tui import status_dashboard as sd

    snap = sd.ComposeStateSnapshot(
        services=(
            sd.ServiceState("traefik", "running", "healthy", "Up", "img", "agmind-traefik-1"),
        ),
        error=None,
        compose_file=tmp_path / "docker-compose.yml",
    )
    monkeypatch.setattr(sd, "query_compose_state", lambda _d: snap)
    monkeypatch.setattr(core_cmd, "load_deploy_state", lambda _d: None)

    result = CliRunner().invoke(_make_app(), ["status", "--deploy", "--install-dir", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert "desired" not in result.output.lower()
    assert "missing" not in result.output.lower()


def test_status_deploy_rich_table_adds_drift_rows(monkeypatch, tmp_path) -> None:
    """D-07: rich-table renderer folds missing/extra/domain rows into the existing deploy table
    without breaking the existing live rows."""
    from agmind.cli import core_cmd
    from agmind.cli.tui import status_dashboard as sd

    snap = sd.ComposeStateSnapshot(
        services=(
            sd.ServiceState("traefik", "running", "healthy", "Up", "img", "agmind-traefik-1"),
            sd.ServiceState("stray-svc", "running", "healthy", "Up", "img", "agmind-stray-svc-1"),
        ),
        error=None,
        compose_file=tmp_path / "docker-compose.yml",
    )
    monkeypatch.setattr(sd, "query_compose_state", lambda _d: snap)
    monkeypatch.setattr(
        core_cmd,
        "load_deploy_state",
        lambda _d: _fake_deploy_state(
            resolved_services=["traefik", "postgres"], domain="lab.example.com"
        ),
    )

    payload = core_cmd._build_status_payload(True, tmp_path)
    renderable = core_cmd._render_status_table(payload, True)
    text = _render_to_text(renderable)

    # existing live rows preserved
    assert "running" in text
    assert "healthy" in text
    # new drift rows present
    assert "lab.example.com" in text
    assert "postgres" in text
    assert "stray-svc" in text


def test_status_deploy_rich_table_no_state_no_drift_rows(monkeypatch, tmp_path) -> None:
    """D-07: rich-table renderer emits no desired/drift rows and does not crash without state."""
    from agmind.cli import core_cmd
    from agmind.cli.tui import status_dashboard as sd

    snap = sd.ComposeStateSnapshot(
        services=(
            sd.ServiceState("traefik", "running", "healthy", "Up", "img", "agmind-traefik-1"),
        ),
        error=None,
        compose_file=tmp_path / "docker-compose.yml",
    )
    monkeypatch.setattr(sd, "query_compose_state", lambda _d: snap)
    monkeypatch.setattr(core_cmd, "load_deploy_state", lambda _d: None)

    payload = core_cmd._build_status_payload(True, tmp_path)
    renderable = core_cmd._render_status_table(payload, True)
    text = _render_to_text(renderable)

    assert "desired" not in text.lower()
    assert "domain" not in text.lower()
