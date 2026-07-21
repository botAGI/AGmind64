"""SPEC-16.5: off-host backup push via rclone (agmind.ops.offhost).

Hermetic by construction: every test mocks shutil.which (binary resolution) and
subprocess.run (the invocation), so the real rclone binary is never touched even
on a host that happens to have it — exactly as the k6 load-test wrapper tests
never touch k6. Do not relax this into calling the real binary; the unit tier
must stay green on a machine without rclone.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from agmind.ops import offhost
from agmind.ops.offhost import (
    RCLONE_REMOTE_ENV,
    OffHostPushError,
    push_backup,
    resolve_remote,
)

pytestmark = pytest.mark.backend_any


def _archive(tmp_path: Path) -> Path:
    p = tmp_path / "agmind-2026-07-21.tar.gz"
    p.write_bytes(b"fake-archive")
    return p


def test_push_backup_invokes_rclone_copyto(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    archive = _archive(tmp_path)
    monkeypatch.setattr(offhost.shutil, "which", lambda _name: "/usr/bin/rclone")
    captured: dict[str, list[str]] = {}

    def fake_run(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        captured["argv"] = list(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(offhost.subprocess, "run", fake_run)

    target = push_backup(archive, "s3:agmind-backups/host1")

    # copyto goes to a destination FILE path (remote + basename), not a dir.
    assert target == f"s3:agmind-backups/host1/{archive.name}"
    argv = captured["argv"]
    assert argv[0] == "/usr/bin/rclone"
    assert argv[1] == "copyto"
    assert argv[2] == str(archive)
    assert argv[3] == f"s3:agmind-backups/host1/{archive.name}"


def test_push_backup_strips_trailing_slash_on_remote(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = _archive(tmp_path)
    monkeypatch.setattr(offhost.shutil, "which", lambda _name: "/usr/bin/rclone")
    captured: dict[str, list[str]] = {}

    def fake_run(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        captured["argv"] = list(argv)
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(offhost.subprocess, "run", fake_run)
    target = push_backup(archive, "s3:bucket/")
    assert target == f"s3:bucket/{archive.name}"
    assert captured["argv"][3] == f"s3:bucket/{archive.name}"


def test_push_backup_missing_rclone_raises_clean_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = _archive(tmp_path)
    monkeypatch.setattr(offhost.shutil, "which", lambda _name: None)

    def must_not_run(*_a: object, **_k: object) -> subprocess.CompletedProcess[str]:
        raise AssertionError("subprocess.run must not be called when rclone is missing")

    monkeypatch.setattr(offhost.subprocess, "run", must_not_run)

    with pytest.raises(OffHostPushError) as excinfo:
        push_backup(archive, "s3:bucket")
    assert "rclone is not installed" in str(excinfo.value)


def test_push_backup_nonzero_rc_raises_with_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive = _archive(tmp_path)
    monkeypatch.setattr(offhost.shutil, "which", lambda _name: "/usr/bin/rclone")

    def fake_run(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(argv, 1, stdout="", stderr="permission denied")

    monkeypatch.setattr(offhost.subprocess, "run", fake_run)
    with pytest.raises(OffHostPushError) as excinfo:
        push_backup(archive, "s3:bucket")
    assert "permission denied" in str(excinfo.value)
    assert "rc=1" in str(excinfo.value)


def test_push_backup_no_remote_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    archive = _archive(tmp_path)
    monkeypatch.setattr(offhost.shutil, "which", lambda _name: "/usr/bin/rclone")
    with pytest.raises(OffHostPushError):
        push_backup(archive, "")


def test_resolve_remote_explicit_wins() -> None:
    assert resolve_remote("s3:explicit", env={RCLONE_REMOTE_ENV: "s3:fromenv"}) == "s3:explicit"


def test_resolve_remote_from_env_mapping() -> None:
    assert resolve_remote(None, env={RCLONE_REMOTE_ENV: "s3:fromenv"}) == "s3:fromenv"


def test_resolve_remote_from_dotenv_file(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text(f"{RCLONE_REMOTE_ENV}=s3:fromfile\n", encoding="utf-8")
    assert resolve_remote(None, install_dir=tmp_path) == "s3:fromfile"


def test_resolve_remote_none_when_unset() -> None:
    assert resolve_remote(None, env={}) is None
    assert resolve_remote("   ", env={}) is None


def _fake_backup_result(output_path: Path) -> object:
    from agmind.ops.backup import BackupResult

    return BackupResult(
        output_path=Path(output_path),
        bytes_written=64,
        sources_included=("compose", "env"),
        sources_missing=(),
    )


def test_cmd_backup_warns_remote_perms_when_pushing_unencrypted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """live-run 2026-07-21: a 0600 local archive landed 0644 on the remote — rclone applies
    the remote's permission model, not the local mode. An UNENCRYPTED push must say so."""
    from typer.testing import CliRunner

    from agmind.cli import _make_app, ops_cmd

    out = tmp_path / "backup.tar.gz"
    monkeypatch.setattr(
        ops_cmd,
        "create_backup",
        lambda output_path, sudo_password=None, **_k: _fake_backup_result(output_path),
    )
    monkeypatch.setattr(ops_cmd, "push_backup", lambda _archive, remote: f"{remote}/backup.tar.gz")

    result = CliRunner().invoke(
        _make_app(), ["backup", "-o", str(out), "--remote", "offsite:bucket"]
    )

    assert result.exit_code == 0, result.output
    assert "does NOT inherit the local 0600 mode" in result.output
    assert "--encrypt" in result.output


def test_cmd_backup_no_remote_perms_warning_when_encrypted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An age-encrypted archive is safe at rest regardless of remote permissions — no warning."""
    from typer.testing import CliRunner

    from agmind.cli import _make_app, ops_cmd

    out = tmp_path / "backup.tar.gz"
    monkeypatch.setattr(
        ops_cmd,
        "create_backup",
        lambda output_path, sudo_password=None, **_k: _fake_backup_result(
            Path(str(output_path) + ".age")
        ),
    )
    monkeypatch.setattr(ops_cmd, "push_backup", lambda _archive, remote: f"{remote}/backup.tar.gz")

    result = CliRunner().invoke(
        _make_app(),
        [
            "backup",
            "-o",
            str(out),
            "--remote",
            "offsite:bucket",
            "--encrypt",
            "--age-recipient",
            "age1xyz",
        ],
    )

    assert result.exit_code == 0, result.output
    assert "does NOT inherit the local 0600 mode" not in result.output
