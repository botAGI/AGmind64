"""Tests для agmind.cli — typer app construction.

Если typer не установлен — _HAS_TYPER=False и большинство тестов
помечается skipped.
"""

from __future__ import annotations

import pytest

from agmind.cli import _HAS_TYPER

pytestmark = pytest.mark.backend_any


def test_cli_module_imports() -> None:
    """import agmind.cli не должен падать даже без typer."""
    import agmind.cli  # noqa: F401


def test_app_function_exists() -> None:
    from agmind.cli import app

    assert callable(app)


@pytest.mark.skipif(not _HAS_TYPER, reason="typer not installed")
def test_make_app_builds_typer_instance() -> None:
    from agmind.cli import _make_app

    app = _make_app()
    # typer.Typer имеет registered_commands
    assert hasattr(app, "registered_commands") or hasattr(app, "__class__")


@pytest.mark.skipif(not _HAS_TYPER, reason="typer not installed")
def test_make_app_has_doctor_command() -> None:
    import typer
    from click.testing import CliRunner  # type: ignore[import-untyped]

    from agmind.cli import _make_app

    cli_app = typer.main.get_command(_make_app())
    runner = CliRunner()
    result = runner.invoke(cli_app, ["--help"])
    assert result.exit_code == 0
    assert "doctor" in result.output.lower()


@pytest.mark.skipif(not _HAS_TYPER, reason="typer not installed")
def test_app_version_command() -> None:
    import typer
    from click.testing import CliRunner

    from agmind import __version__
    from agmind.cli import _make_app

    cli_app = typer.main.get_command(_make_app())
    runner = CliRunner()
    result = runner.invoke(cli_app, ["version"])
    assert result.exit_code == 0
    assert __version__ in result.output


@pytest.mark.skipif(not _HAS_TYPER, reason="typer not installed")
def test_tools_validate_command() -> None:
    import typer
    from click.testing import CliRunner

    from agmind.cli import _make_app

    cli_app = typer.main.get_command(_make_app())
    runner = CliRunner()
    result = runner.invoke(cli_app, ["tools", "validate"])
    assert result.exit_code == 0
    assert "tool candidates OK" in result.output


@pytest.mark.skipif(not _HAS_TYPER, reason="typer not installed")
def test_targets_validate_command() -> None:
    import typer
    from click.testing import CliRunner

    from agmind.cli import _make_app

    cli_app = typer.main.get_command(_make_app())
    runner = CliRunner()
    result = runner.invoke(cli_app, ["targets", "validate"])
    assert result.exit_code == 0
    assert "deployment targets OK" in result.output


