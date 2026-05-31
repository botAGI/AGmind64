"""Tests для agmind.cli — typer app construction.

Если typer не установлен — _HAS_TYPER=False и большинство тестов
помечается skipped.
"""

from __future__ import annotations

import json

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
    from typer.testing import CliRunner

    from agmind.cli import _make_app

    cli_app = _make_app()
    runner = CliRunner()
    result = runner.invoke(cli_app, ["--help"])
    assert result.exit_code == 0
    assert "doctor" in result.output.lower()


@pytest.mark.skipif(not _HAS_TYPER, reason="typer not installed")
def test_doctor_runs_preflight_exactly_once(monkeypatch: pytest.MonkeyPatch) -> None:
    from typer.testing import CliRunner

    from agmind.cli import _make_app
    from agmind.diagnostics.doctor import CheckResult, DoctorReport

    calls = {"n": 0}

    def fake_run_preflight() -> DoctorReport:
        calls["n"] += 1
        return DoctorReport(checks=[CheckResult(name="probe", status="ok", message="m")])

    monkeypatch.setattr("agmind.diagnostics.doctor.run_preflight", fake_run_preflight)

    cli_app = _make_app()
    result = CliRunner().invoke(cli_app, ["doctor"])

    assert calls["n"] == 1
    assert result.exit_code == 0


@pytest.mark.skipif(not _HAS_TYPER, reason="typer not installed")
@pytest.mark.parametrize(
    ("statuses", "expected_code"),
    [
        (["ok", "skip"], 0),
        (["ok", "warn"], 1),
        (["ok", "warn", "fail"], 2),
    ],
)
def test_doctor_exit_codes(
    monkeypatch: pytest.MonkeyPatch,
    statuses: list[str],
    expected_code: int,
) -> None:
    from typer.testing import CliRunner

    from agmind.cli import _make_app
    from agmind.diagnostics.doctor import CheckResult, DoctorReport

    def fake_run_preflight() -> DoctorReport:
        return DoctorReport(checks=[CheckResult(name=s, status=s, message="m") for s in statuses])

    monkeypatch.setattr("agmind.diagnostics.doctor.run_preflight", fake_run_preflight)

    cli_app = _make_app()
    result = CliRunner().invoke(cli_app, ["doctor"])

    assert result.exit_code == expected_code


