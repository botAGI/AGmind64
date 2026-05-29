from __future__ import annotations

from pathlib import Path

import pytest

import agmind.deploy as deploy_module
from agmind.cli import deploy_cmd

pytestmark = pytest.mark.backend_any


def test_run_compose_uses_install_dir_env_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_dir = tmp_path / "agmind"
    install_dir.mkdir()
    compose = install_dir / "docker-compose.yml"
    env_file = install_dir / ".env"
    compose.write_text("services: {}\n", encoding="utf-8")
    env_file.write_text("POSTGRES_PASSWORD=x\n", encoding="utf-8")
    monkeypatch.setenv("AGMIND_INSTALL_DIR", str(install_dir))
    calls: list[dict[str, object]] = []

    def fake_run(cmd: list[str], cwd: Path, check: bool) -> object:
        calls.append({"cmd": cmd, "cwd": cwd, "check": check})

        class Result:
            returncode = 0

        return Result()

    monkeypatch.setattr(deploy_cmd.subprocess, "run", fake_run)

    assert deploy_cmd._run_compose("config", "--quiet") == 0

    assert calls == [
        {
            "cmd": [
                "docker",
                "compose",
                "--env-file",
                str(env_file),
                "-f",
                str(compose),
                "config",
                "--quiet",
            ],
            "cwd": install_dir,
            "check": False,
        }
    ]


def test_run_compose_still_works_before_env_file_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_dir = tmp_path / "agmind"
    install_dir.mkdir()
    compose = install_dir / "docker-compose.yml"
    compose.write_text("services: {}\n", encoding="utf-8")
    monkeypatch.setenv("AGMIND_INSTALL_DIR", str(install_dir))
    calls: list[list[str]] = []

    def fake_run(cmd: list[str], cwd: Path, check: bool) -> object:
        calls.append(cmd)

        class Result:
            returncode = 0

        return Result()

    monkeypatch.setattr(deploy_cmd.subprocess, "run", fake_run)

    assert deploy_cmd._run_compose("ps") == 0
    assert "--env-file" not in calls[0]


