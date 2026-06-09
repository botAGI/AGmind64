"""Phase L.E: tests for agmind.ops.exec (logs / shell)."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from agmind.ops.exec import _check_prereqs, known_services, logs, shell

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


def test_known_services_inaccessible_compose_returns_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    original_exists = Path.exists

    def flaky_exists(path: Path) -> bool:
        if path.name == "docker-compose.yml":
            raise PermissionError("stat denied")
        return original_exists(path)

    monkeypatch.setattr(Path, "exists", flaky_exists)

    assert known_services(tmp_path) == []


# ---------- _check_prereqs ordering ----------


def test_check_prereqs_missing_compose_wins_over_missing_docker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The actionable error is "no deployment … run `agmind deploy --apply`".

    The compose-file check MUST precede the docker-binary check: on a host that
    has never deployed (no docker-compose.yml) the operator's next step is to run
    the installer, NOT to debug a docker PATH issue. Reordering also makes the
    bare logs/shell no-compose tests hermetic on a docker-less CI box.
    """
    monkeypatch.setattr("shutil.which", lambda name: None)
    err = _check_prereqs(tmp_path)
    assert err is not None
    assert "no deployment" in err
    assert "docker binary" not in err


def test_check_prereqs_docker_error_when_compose_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When a deployment exists but docker is gone, surface the docker error."""
    install = _make_install_dir(tmp_path)
    monkeypatch.setattr("shutil.which", lambda name: None)
    err = _check_prereqs(install)
    assert err is not None
    assert "docker binary not found" in err


# ---------- logs ----------


def test_logs_no_compose_returns_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # Hermetic: pin docker present so the assertion targets the compose-missing
    # path regardless of whether the runner has docker on PATH.
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/docker")
    rc = logs(install_dir=tmp_path)
    assert rc == 2
    assert "no deployment" in capsys.readouterr().out


def test_logs_unknown_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/docker")
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


def test_logs_reports_inaccessible_compose_without_traceback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    install = tmp_path / "locked"
    install.mkdir()
    original_exists = Path.exists

    def flaky_exists(path: Path) -> bool:
        if path.name == "docker-compose.yml":
            raise PermissionError("stat denied")
        return original_exists(path)

    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/docker")
    monkeypatch.setattr(Path, "exists", flaky_exists)

    rc = logs(install_dir=install)

    out = capsys.readouterr().out
    assert rc == 2
    assert "cannot access deployment" in out
    assert "stat denied" in out
    assert "Traceback" not in out


def test_logs_reports_subprocess_failure_without_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    install = _make_install_dir(tmp_path)

    def fake_run(cmd: list[str], **kw: object) -> object:
        raise OSError("permission denied")

    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/docker")
    monkeypatch.setattr(subprocess, "run", fake_run)

    rc = logs(install_dir=install, service="traefik")

    out = capsys.readouterr().out
    assert rc == 1
    assert "permission denied" in out
    assert "Traceback" not in out


# ---------- shell ----------


def test_shell_no_compose(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/docker")
    rc = shell(install_dir=tmp_path, service="anything")
    assert rc == 2
    assert "no deployment" in capsys.readouterr().out


def test_shell_unknown_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/docker")
    install = _make_install_dir(tmp_path)
    rc = shell(install_dir=install, service="ghost")
    assert rc == 2
    assert "unknown service" in capsys.readouterr().out


def test_shell_reports_inaccessible_compose_without_traceback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    install = tmp_path / "locked"
    install.mkdir()
    original_exists = Path.exists

    def flaky_exists(path: Path) -> bool:
        if path.name == "docker-compose.yml":
            raise PermissionError("stat denied")
        return original_exists(path)

    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/docker")
    monkeypatch.setattr(Path, "exists", flaky_exists)

    rc = shell(install_dir=install, service="traefik")

    out = capsys.readouterr().out
    assert rc == 2
    assert "cannot access deployment" in out
    assert "stat denied" in out
    assert "Traceback" not in out


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


def test_shell_reports_subprocess_failure_without_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    install = _make_install_dir(tmp_path)

    def fake_run(cmd: list[str], **kw: object) -> object:
        raise OSError("permission denied")

    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/docker")
    monkeypatch.setattr(subprocess, "run", fake_run)

    rc = shell(install_dir=install, service="traefik")

    out = capsys.readouterr().out
    assert rc == 1
    assert "permission denied" in out
    assert "Traceback" not in out


def test_logs_can_run_docker_compose_via_sudo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install = _make_install_dir(tmp_path)
    captured: dict[str, object] = {}

    def fake_run(cmd: list[str], **kw: object) -> object:
        captured["cmd"] = cmd
        captured["cwd"] = kw.get("cwd")
        captured["input"] = kw.get("input")

        class P:
            returncode = 0

        return P()

    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/docker")
    monkeypatch.setattr(subprocess, "run", fake_run)

    rc = logs(install_dir=install, service="traefik", tail=42, sudo_password="pw")

    assert rc == 0
    assert captured["cmd"] == [
        "sudo",
        "-S",
        "-p",
        "",
        "--",
        "docker",
        "compose",
        "logs",
        "--tail=42",
        "traefik",
    ]
    assert captured["cwd"] == install
    assert captured["input"] == "pw\n"
    assert "pw" not in captured["cmd"]


def test_shell_can_run_docker_compose_via_sudo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install = _make_install_dir(tmp_path)
    captured: dict[str, object] = {}

    def fake_run(cmd: list[str], **kw: object) -> object:
        captured["cmd"] = cmd
        captured["cwd"] = kw.get("cwd")
        captured["input"] = kw.get("input")

        class P:
            returncode = 0

        return P()

    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/docker")
    monkeypatch.setattr(subprocess, "run", fake_run)

    rc = shell(
        install_dir=install,
        service="traefik",
        cmd=["/bin/bash"],
        workdir="/tmp",
        sudo_password="pw",
    )

    assert rc == 0
    assert captured["cmd"] == [
        "sudo",
        "-S",
        "-p",
        "",
        "--",
        "docker",
        "compose",
        "exec",
        "-w",
        "/tmp",
        "traefik",
        "/bin/bash",
    ]
    assert captured["cwd"] == install
    assert captured["input"] == "pw\n"
    assert "pw" not in captured["cmd"]


def test_cmd_logs_prompts_for_sudo_password_when_requested(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agmind.cli import ops_cmd

    captured: dict[str, object] = {}

    def fake_logs(**kwargs: object) -> int:
        captured.update(kwargs)
        return 0

    monkeypatch.setattr(ops_cmd, "do_logs", fake_logs)
    monkeypatch.setattr(ops_cmd.getpass, "getpass", lambda prompt: "pw")

    rc = ops_cmd.cmd_logs(
        service="traefik",
        install_dir=tmp_path,
        tail=50,
        follow=False,
        ask_sudo_password=True,
    )

    assert rc == 0
    assert captured["sudo_password"] == "pw"


def test_cmd_shell_prompts_for_sudo_password_when_requested(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agmind.cli import ops_cmd

    captured: dict[str, object] = {}

    def fake_shell(**kwargs: object) -> int:
        captured.update(kwargs)
        return 0

    monkeypatch.setattr(ops_cmd, "do_shell", fake_shell)
    monkeypatch.setattr(ops_cmd.getpass, "getpass", lambda prompt: "pw")

    rc = ops_cmd.cmd_shell(
        service="traefik",
        install_dir=tmp_path,
        cmd=["/bin/sh"],
        workdir=None,
        ask_sudo_password=True,
    )

    assert rc == 0
    assert captured["sudo_password"] == "pw"
