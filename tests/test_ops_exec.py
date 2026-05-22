"""Phase L.E: tests for agmind.ops.exec (logs / shell)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from agmind.ops.exec import known_services, logs, shell

pytestmark = pytest.mark.backend_any


def _make_install_dir(tmp_path: Path) -> Path:
    install = tmp_path / "opt"
    install.mkdir()
    (install / "docker-compose.yml").write_text(
        "services:\n"
        "  traefik:\n    image: traefik:v3.0\n"
        "  llama-llm:\n    image: ggerganov/llama.cpp:server\n"
        "  qdrant:\n    image: qdrant/qdrant:v1.14\n",
        encoding="utf-8",
    )
    return install


# ---------- known_services ----------


def test_known_services_parses_compose(tmp_path: Path) -> None:
    install = _make_install_dir(tmp_path)
    assert known_services(install) == ["llama-llm", "qdrant", "traefik"]


def test_known_services_missing_compose(tmp_path: Path) -> None:
    assert known_services(tmp_path) == []


def test_known_services_invalid_yaml(tmp_path: Path) -> None:
    (tmp_path / "docker-compose.yml").write_text("not: : yaml :", encoding="utf-8")
    assert known_services(tmp_path) == []


# ---------- logs ----------


def test_logs_no_compose_returns_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc = logs(install_dir=tmp_path)
    assert rc == 2
    assert "no deployment" in capsys.readouterr().out


def test_logs_unknown_service(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    install = _make_install_dir(tmp_path)
    rc = logs(install_dir=install, service="nonexistent")
    assert rc == 2
    out = capsys.readouterr().out
    assert "unknown service" in out
    assert "traefik" in out


def test_logs_invokes_docker_compose(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    install = _make_install_dir(tmp_path)
    captured: dict[str, object] = {}

    def fake_run(cmd: list[str], **kw: object) -> object:
        captured["cmd"] = cmd
        captured["cwd"] = kw.get("cwd")

        class P:
            returncode = 0

        return P()

    monkeypatch.setattr(
        "shutil.which", lambda name: "/usr/bin/docker" if name == "docker" else None
    )
    monkeypatch.setattr(subprocess, "run", fake_run)

    rc = logs(install_dir=install, service="traefik", tail=42, follow=True)
    assert rc == 0
    cmd = captured["cmd"]
    assert "docker" in cmd[0]
    assert "logs" in cmd
    assert "--tail=42" in cmd
    assert "--follow" in cmd
    assert "traefik" in cmd
    assert captured["cwd"] == install


def test_logs_no_service_means_all(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    install = _make_install_dir(tmp_path)
    captured: dict[str, object] = {}

    def fake_run(cmd: list[str], **kw: object) -> object:
        captured["cmd"] = cmd

        class P:
            returncode = 0

        return P()

    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/docker")
    monkeypatch.setattr(subprocess, "run", fake_run)
    rc = logs(install_dir=install, service=None, tail=100)
    assert rc == 0
    cmd = captured["cmd"]
    # No service name should be appended при None
    services = ["traefik", "llama-llm", "qdrant"]
    assert not any(s in cmd for s in services)


def test_logs_no_docker_binary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    install = _make_install_dir(tmp_path)
    monkeypatch.setattr("shutil.which", lambda name: None)
    rc = logs(install_dir=install)
    assert rc == 2
    assert "docker" in capsys.readouterr().out


# ---------- shell ----------


def test_shell_no_compose(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc = shell(install_dir=tmp_path, service="anything")
    assert rc == 2
    assert "no deployment" in capsys.readouterr().out


def test_shell_unknown_service(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    install = _make_install_dir(tmp_path)
    rc = shell(install_dir=install, service="ghost")
    assert rc == 2
    assert "unknown service" in capsys.readouterr().out


def test_shell_invokes_docker_compose_exec(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    install = _make_install_dir(tmp_path)
    captured: dict[str, object] = {}

    def fake_run(cmd: list[str], **kw: object) -> object:
        captured["cmd"] = cmd

        class P:
            returncode = 0

        return P()

    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/docker")
    monkeypatch.setattr(subprocess, "run", fake_run)
    rc = shell(install_dir=install, service="traefik", cmd=["/bin/bash"], workdir="/tmp")
    assert rc == 0
    cmd = captured["cmd"]
    assert cmd[:3] == ["docker", "compose", "exec"]
    assert "-w" in cmd
    assert "/tmp" in cmd
    assert "traefik" in cmd
    assert "/bin/bash" in cmd


def test_shell_default_cmd_is_sh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    install = _make_install_dir(tmp_path)
    captured: dict[str, object] = {}
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/docker")

    def fake_run(cmd: list[str], **kw: object) -> object:
        captured["cmd"] = cmd

        class P:
            returncode = 0

        return P()

    monkeypatch.setattr(subprocess, "run", fake_run)
    rc = shell(install_dir=install, service="traefik")
    assert rc == 0
    assert "/bin/sh" in captured["cmd"]
