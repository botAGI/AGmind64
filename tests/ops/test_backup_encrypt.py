"""SPEC-17.4: opt-in at-rest encryption of a backup archive with ``age``.

Hermetic: ``age`` is NOT on this host, so every test mocks ``shutil.which`` (binary
resolution) and ``subprocess.run`` (the invocation) — the real ``age`` binary is never
touched, exactly as the rclone off-host-push tests never touch rclone and the k6
load-test tests never touch k6.
"""

from __future__ import annotations

import stat
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agmind.ops import backup
from agmind.ops.backup import (
    BackupEncryptError,
    BackupResult,
    BackupSource,
    create_backup,
)

pytestmark = pytest.mark.backend_any

_RECIPIENT = "age1qxfakerecipient000000000000000000000000000000000000000000"


def _env_source(tmp_path: Path) -> tuple[Path, list[BackupSource]]:
    """A minimal one-file source set (an ``.env``) + the intended output path."""
    install = tmp_path / "opt"
    install.mkdir()
    (install / ".env").write_text("SECRET=abc\n", encoding="utf-8")
    out = tmp_path / "backup.tar.gz"
    return out, [BackupSource("env", install / ".env")]


def _age_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        backup.shutil, "which", lambda name: "/usr/bin/age" if name == "age" else None
    )


# ---------- create_backup(encrypt=...) ----------


def test_create_backup_encrypt_invokes_age_and_produces_age_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out, sources = _env_source(tmp_path)
    _age_present(monkeypatch)
    captured: dict[str, list[str]] = {}

    def fake_run(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        captured["argv"] = list(argv)
        # Simulate age writing its ``-o`` output file so the atomic replace can proceed.
        o_target = Path(argv[argv.index("-o") + 1])
        o_target.write_bytes(b"age-encrypted-payload")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(backup.subprocess, "run", fake_run)

    result = create_backup(
        output_path=out,
        sources=sources,
        encrypt=True,
        age_recipient=_RECIPIENT,
    )

    argv = captured["argv"]
    assert argv[0] == "/usr/bin/age"
    assert "-r" in argv
    assert argv[argv.index("-r") + 1] == _RECIPIENT
    # Final artifact is <output>.age (0600); the plaintext archive is gone.
    encrypted = out.with_name(out.name + ".age")
    assert result.output_path == encrypted
    assert encrypted.exists()
    assert not out.exists()
    assert stat.S_IMODE(encrypted.stat().st_mode) == 0o600
    assert result.bytes_written == encrypted.stat().st_size


def test_create_backup_encrypt_age_missing_raises_clean_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out, sources = _env_source(tmp_path)
    monkeypatch.setattr(backup.shutil, "which", lambda _name: None)

    def must_not_run(*_a: object, **_k: object) -> subprocess.CompletedProcess[str]:
        raise AssertionError("subprocess.run must not be called when age is missing")

    monkeypatch.setattr(backup.subprocess, "run", must_not_run)

    with pytest.raises(BackupEncryptError) as excinfo:
        create_backup(output_path=out, sources=sources, encrypt=True, age_recipient=_RECIPIENT)
    assert "age not found" in str(excinfo.value)
    # Fail-fast BEFORE building: no plaintext archive is ever written.
    assert not out.exists()


def test_create_backup_encrypt_without_recipient_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out, sources = _env_source(tmp_path)
    _age_present(monkeypatch)
    with pytest.raises(BackupEncryptError) as excinfo:
        create_backup(output_path=out, sources=sources, encrypt=True, age_recipient=None)
    assert "recipient" in str(excinfo.value)
    assert not out.exists()


def test_create_backup_encrypt_age_nonzero_rc_raises_and_leaves_no_plaintext(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out, sources = _env_source(tmp_path)
    _age_present(monkeypatch)

    def fake_run(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 1, stdout="", stderr="bad recipient")

    monkeypatch.setattr(backup.subprocess, "run", fake_run)

    with pytest.raises(BackupEncryptError) as excinfo:
        create_backup(output_path=out, sources=sources, encrypt=True, age_recipient=_RECIPIENT)
    assert "bad recipient" in str(excinfo.value)
    assert "rc=1" in str(excinfo.value)
    # Fail-closed: neither the plaintext archive nor a partial .age is left behind.
    assert not out.exists()
    assert not out.with_name(out.name + ".age").exists()


def test_create_backup_without_encrypt_never_invokes_age(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out, sources = _env_source(tmp_path)

    def must_not_run(*_a: object, **_k: object) -> subprocess.CompletedProcess[str]:
        raise AssertionError("subprocess.run (age) must not run without --encrypt")

    monkeypatch.setattr(backup.subprocess, "run", must_not_run)

    result = create_backup(output_path=out, sources=sources)
    # Plaintext archive is the artifact; no .age produced.
    assert result.output_path == out
    assert out.exists()
    assert not out.with_name(out.name + ".age").exists()


# ---------- cmd_backup: .env-plaintext warning + age-missing clean exit ----------


def test_cmd_backup_warns_env_plaintext_when_env_in_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agmind.cli import _make_app, ops_cmd

    out = tmp_path / "backup.tar.gz"

    def fake_create_backup(
        output_path: Path, sudo_password: str | None = None, **_kwargs: object
    ) -> BackupResult:
        return BackupResult(
            output_path=Path(output_path),
            bytes_written=64,
            sources_included=("compose", "env"),
            sources_missing=(),
        )

    monkeypatch.setattr(ops_cmd, "create_backup", fake_create_backup)

    result = CliRunner().invoke(_make_app(), ["backup", "-o", str(out)])
    # result.output combines stdout + stderr (the warning prints to stderr).
    assert result.exit_code == 0, result.output
    assert "PLAINTEXT" in result.output
    assert ".env" in result.output


def test_cmd_backup_no_env_plaintext_warning_when_encrypted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from agmind.cli import ops_cmd

    out = tmp_path / "backup.tar.gz"

    def fake_create_backup(
        output_path: Path, sudo_password: str | None = None, **kwargs: object
    ) -> BackupResult:
        # cmd_backup forwards the encrypt knobs only when --encrypt is set.
        assert kwargs.get("encrypt") is True
        assert kwargs.get("age_recipient") == _RECIPIENT
        return BackupResult(
            output_path=Path(str(output_path) + ".age"),
            bytes_written=64,
            sources_included=("compose", "env"),
            sources_missing=(),
        )

    monkeypatch.setattr(ops_cmd, "create_backup", fake_create_backup)

    rc = ops_cmd.cmd_backup(out, encrypt=True, age_recipient=_RECIPIENT)
    assert rc == 0
    err = capsys.readouterr().err
    assert "PLAINTEXT" not in err


def test_cmd_backup_encrypt_age_missing_clean_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from agmind.cli import ops_cmd

    out = tmp_path / "backup.tar.gz"

    def fake_create_backup(
        output_path: Path, sudo_password: str | None = None, **_kwargs: object
    ) -> BackupResult:
        raise BackupEncryptError(
            "age not found — install age (https://github.com/FiloSottile/age) or drop --encrypt."
        )

    monkeypatch.setattr(ops_cmd, "create_backup", fake_create_backup)

    rc = ops_cmd.cmd_backup(out, encrypt=True, age_recipient=_RECIPIENT)
    assert rc == 1
    err = capsys.readouterr().err
    assert "age not found" in err
    assert "Traceback" not in err
