"""Tests for the final install step that persists ``credentials.txt`` (chmod 600) from the
service descriptors + the rendered ``.env``. Backs the operator's post-install access info.
"""

from __future__ import annotations

import stat
from pathlib import Path

import pytest

from agmind.install.orchestrator import InstallConfig
from agmind.install.steps import CredentialsStep, default_steps

pytestmark = pytest.mark.backend_any


def _cfg(tmp_path: Path, services: list[str]) -> InstallConfig:
    return InstallConfig(
        domain="lab.test",
        cf_api_token="x" * 20,
        services=services,
        install_dir=tmp_path,
        model_file="Qwen3.6-35B.gguf",
    )


def test_credentials_step_writes_chmod_600_file(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text("GRAFANA_PASSWORD=topsecret\n", encoding="utf-8")
    res = CredentialsStep().run(lambda _e: None, _cfg(tmp_path, ["grafana", "llama-llm"]))
    assert res.success

    creds = tmp_path / "credentials.txt"
    assert creds.exists()
    assert stat.S_IMODE(creds.stat().st_mode) == 0o600

    text = creds.read_text(encoding="utf-8")
    assert "grafana" in text
    assert "admin" in text
    assert "topsecret" in text  # this IS the secrets file
    # domain is substituted (descriptor host agmind.dev → install domain lab.test)
    assert "https://grafana.lab.test" in text
    assert "https://llama.lab.test/v1" in text  # model endpoint block
    assert "agmind.dev" not in text
    assert "Qwen3.6-35B.gguf" in text


def test_credentials_step_nonfatal_without_env(tmp_path: Path) -> None:
    # No .env present → password unknown, but the file is still written (best-effort).
    res = CredentialsStep().run(lambda _e: None, _cfg(tmp_path, ["grafana"]))
    assert res.success
    creds = tmp_path / "credentials.txt"
    assert creds.exists()
    text = creds.read_text(encoding="utf-8")
    assert "GRAFANA_PASSWORD" in text  # fallback: point at the env var


def test_credentials_step_reads_root_owned_env_via_sudo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Surfaced by a live deploy: /opt/agmind/.env is root:root 0600 (sudo-written), so a
    non-root install user can't read it directly — CredentialsStep must fall back to `sudo cat`,
    not silently skip and leave the operator with no password record."""
    import subprocess as sp

    from agmind.install import steps

    (tmp_path / ".env").write_text(
        "blocked\n", encoding="utf-8"
    )  # exists() → True; direct read denied
    cfg = InstallConfig(
        domain="lab.test",
        cf_api_token="x" * 20,
        services=["grafana"],
        install_dir=tmp_path,
        model_file="m.gguf",
        sudo_password="pw",
    )

    def deny_direct(_path: Path) -> dict[str, str]:
        raise PermissionError("Permission denied: .env")

    monkeypatch.setattr(steps, "parse_env_file", deny_direct)

    def fake_sudo(cmd: list[str], **kw: object) -> sp.CompletedProcess:
        assert cmd[:2] == ["sudo", "-S"] and cmd[-2:] == ["cat", str(tmp_path / ".env")]
        return sp.CompletedProcess(cmd, 0, stdout="GRAFANA_PASSWORD=sudosecret\n", stderr="")

    monkeypatch.setattr(steps.subprocess, "run", fake_sudo)

    res = CredentialsStep().run(lambda _e: None, cfg)
    assert res.success
    creds = (tmp_path / "credentials.txt").read_text(encoding="utf-8")
    assert "sudosecret" in creds, "credentials.txt must contain the sudo-read password, not skip"


def test_credentials_step_is_final_in_default_pipeline() -> None:
    assert [s.step_id for s in default_steps()][-1] == "credentials"


def test_credentials_step_writes_via_sudo_when_dir_not_writable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """live-audit 2026-06-05 (credentials-txt-write-no-sudo-path): /opt/agmind is owned by the
    agmind user but the install ran as a different non-root user, so write_private_text could not
    mkstemp inside it and credentials.txt was silently skipped. CredentialsStep must stage the
    file and place it via the sudo helper (mirroring how .env is written)."""
    from agmind.install import steps

    install_dir = tmp_path / "opt"
    install_dir.mkdir()
    (install_dir / ".env").write_text("GRAFANA_PASSWORD=topsecret\n", encoding="utf-8")
    cfg = InstallConfig(
        domain="lab.test",
        cf_api_token="x" * 20,
        services=["grafana"],
        install_dir=install_dir,
        model_file="m.gguf",
        sudo_password="pw",
    )

    def deny_direct(_p: Path, _c: str) -> None:
        raise PermissionError("[Errno 13] Permission denied: /opt/agmind")

    monkeypatch.setattr(steps, "write_private_text", deny_direct)

    placed: dict[str, str] = {}

    def fake_sudo(_config: object, cmd: list[str], _cb: object, _sid: object) -> None:
        assert cmd[0] == "install"
        assert cmd[-1] == str(install_dir / "credentials.txt")
        placed["content"] = Path(cmd[-2]).read_text(encoding="utf-8")
        Path(cmd[-1]).write_text(placed["content"], encoding="utf-8")

    monkeypatch.setattr(steps, "_run_sudo_runtime_command", fake_sudo)

    res = CredentialsStep().run(lambda _e: None, cfg)
    assert res.success, res.message
    creds = install_dir / "credentials.txt"
    assert creds.exists(), "credentials.txt must be placed via sudo, not silently skipped"
    assert "topsecret" in creds.read_text(encoding="utf-8")
    assert "topsecret" in placed["content"]
