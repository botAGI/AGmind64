from __future__ import annotations

from pathlib import Path

import pytest

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