@pytest.mark.skipif(not _HAS_TYPER, reason="typer not installed")
def test_deploy_command_accepts_repeated_explicit_services(
    tmp_path: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from typer.testing import CliRunner

    from agmind.cli import _make_app

    captured: dict[str, object] = {}

    def fake_cmd_deploy(**kwargs: object) -> int:
        captured.update(kwargs)
        return 0

    monkeypatch.setattr("agmind.cli.deploy_cmd.cmd_deploy", fake_cmd_deploy)

    cli_app = _make_app()
    runner = CliRunner()
    result = runner.invoke(
        cli_app,
        [
            "deploy",
            "--profile",
            "stale-profile",
            "--service",
            "traefik",
            "--service",
            "llama-llm",
            "--install-dir",
            str(tmp_path / "install"),  # type: ignore[operator]
        ],
    )

    assert result.exit_code == 0
    assert captured["profiles"] == ["stale-profile"]
    assert captured["services"] == ["traefik", "llama-llm"]


@pytest.mark.skipif(not _HAS_TYPER, reason="typer not installed")
def test_render_compose_accepts_repeated_explicit_services(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from typer.testing import CliRunner

    from agmind.cli import _make_app

    captured: dict[str, object] = {}

    def fake_cmd_render_compose(**kwargs: object) -> int:
        captured.update(kwargs)
        return 0

    monkeypatch.setattr("agmind.cli.render_cmd.cmd_render_compose", fake_cmd_render_compose)

    cli_app = _make_app()
    runner = CliRunner()
    result = runner.invoke(
        cli_app,
        [
            "render",
            "compose",
            "--profile",
            "stale-profile",
            "--service",
            "n8n",
            "--service",
            "dozzle",
            "--domain",
            "lab.example.com",
        ],
    )

    assert result.exit_code == 0
    assert captured["profiles"] == ["stale-profile"]
    assert captured["services"] == ["n8n", "dozzle"]
    assert captured["domain"] == "lab.example.com"


@pytest.mark.skipif(not _HAS_TYPER, reason="typer not installed")
def test_setup_runs_full_install_flow_in_tui_by_default(
    tmp_path: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from typer.testing import CliRunner

    from agmind.cli import _make_app
    from agmind.cli.tui.setup_wizard import SetupState
    from agmind.install.orchestrator import InstallResult

    token_file = tmp_path / "cf-token"  # type: ignore[operator]
    token_file.write_text("super-secret-token", encoding="utf-8")
    token_file.chmod(0o600)
    captured: dict[str, object] = {}

    def fake_run_setup_wizard(**kwargs: object) -> SetupState:
        captured.update(kwargs)
        state = SetupState(domain="lab.example.com", cf_api_token="super-secret-token")
        state.__dict__["_install_result"] = InstallResult(
            success=True,
            steps=(),
            message="install ok",
        )
        return state

    monkeypatch.setattr("agmind.cli.tui.setup_wizard.run_setup_wizard", fake_run_setup_wizard)

    cli_app = _make_app()
    runner = CliRunner()
    result = runner.invoke(
        cli_app,
        [
            "setup",
            "--domain",
            "lab.example.com",
            "--cf-token-file",
            str(token_file),
            "--model-file",
            "model.gguf",
        ],
    )

    assert result.exit_code == 0
    assert "install ok" in result.output
    assert captured["install_mode"] is True
    assert captured["require_sudo_password"] is True


@pytest.mark.skipif(not _HAS_TYPER, reason="typer not installed")
def test_setup_reports_missing_cf_token_file_without_traceback(tmp_path: object) -> None:
    from typer.testing import CliRunner

    from agmind.cli import _make_app

    missing = tmp_path / "missing-cf-token"  # type: ignore[operator]
    cli_app = _make_app()
    runner = CliRunner()

    result = runner.invoke(
        cli_app,
        [
            "setup",
            "--no-tui",
            "--dry-run",
            "--domain",
            "lab.example.com",
            "--cf-token-file",
            str(missing),
        ],
    )

    assert result.exit_code == 2
    assert "cannot read --cf-token-file" in result.output
    assert str(missing) in result.output
    assert "Traceback" not in result.output


@pytest.mark.skipif(not _HAS_TYPER, reason="typer not installed")
def test_setup_rejects_world_readable_cf_token_file(tmp_path: object) -> None:
    from typer.testing import CliRunner

    from agmind.cli import _make_app

    token_file = tmp_path / "cf-token"  # type: ignore[operator]
    token_file.write_text("super-secret-token", encoding="utf-8")
    token_file.chmod(0o644)
    cli_app = _make_app()
    runner = CliRunner()

    result = runner.invoke(
        cli_app,
        [
            "setup",
            "--no-tui",
            "--dry-run",
            "--domain",
            "lab.example.com",
            "--cf-token-file",
            str(token_file),
        ],
    )

    assert result.exit_code == 2
    assert "must be chmod 600" in result.output
    assert str(token_file) in result.output
    assert "super-secret-token" not in result.output
    assert "Traceback" not in result.output


@pytest.mark.skipif(not _HAS_TYPER, reason="typer not installed")
def test_setup_reports_missing_from_state_without_traceback(tmp_path: object) -> None:
    from typer.testing import CliRunner

    from agmind.cli import _make_app

    missing = tmp_path / "missing-state.json"  # type: ignore[operator]
    cli_app = _make_app()
    runner = CliRunner()

    result = runner.invoke(
        cli_app,
        [
            "setup",
            "--no-tui",
            "--dry-run",
            "--from-state",
            str(missing),
            "--domain",
            "lab.example.com",
        ],
    )

    assert result.exit_code == 2
    assert "cannot read --from-state" in result.output
    assert str(missing) in result.output
    assert "Traceback" not in result.output


@pytest.mark.skipif(not _HAS_TYPER, reason="typer not installed")
def test_setup_reports_invalid_from_state_without_using_defaults(tmp_path: object) -> None:
    from typer.testing import CliRunner

    from agmind.cli import _make_app

    state_file = tmp_path / "setup-state.json"  # type: ignore[operator]
    state_file.write_text("{not-json", encoding="utf-8")
    cli_app = _make_app()
    runner = CliRunner()

    result = runner.invoke(
        cli_app,
        [
            "setup",
            "--no-tui",
            "--dry-run",
            "--from-state",
            str(state_file),
            "--domain",
            "lab.example.com",
        ],
    )

    assert result.exit_code == 2
    assert "cannot load --from-state" in result.output
    assert str(state_file) in result.output
    assert "dry-run: stopping" not in result.output
    assert "Traceback" not in result.output


@pytest.mark.skipif(not _HAS_TYPER, reason="typer not installed")
def test_install_no_tui_prints_runtime_credentials_path(
    tmp_path: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from typer.testing import CliRunner

    from agmind.cli import _make_app
    from agmind.install.orchestrator import InstallResult

    token_file = tmp_path / "cf-token"  # type: ignore[operator]
    token_file.write_text("super-secret-token-with-length-40-abcdef", encoding="utf-8")
    token_file.chmod(0o600)

    monkeypatch.setattr("getpass.getpass", lambda prompt: "sudo-password")
    monkeypatch.setattr("agmind.install.steps.default_steps", lambda: [])

    class FakeOrchestrator:
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs

        def run(self) -> InstallResult:
            return InstallResult(success=True, steps=(), message="install ok")

    monkeypatch.setattr("agmind.install.orchestrator.InstallOrchestrator", FakeOrchestrator)

    cli_app = _make_app()
    runner = CliRunner()
    result = runner.invoke(
        cli_app,
        [
            "install",
            "--no-tui",
            "--domain",
            "lab.example.com",
            "--cf-token-file",
            str(token_file),
            "--model-file",
            "model.gguf",
        ],
    )

    assert result.exit_code == 0
    assert "Runtime credentials: /opt/agmind/.env (chmod 600)" in result.output
    assert "Values are not printed" in result.output
    assert "super-secret-token" not in result.output


@pytest.mark.skipif(not _HAS_TYPER, reason="typer not installed")
def test_install_no_tui_requires_cf_token_before_sudo_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from typer.testing import CliRunner

    from agmind.cli import _make_app

    def fail_getpass(prompt: str) -> str:
        raise AssertionError(f"sudo prompt should not run before validation: {prompt}")

    monkeypatch.setattr("getpass.getpass", fail_getpass)
    monkeypatch.setattr(
        "agmind.install.steps.default_steps",
        lambda: (_ for _ in ()).throw(AssertionError("install steps should not be built")),
    )

    cli_app = _make_app()
    runner = CliRunner()
    result = runner.invoke(
        cli_app,
        [
            "install",
            "--no-tui",
            "--domain",
            "lab.example.com",
            "--model-file",
            "model.gguf",
        ],
    )

    assert result.exit_code == 2
    assert "CF API token" in result.output
    assert "--cf-token-file" in result.output
    assert "Traceback" not in result.output


@pytest.mark.skipif(not _HAS_TYPER, reason="typer not installed")
def test_install_no_tui_rejects_invalid_domain_before_sudo_prompt(
    tmp_path: object,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from typer.testing import CliRunner

    from agmind.cli import _make_app

    token_file = tmp_path / "cf-token"  # type: ignore[operator]
    token_file.write_text("super-secret-token-with-length-40-abcdef", encoding="utf-8")
    token_file.chmod(0o600)

    def fail_getpass(prompt: str) -> str:
        raise AssertionError(f"sudo prompt should not run before validation: {prompt}")

    monkeypatch.setattr("getpass.getpass", fail_getpass)
    monkeypatch.setattr(
        "agmind.install.steps.default_steps",
        lambda: (_ for _ in ()).throw(AssertionError("install steps should not be built")),
    )

    cli_app = _make_app()
    runner = CliRunner()
    result = runner.invoke(
        cli_app,
        [
            "install",
            "--no-tui",
            "--domain",
            "bad`domain.example",
            "--cf-token-file",
            str(token_file),
            "--model-file",
            "model.gguf",
        ],
    )

    assert result.exit_code == 2
    assert "domain" in result.output.lower()
    assert "Traceback" not in result.output


@pytest.mark.skipif(not _HAS_TYPER, reason="typer not installed")
def test_install_dry_run_from_state_preserves_install_dir(tmp_path: object) -> None:
    from typer.testing import CliRunner

    from agmind.cli import _make_app

    state_file = tmp_path / "setup-state.json"  # type: ignore[operator]
    token_file = tmp_path / "cf-token"  # type: ignore[operator]
    install_dir = tmp_path / "user-stack"  # type: ignore[operator]
    state_file.write_text(
        json.dumps(
            {
                "domain": "old.example.com",
                "services": ["traefik", "llama-llm", "qdrant"],
                "profiles": [],
                "model_id": "custom",
                "model_repo": "repo/llm",
                "model_file": "llm.gguf",
                "install_dir": str(install_dir),
            }
        ),
        encoding="utf-8",
    )
    token_file.write_text("super-secret-token-with-length-40-abcdef\n", encoding="utf-8")
    token_file.chmod(0o600)

    cli_app = _make_app()
    runner = CliRunner()
    result = runner.invoke(
        cli_app,
        [
            "install",
            "--no-tui",
            "--dry-run",
            "--from-state",
            str(state_file),
            "--domain",
            "lab.example.com",
            "--cf-token-file",
            str(token_file),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output.split("\n", 1)[1])
    assert payload["domain"] == "lab.example.com"
    assert payload["install_dir"] == str(install_dir)
    assert payload["services"] == ["traefik", "llama-llm", "qdrant"]
    assert "super-secret-token" not in result.output


@pytest.mark.skipif(not _HAS_TYPER, reason="typer not installed")
def test_install_dry_run_from_state_rejects_unknown_service(tmp_path: object) -> None:
    from typer.testing import CliRunner

    from agmind.cli import _make_app

    state_file = tmp_path / "setup-state.json"  # type: ignore[operator]
    token_file = tmp_path / "cf-token"  # type: ignore[operator]
    state_file.write_text(
        json.dumps(
            {
                "domain": "old.example.com",
                "services": ["traefik", "missing-service"],
                "profiles": [],
                "model_id": "custom",
                "model_repo": "repo/llm",
                "model_file": "llm.gguf",
            }
        ),
        encoding="utf-8",
    )
    token_file.write_text("super-secret-token-with-length-40-abcdef\n", encoding="utf-8")
    token_file.chmod(0o600)

    cli_app = _make_app()
    runner = CliRunner()
    result = runner.invoke(
        cli_app,
        [
            "install",
            "--no-tui",
            "--dry-run",
            "--from-state",
            str(state_file),
            "--domain",
            "lab.example.com",
            "--cf-token-file",
            str(token_file),
        ],
    )

    assert result.exit_code == 2
    assert "unknown selected services in --from-state: missing-service" in result.output
    assert "dry-run: stopping" not in result.output
    assert "Traceback" not in result.output


@pytest.mark.skipif(not _HAS_TYPER, reason="typer not installed")
def test_install_dry_run_from_legacy_profile_state_expands_services(tmp_path: object) -> None:
    from typer.testing import CliRunner

    from agmind.cli import _make_app

    state_file = tmp_path / "legacy-setup-state.json"  # type: ignore[operator]
    token_file = tmp_path / "cf-token"  # type: ignore[operator]
    state_file.write_text(
        json.dumps(
            {
                "domain": "old.example.com",
                "services": [],
                "profiles": ["core"],
                "model_id": "custom",
                "model_repo": "repo/llm",
                "model_file": "llm.gguf",
            }
        ),
        encoding="utf-8",
    )
    token_file.write_text("super-secret-token-with-length-40-abcdef\n", encoding="utf-8")
    token_file.chmod(0o600)

    cli_app = _make_app()
    runner = CliRunner()
    result = runner.invoke(
        cli_app,
        [
            "install",
            "--no-tui",
            "--dry-run",
            "--from-state",
            str(state_file),
            "--domain",
            "lab.example.com",
            "--cf-token-file",
            str(token_file),
        ],
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output.split("\n", 1)[1])
    assert payload["services"]
    assert "traefik" in payload["services"]
    assert "llama-llm" in payload["services"]
    assert "qdrant" in payload["services"]
    assert set(payload["services"]) == {
        "llama-embed",
        "llama-llm",
        "llama-rerank",
        "qdrant",
        "traefik",
    }


@pytest.mark.skipif(not _HAS_TYPER, reason="typer not installed")
def test_install_dry_run_from_unknown_legacy_profile_fails(tmp_path: object) -> None:
    from typer.testing import CliRunner

    from agmind.cli import _make_app

    state_file = tmp_path / "legacy-setup-state.json"  # type: ignore[operator]
    token_file = tmp_path / "cf-token"  # type: ignore[operator]
    state_file.write_text(
        json.dumps(
            {
                "domain": "old.example.com",
                "services": [],
                "profiles": ["does-not-exist"],
                "model_id": "custom",
                "model_repo": "repo/llm",
                "model_file": "llm.gguf",
            }
        ),
        encoding="utf-8",
    )
    token_file.write_text("super-secret-token-with-length-40-abcdef\n", encoding="utf-8")
    token_file.chmod(0o600)

    cli_app = _make_app()
    runner = CliRunner()
    result = runner.invoke(
        cli_app,
        [
            "install",
            "--no-tui",
            "--dry-run",
            "--from-state",
            str(state_file),
            "--domain",
            "lab.example.com",
            "--cf-token-file",
            str(token_file),
        ],
    )

    assert result.exit_code == 2
    assert "unknown selected profiles in --from-state" in result.output
    assert "does-not-exist" in result.output
    assert "dry-run: stopping" not in result.output
    assert "Traceback" not in result.output


@pytest.mark.skipif(not _HAS_TYPER, reason="typer not installed")
def test_install_dry_run_from_mixed_unknown_legacy_profile_fails(tmp_path: object) -> None:
    from typer.testing import CliRunner

    from agmind.cli import _make_app

    state_file = tmp_path / "legacy-setup-state.json"  # type: ignore[operator]
    token_file = tmp_path / "cf-token"  # type: ignore[operator]
    state_file.write_text(
        json.dumps(
            {
                "domain": "old.example.com",
                "services": [],
                "profiles": ["core", "missing-profile"],
                "model_id": "custom",
                "model_repo": "repo/llm",
                "model_file": "llm.gguf",
            }
        ),
        encoding="utf-8",
    )
    token_file.write_text("super-secret-token-with-length-40-abcdef\n", encoding="utf-8")
    token_file.chmod(0o600)

    cli_app = _make_app()
    runner = CliRunner()
    result = runner.invoke(
        cli_app,
        [
            "install",
            "--no-tui",
            "--dry-run",
            "--from-state",
            str(state_file),
            "--domain",
            "lab.example.com",
            "--cf-token-file",
            str(token_file),
        ],
    )

    assert result.exit_code == 2
    assert "unknown selected profiles in --from-state: missing-profile" in result.output
    assert "dry-run: stopping" not in result.output
    assert "Traceback" not in result.output


@pytest.mark.skipif(not _HAS_TYPER, reason="typer not installed")
def test_install_dry_run_from_empty_state_fails_before_config_dump(tmp_path: object) -> None:
    from typer.testing import CliRunner

    from agmind.cli import _make_app

    state_file = tmp_path / "empty-setup-state.json"  # type: ignore[operator]
    token_file = tmp_path / "cf-token"  # type: ignore[operator]
    state_file.write_text(
        json.dumps(
            {
                "domain": "old.example.com",
                "services": [],
                "profiles": [],
                "model_id": "custom",
                "model_repo": "repo/llm",
                "model_file": "llm.gguf",
            }
        ),
        encoding="utf-8",
    )
    token_file.write_text("super-secret-token-with-length-40-abcdef\n", encoding="utf-8")
    token_file.chmod(0o600)

    cli_app = _make_app()
    runner = CliRunner()
    result = runner.invoke(
        cli_app,
        [
            "install",
            "--no-tui",
            "--dry-run",
            "--from-state",
            str(state_file),
            "--domain",
            "lab.example.com",
            "--cf-token-file",
            str(token_file),
        ],
    )

    assert result.exit_code == 2
    assert "no selected services in --from-state" in result.output
    assert "dry-run: stopping" not in result.output
    assert "Traceback" not in result.output


@pytest.mark.skipif(not _HAS_TYPER, reason="typer not installed")
def test_ops_smoke_backup_root_owned_dry_run_command() -> None:
    from typer.testing import CliRunner

    from agmind.cli import _make_app

    cli_app = _make_app()
    runner = CliRunner()
    result = runner.invoke(cli_app, ["ops", "smoke", "backup-root-owned", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert "root-owned backup smoke dry-run" in result.output
    assert "/tmp/agmind-root-owned-smoke" in result.output
    assert "/opt/agmind" not in result.output


@pytest.mark.skipif(not _HAS_TYPER, reason="typer not installed")
def test_app_version_command() -> None:
    from typer.testing import CliRunner

    from agmind import __version__
    from agmind.cli import _make_app

    cli_app = _make_app()
    runner = CliRunner()
    result = runner.invoke(cli_app, ["version"])
    assert result.exit_code == 0
    assert __version__ in result.output


@pytest.mark.skipif(not _HAS_TYPER, reason="typer not installed")
def test_tools_validate_command() -> None:
    from typer.testing import CliRunner

    from agmind.cli import _make_app

    cli_app = _make_app()
    runner = CliRunner()
    result = runner.invoke(cli_app, ["tools", "validate"])
    assert result.exit_code == 0
    assert "tool candidates OK" in result.output


@pytest.mark.skipif(not _HAS_TYPER, reason="typer not installed")
def test_targets_validate_command() -> None:
    from typer.testing import CliRunner

    from agmind.cli import _make_app

    cli_app = _make_app()
    runner = CliRunner()
    result = runner.invoke(cli_app, ["targets", "validate"])
    assert result.exit_code == 0
    assert "deployment targets OK" in result.output


@pytest.mark.skipif(not _HAS_TYPER, reason="typer not installed")
def test_targets_validate_json_command() -> None:
    import json

    from typer.testing import CliRunner

    from agmind.cli import _make_app

    cli_app = _make_app()
    runner = CliRunner()
    result = runner.invoke(cli_app, ["targets", "validate", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["ok"] is True
    assert payload["target_count"] == 3


@pytest.mark.skipif(not _HAS_TYPER, reason="typer not installed")
def test_cluster_inspect_command_json(monkeypatch: pytest.MonkeyPatch) -> None:
    from typer.testing import CliRunner

    from agmind.cli import _make_app
    from agmind.cluster.inspect import CommandResult, inspect_cluster

    report = inspect_cluster(
        run=lambda args: CommandResult(127),
        path_exists=lambda path: False,
        discover_peers=lambda: [],
    )
    monkeypatch.setattr("agmind.cli.cluster_cmd._inspect_cluster", lambda discover_timeout: report)

    cli_app = _make_app()
    runner = CliRunner()
    result = runner.invoke(cli_app, ["cluster", "inspect", "--json", "--timeout", "0.1"])
    assert result.exit_code == 0
    assert '"detected_target": "unknown"' in result.output


@pytest.mark.skipif(not _HAS_TYPER, reason="typer not installed")
def test_governance_validate_command() -> None:
    from typer.testing import CliRunner

    from agmind.cli import _make_app

    cli_app = _make_app()
    runner = CliRunner()
    result = runner.invoke(cli_app, ["governance", "validate"])
    assert result.exit_code == 0
    assert "governance OK: 9 checks" in result.output


@pytest.mark.skipif(not _HAS_TYPER, reason="typer not installed")
def test_deploy_status_subcommand_routes_to_compose_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from typer.testing import CliRunner

    from agmind.cli import _make_app

    calls: list[str] = []

    def fake_status() -> int:
        calls.append("status")
        return 0

    monkeypatch.setattr("agmind.cli.deploy_cmd.cmd_status", fake_status)

    cli_app = _make_app()
    runner = CliRunner()
    result = runner.invoke(cli_app, ["deploy", "status"])

    assert result.exit_code == 0
    assert calls == ["status"]


@pytest.mark.skipif(not _HAS_TYPER, reason="typer not installed")
def test_deploy_logs_subcommand_routes_to_compose_logs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from typer.testing import CliRunner

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

    cli_app = _make_app()
    runner = CliRunner()
    result = runner.invoke(cli_app, ["deploy", "logs", "llama-llm", "--lines", "123", "-f"])

    assert result.exit_code == 0
    assert calls == {"service": "llama-llm", "follow": True, "lines": 123}


@pytest.mark.skipif(not _HAS_TYPER, reason="typer not installed")
def test_deploy_restart_subcommand_routes_to_compose_restart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from typer.testing import CliRunner

    from agmind.cli import _make_app

    calls: list[str | None] = []

    def fake_restart(service: str | None = None) -> int:
        calls.append(service)
        return 0

    monkeypatch.setattr("agmind.cli.deploy_cmd.cmd_restart", fake_restart)

    cli_app = _make_app()
    runner = CliRunner()
    result = runner.invoke(cli_app, ["deploy", "restart", "llama-llm"])

    assert result.exit_code == 0
    assert calls == ["llama-llm"]


@pytest.mark.skipif(not _HAS_TYPER, reason="typer not installed")
def test_ci_status_command_json(monkeypatch: pytest.MonkeyPatch) -> None:
    from typer.testing import CliRunner

    from agmind.ci.monitor import CIMonitorReport
    from agmind.cli import _make_app

    monkeypatch.setattr(
        "agmind.cli.ci_cmd.collect_ci_status",
        lambda repository, run_limit: CIMonitorReport(repository="botAGI/AGmind64"),
    )

    cli_app = _make_app()
    runner = CliRunner()
    result = runner.invoke(cli_app, ["ci", "status", "--json", "--repo", "botAGI/AGmind64"])
    assert result.exit_code == 0
    assert '"repository": "botAGI/AGmind64"' in result.output


@pytest.mark.skipif(not _HAS_TYPER, reason="typer not installed")
def test_render_kubernetes_command() -> None:
    from typer.testing import CliRunner

    from agmind.cli import _make_app

    cli_app = _make_app()
    runner = CliRunner()
    result = runner.invoke(
        cli_app,
        ["render", "kubernetes", "--profile", "proxmox", "--namespace", "agmind"],
    )
    assert result.exit_code == 0
    assert "kind: Deployment" in result.output
    assert "name: proxmox-exporter" in result.output


@pytest.mark.skipif(not _HAS_TYPER, reason="typer not installed")
def test_render_kubernetes_target_applies_excluded_services() -> None:
    from typer.testing import CliRunner

    from agmind.cli import _make_app

    cli_app = _make_app()
    runner = CliRunner()
    result = runner.invoke(
        cli_app,
        ["render", "kubernetes", "--target", "k3s", "--namespace", "agmind"],
    )

    assert result.exit_code == 0, result.output
    assert "amd-gpu-device-plugin" in result.output
    assert "kubernetes-omitted" not in result.output
    assert "name: portainer" not in result.output
    assert "name: dozzle" not in result.output


@pytest.mark.skipif(not _HAS_TYPER, reason="typer not installed")
def test_render_kubernetes_accepts_repeated_explicit_services(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from typer.testing import CliRunner

    from agmind.cli import _make_app

    captured: dict[str, object] = {}

    def fake_cmd_render_kubernetes(**kwargs: object) -> int:
        captured.update(kwargs)
        return 0

    monkeypatch.setattr(
        "agmind.cli.render_cmd.cmd_render_kubernetes",
        fake_cmd_render_kubernetes,
    )

    cli_app = _make_app()
    runner = CliRunner()
    result = runner.invoke(
        cli_app,
        [
            "render",
            "kubernetes",
            "--profile",
            "stale-profile",
            "--service",
            "n8n",
            "--service",
            "dozzle",
            "--namespace",
            "agmind",
        ],
    )

    assert result.exit_code == 0
    assert captured["profiles"] == ["stale-profile"]
    assert captured["services"] == ["n8n", "dozzle"]
    assert captured["namespace"] == "agmind"


@pytest.mark.skipif(not _HAS_TYPER, reason="typer not installed")
def test_render_topology_command_reports_ambiguous_vector_provider() -> None:
    from typer.testing import CliRunner

    from agmind.cli import _make_app

    cli_app = _make_app()
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

    from typer.testing import CliRunner

    from agmind.cli import _make_app

    cli_app = _make_app()
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
            "etcd",
            "--service",
            "milvus-minio",
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
    from typer.testing import CliRunner

    from agmind.cli import _make_app

    cli_app = _make_app()
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

    from typer.testing import CliRunner

    from agmind.cli import _make_app

    cli_app = _make_app()
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

    from typer.testing import CliRunner

    from agmind.cli import _make_app

    cli_app = _make_app()
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
    from typer.testing import CliRunner

    from agmind.cli import _make_app

    cli_app = _make_app()
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
def test_render_topology_rejects_unknown_explicit_service() -> None:
    from typer.testing import CliRunner

    from agmind.cli import _make_app

    cli_app = _make_app()
    runner = CliRunner()
    result = runner.invoke(
        cli_app,
        [
            "render",
            "topology",
            "--service",
            "postgres",
            "--service",
            "missing-service",
        ],
    )

    assert result.exit_code == 1
    assert "unknown selected services for topology: missing-service" in result.output
    assert "TOPOLOGY OK" not in result.output


@pytest.mark.skipif(not _HAS_TYPER, reason="typer not installed")
def test_render_topology_rejects_unknown_profile() -> None:
    from typer.testing import CliRunner

    from agmind.cli import _make_app

    cli_app = _make_app()
    runner = CliRunner()
    result = runner.invoke(
        cli_app,
        [
            "render",
            "topology",
            "--profile",
            "core,missing-profile",
        ],
    )

    assert result.exit_code == 1
    assert "unknown selected profiles for topology: missing-profile" in result.output
    assert "TOPOLOGY OK" not in result.output
    assert "RAG STORAGE PLAN" not in result.output


@pytest.mark.skipif(not _HAS_TYPER, reason="typer not installed")
def test_upgrade_plan_does_not_apply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from typer.testing import CliRunner

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

    cli_app = _make_app()
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