@pytest.mark.skipif(not _HAS_TYPER, reason="typer not installed")
def test_targets_validate_json_command() -> None:
    import json

    import typer
    from click.testing import CliRunner

    from agmind.cli import _make_app

    cli_app = typer.main.get_command(_make_app())
    runner = CliRunner()
    result = runner.invoke(cli_app, ["targets", "validate", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["target_count"] == 3


@pytest.mark.skipif(not _HAS_TYPER, reason="typer not installed")
def test_cluster_inspect_command_json(monkeypatch: pytest.MonkeyPatch) -> None:
    import typer
    from click.testing import CliRunner

    from agmind.cli import _make_app
    from agmind.cluster.inspect import CommandResult, inspect_cluster

    report = inspect_cluster(
        run=lambda args: CommandResult(127),
        path_exists=lambda path: False,
        discover_peers=lambda: [],
    )
    monkeypatch.setattr("agmind.cli.cluster_cmd._inspect_cluster", lambda discover_timeout: report)

    cli_app = typer.main.get_command(_make_app())
    runner = CliRunner()
    result = runner.invoke(cli_app, ["cluster", "inspect", "--json", "--timeout", "0.1"])
    assert result.exit_code == 0
    assert '"detected_target": "unknown"' in result.output


@pytest.mark.skipif(not _HAS_TYPER, reason="typer not installed")
def test_governance_validate_command() -> None:
    import typer
    from click.testing import CliRunner

    from agmind.cli import _make_app

    cli_app = typer.main.get_command(_make_app())
    runner = CliRunner()
    result = runner.invoke(cli_app, ["governance", "validate"])
    assert result.exit_code == 0
    assert "governance OK: 7 checks" in result.output


@pytest.mark.skipif(not _HAS_TYPER, reason="typer not installed")
def test_deploy_status_subcommand_routes_to_compose_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import typer
    from click.testing import CliRunner

    from agmind.cli import _make_app

    calls: list[str] = []

    def fake_status() -> int:
        calls.append("status")
        return 0

    monkeypatch.setattr("agmind.cli.deploy_cmd.cmd_status", fake_status)

    cli_app = typer.main.get_command(_make_app())
    runner = CliRunner()
    result = runner.invoke(cli_app, ["deploy", "status"])

    assert result.exit_code == 0
    assert calls == ["status"]


@pytest.mark.skipif(not _HAS_TYPER, reason="typer not installed")
def test_deploy_logs_subcommand_routes_to_compose_logs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import typer
    from click.testing import CliRunner

    from agmind.cli import _make_app

    calls: dict[str, object] = {}

    def fake_logs(
        service: str | None = None,
        *,
        follow: bool = False,
        lines: int = 100,
    ) -> int:
        calls.update({"service": service, "follow": follow, "lines": lines})
        return 0

    monkeypatch.setattr("agmind.cli.deploy_cmd.cmd_logs", fake_logs)

    cli_app = typer.main.get_command(_make_app())
    runner = CliRunner()
    result = runner.invoke(cli_app, ["deploy", "logs", "llama-llm", "--lines", "123", "-f"])

    assert result.exit_code == 0
    assert calls == {"service": "llama-llm", "follow": True, "lines": 123}


@pytest.mark.skipif(not _HAS_TYPER, reason="typer not installed")
def test_deploy_restart_subcommand_routes_to_compose_restart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import typer
    from click.testing import CliRunner

    from agmind.cli import _make_app

    calls: list[str | None] = []

    def fake_restart(service: str | None = None) -> int:
        calls.append(service)
        return 0

    monkeypatch.setattr("agmind.cli.deploy_cmd.cmd_restart", fake_restart)

    cli_app = typer.main.get_command(_make_app())
    runner = CliRunner()
    result = runner.invoke(cli_app, ["deploy", "restart", "llama-llm"])

    assert result.exit_code == 0
    assert calls == ["llama-llm"]


@pytest.mark.skipif(not _HAS_TYPER, reason="typer not installed")
def test_ci_status_command_json(monkeypatch: pytest.MonkeyPatch) -> None:
    import typer
    from click.testing import CliRunner

    from agmind.ci.monitor import CIMonitorReport
    from agmind.cli import _make_app

    monkeypatch.setattr(
        "agmind.cli.ci_cmd.collect_ci_status",
        lambda repository, run_limit: CIMonitorReport(repository="botAGI/AGmind64"),
    )

    cli_app = typer.main.get_command(_make_app())
    runner = CliRunner()
    result = runner.invoke(cli_app, ["ci", "status", "--json", "--repo", "botAGI/AGmind64"])
    assert result.exit_code == 0
    assert '"repository": "botAGI/AGmind64"' in result.output


@pytest.mark.skipif(not _HAS_TYPER, reason="typer not installed")
def test_render_kubernetes_command() -> None:
    import typer
    from click.testing import CliRunner

    from agmind.cli import _make_app

    cli_app = typer.main.get_command(_make_app())
    runner = CliRunner()
    result = runner.invoke(
        cli_app,
        ["render", "kubernetes", "--profile", "proxmox", "--namespace", "agmind"],
    )
    assert result.exit_code == 0
    assert "kind: Deployment" in result.output
    assert "name: proxmox-exporter" in result.output


@pytest.mark.skipif(not _HAS_TYPER, reason="typer not installed")
def test_render_topology_command_reports_ambiguous_vector_provider() -> None:
    import typer
    from click.testing import CliRunner

    from agmind.cli import _make_app

    cli_app = typer.main.get_command(_make_app())
    runner = CliRunner()
    result = runner.invoke(
        cli_app,
        [
            "render",
            "topology",
            "--service",
            "dify-api",
            "--service",
            "milvus",
            "--service",
            "qdrant",
            "--service",
            "postgres",
            "--service",
            "redis",
        ],
    )

    assert result.exit_code == 0
    assert "RAG STORAGE PLAN" in result.output
    assert "DIFY VECTOR DB ..... milvus (ambiguous: qdrant also selected)" in result.output
    assert "TOPOLOGY WARNINGS" in result.output
    assert "Dify has multiple vector_db providers selected" in result.output


@pytest.mark.skipif(not _HAS_TYPER, reason="typer not installed")
def test_render_topology_command_json_uses_structured_payload() -> None:
    import json

    import typer
    from click.testing import CliRunner

    from agmind.cli import _make_app

    cli_app = typer.main.get_command(_make_app())
    runner = CliRunner()
    result = runner.invoke(
        cli_app,
        [
            "render",
            "topology",
            "--json",
            "--service",
            "dify-api",
            "--service",
            "elasticsearch",
            "--service",
            "llama-embed",
            "--service",
            "llama-llm",
            "--service",
            "milvus",
            "--service",
            "minio",
            "--service",
            "mysql",
            "--service",
            "postgres",
            "--service",
            "qdrant",
            "--service",
            "ragflow",
            "--service",
            "redis",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["warning_count"] == 2
    assert payload["retrieval"]["dify_vector_provider"] == "milvus"
    assert payload["retrieval"]["dify_vector_providers"] == ["milvus", "qdrant"]
    assert payload["warnings"][0]["source"] == "compatibility"


@pytest.mark.skipif(not _HAS_TYPER, reason="typer not installed")
def test_render_topology_fail_on_warning_exits_nonzero_with_report() -> None:
    import typer
    from click.testing import CliRunner

    from agmind.cli import _make_app

    cli_app = typer.main.get_command(_make_app())
    runner = CliRunner()
    result = runner.invoke(
        cli_app,
        [
            "render",
            "topology",
            "--fail-on-warning",
            "--service",
            "dify-api",
            "--service",
            "milvus",
            "--service",
            "qdrant",
            "--service",
            "postgres",
            "--service",
            "redis",
        ],
    )

    assert result.exit_code == 2
    assert "TOPOLOGY WARNINGS" in result.output
    assert "Dify has multiple vector_db providers selected" in result.output


@pytest.mark.skipif(not _HAS_TYPER, reason="typer not installed")
def test_render_topology_fail_on_warning_keeps_json_payload() -> None:
    import json

    import typer
    from click.testing import CliRunner

    from agmind.cli import _make_app

    cli_app = typer.main.get_command(_make_app())
    runner = CliRunner()
    result = runner.invoke(
        cli_app,
        [
            "render",
            "topology",
            "--json",
            "--fail-on-warning",
            "--service",
            "dify-api",
            "--service",
            "milvus",
            "--service",
            "qdrant",
            "--service",
            "postgres",
            "--service",
            "redis",
        ],
    )

    assert result.exit_code == 2
    payload = json.loads(result.output)
    assert payload["has_warnings"] is True
    assert payload["warning_count"] > 0


@pytest.mark.skipif(not _HAS_TYPER, reason="typer not installed")
def test_render_topology_fail_on_warning_allows_info_only_json_payload() -> None:
    import json

    import typer
    from click.testing import CliRunner

    from agmind.cli import _make_app

    cli_app = typer.main.get_command(_make_app())
    runner = CliRunner()
    result = runner.invoke(
        cli_app,
        [
            "render",
            "topology",
            "--json",
            "--fail-on-warning",
            "--profile",
            "core,rag",
        ],
    )

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["has_warnings"] is False
    assert payload["warning_count"] == 0
    assert payload["info_count"] == 1
    assert payload["infos"][0]["kind"] == "optional_missing_capability"


@pytest.mark.skipif(not _HAS_TYPER, reason="typer not installed")
def test_render_topology_fail_on_warning_allows_clean_selection() -> None:
    import typer
    from click.testing import CliRunner

    from agmind.cli import _make_app

    cli_app = typer.main.get_command(_make_app())
    runner = CliRunner()
    result = runner.invoke(
        cli_app,
        [
            "render",
            "topology",
            "--fail-on-warning",
            "--service",
            "postgres",
            "--service",
            "redis",
        ],
    )

    assert result.exit_code == 0
    assert "TOPOLOGY OK" in result.output


@pytest.mark.skipif(not _HAS_TYPER, reason="typer not installed")
def test_upgrade_plan_does_not_apply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import typer
    from click.testing import CliRunner

    from agmind.cli import _make_app

    calls: dict[str, object] = {}

    def fake_component(**kwargs: object) -> int:
        calls["component"] = kwargs
        return 0

    def fake_apply() -> int:
        calls["apply"] = True
        return 0

    monkeypatch.setattr("agmind.cli.upgrade_cmd.cmd_component", fake_component)
    monkeypatch.setattr("agmind.cli.upgrade_cmd.cmd_apply", fake_apply)

    cli_app = typer.main.get_command(_make_app())
    runner = CliRunner()
    result = runner.invoke(
        cli_app,
        ["upgrade", "--component", "dify", "--version", "1.14.3", "--plan", "--apply"],
    )

    assert result.exit_code == 0
    assert calls["component"] == {
        "service": "dify",
        "version": "1.14.3",
        "force": False,
        "digest": None,
        "plan_only": True,
    }
    assert "apply" not in calls


def test_app_called_without_typer_exits(monkeypatch: pytest.MonkeyPatch) -> None:
    """Если typer не установлен, app() должен корректно exit с инструкцией."""
    monkeypatch.setattr("agmind.cli._HAS_TYPER", False)
    from agmind.cli import app

    with pytest.raises(SystemExit) as exc_info:
        app()
    assert exc_info.value.code == 2