def test_run_compose_reports_subprocess_failure_without_traceback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    install_dir = tmp_path / "agmind"
    install_dir.mkdir()
    (install_dir / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    monkeypatch.setenv("AGMIND_INSTALL_DIR", str(install_dir))

    def fake_run(cmd: list[str], cwd: Path, check: bool) -> object:
        raise OSError("permission denied")

    monkeypatch.setattr(deploy_cmd.subprocess, "run", fake_run)

    assert deploy_cmd._run_compose("ps") == 1
    err = capsys.readouterr().err
    assert "docker compose failed" in err
    assert "permission denied" in err
    assert "Traceback" not in err


def test_cmd_deploy_prompts_for_sudo_password_when_requested(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: dict[str, object] = {}

    def fake_deploy(**kwargs: object):
        calls.update(kwargs)

        class Result:
            success = True
            diff = None
            snapshot = None
            message = "ok"
            rollback_performed = False

        return Result()

    monkeypatch.setattr(deploy_module, "deploy", fake_deploy)
    monkeypatch.setattr(deploy_cmd.getpass, "getpass", lambda prompt: "pw")

    rc = deploy_cmd.cmd_deploy(
        profiles=["core"],
        services=None,
        install_dir=tmp_path,
        domain="ci.example.com",
        apply=True,
        no_prompt=True,
        healthcheck_timeout=1,
        ask_sudo_password=True,
    )

    assert rc == 0
    assert calls["sudo_password"] == "pw"


def test_cmd_deploy_keeps_legacy_positional_call_shape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: dict[str, object] = {}

    def fake_deploy(**kwargs: object):
        calls.update(kwargs)

        class Result:
            success = True
            diff = None
            snapshot = None
            message = "ok"
            rollback_performed = False

        return Result()

    monkeypatch.setattr(deploy_module, "deploy", fake_deploy)

    rc = deploy_cmd.cmd_deploy(
        ["core"],
        tmp_path,
        "ci.example.com",
        True,
        True,
        1,
    )

    assert rc == 0
    assert calls["profiles"] == ["core"]
    assert calls["services"] is None
    assert calls["install_dir"] == tmp_path


def test_cmd_deploy_passes_explicit_services_to_runner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: dict[str, object] = {}

    def fake_deploy(**kwargs: object):
        calls.update(kwargs)

        class Result:
            success = True
            diff = None
            snapshot = None
            message = "ok"
            rollback_performed = False

        return Result()

    monkeypatch.setattr(deploy_module, "deploy", fake_deploy)

    rc = deploy_cmd.cmd_deploy(
        profiles=["stale-profile"],
        services=["traefik"],
        install_dir=tmp_path,
        domain="ci.example.com",
        apply=True,
        no_prompt=True,
        healthcheck_timeout=1,
    )

    assert rc == 0
    assert calls["profiles"] == ["stale-profile"]
    assert calls["services"] == ["traefik"]


def test_cmd_deploy_threads_no_prompt_to_runner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """cmd_deploy passes its no_prompt argument straight through to do_deploy."""
    calls: dict[str, object] = {}

    def fake_deploy(**kwargs: object):
        calls.update(kwargs)

        class Result:
            success = True
            diff = None
            snapshot = None
            message = "ok"
            rollback_performed = False

        return Result()

    monkeypatch.setattr(deploy_module, "deploy", fake_deploy)

    rc = deploy_cmd.cmd_deploy(
        profiles=["core"],
        services=None,
        install_dir=tmp_path,
        domain="ci.example.com",
        apply=True,
        no_prompt=False,
        healthcheck_timeout=1,
    )

    assert rc == 0
    assert calls["no_prompt"] is False


def test_deploy_no_prompt_flag_maps_to_runner_kwarg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CliRunner: `deploy --apply --no-prompt` reaches do_deploy with no_prompt=True."""
    from typer.testing import CliRunner

    from agmind.cli import _make_app

    captured: dict[str, object] = {}

    def fake_deploy(**kwargs: object):
        captured.update(kwargs)

        class Result:
            success = True
            diff = None
            snapshot = None
            message = "ok"
            rollback_performed = False

        return Result()

    monkeypatch.setattr(deploy_module, "deploy", fake_deploy)

    result = CliRunner().invoke(
        _make_app(),
        [
            "deploy",
            "--service",
            "traefik",
            "--install-dir",
            str(tmp_path / "install"),
            "--apply",
            "--no-prompt",
        ],
    )

    assert result.exit_code == 0
    assert captured["no_prompt"] is True


def test_deploy_yes_alias_maps_to_runner_kwarg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CliRunner: `deploy --apply --yes` is an alias mapping to no_prompt=True."""
    from typer.testing import CliRunner

    from agmind.cli import _make_app

    captured: dict[str, object] = {}

    def fake_deploy(**kwargs: object):
        captured.update(kwargs)

        class Result:
            success = True
            diff = None
            snapshot = None
            message = "ok"
            rollback_performed = False

        return Result()

    monkeypatch.setattr(deploy_module, "deploy", fake_deploy)

    result = CliRunner().invoke(
        _make_app(),
        [
            "deploy",
            "--service",
            "traefik",
            "--install-dir",
            str(tmp_path / "install"),
            "--apply",
            "--yes",
        ],
    )

    assert result.exit_code == 0
    assert captured["no_prompt"] is True


def test_deploy_without_no_prompt_flag_keeps_runner_kwarg_false(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CliRunner: omitting the flag leaves no_prompt=False so the gate stays armed."""
    from typer.testing import CliRunner

    from agmind.cli import _make_app

    captured: dict[str, object] = {}

    def fake_deploy(**kwargs: object):
        captured.update(kwargs)

        class Result:
            success = True
            diff = None
            snapshot = None
            message = "ok"
            rollback_performed = False

        return Result()

    monkeypatch.setattr(deploy_module, "deploy", fake_deploy)

    result = CliRunner().invoke(
        _make_app(),
        [
            "deploy",
            "--service",
            "traefik",
            "--install-dir",
            str(tmp_path / "install"),
            "--apply",
        ],
    )

    assert result.exit_code == 0
    assert captured["no_prompt"] is False


def test_cmd_rollback_prompts_for_sudo_password_when_requested(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: dict[str, object] = {}

    def fake_rollback(**kwargs: object):
        calls.update(kwargs)

        class Result:
            success = True
            message = "rolled back"

        return Result()

    monkeypatch.setattr(deploy_module, "rollback", fake_rollback)
    monkeypatch.setattr(deploy_cmd.getpass, "getpass", lambda prompt: "pw")

    rc = deploy_cmd.cmd_rollback(
        snapshot_id="snap-1",
        install_dir=tmp_path,
        ask_sudo_password=True,
    )

    assert rc == 0
    assert calls == {
        "snapshot_id": "snap-1",
        "install_dir": tmp_path,
        "sudo_password": "pw",
    }
