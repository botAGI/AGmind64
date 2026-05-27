"""Tests for the manual root-owned backup/restore smoke helper."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.backend_any

REPO_ROOT = Path(__file__).resolve().parents[2]
SMOKE_SCRIPT = REPO_ROOT / "scripts" / "proof" / "root_owned_backup_smoke.py"


def test_root_owned_backup_smoke_dry_run_uses_tmp_only() -> None:
    result = subprocess.run(
        [sys.executable, str(SMOKE_SCRIPT), "--dry-run"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    output = result.stdout + result.stderr
    assert "root-owned backup smoke dry-run" in output
    assert "/tmp/agmind-root-owned-smoke" in output
    assert "/opt/agmind" not in output
    assert "/var/lib/agmind" not in output


def test_root_owned_backup_smoke_rejects_non_tmp_root() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(SMOKE_SCRIPT),
            "--dry-run",
            "--root",
            "/opt/agmind-root-owned-smoke",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "must be under /tmp" in result.stderr


def test_root_owned_backup_smoke_rejects_tmp_itself_as_root() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(SMOKE_SCRIPT),
            "--dry-run",
            "--root",
            "/tmp",
        ],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert "must be a dedicated child under /tmp" in result.stderr


def test_root_owned_backup_smoke_reports_aborted_password_prompt(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from agmind.ops import root_owned_backup_smoke as smoke

    monkeypatch.setattr(smoke.shutil, "which", lambda _name: "/usr/bin/sudo")
    monkeypatch.setattr(smoke.getpass, "getpass", lambda _prompt: (_ for _ in ()).throw(EOFError))

    rc = smoke.main(["--root", "/tmp/agmind-root-owned-smoke", "--output", "/tmp/a.tar.gz"])

    assert rc == 2
    err = capsys.readouterr().err
    assert "sudo password" in err
    assert "Traceback" not in err


def test_sudo_install_text_stages_payload_outside_target_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agmind.ops import root_owned_backup_smoke as smoke

    target = tmp_path / "root-owned" / "install" / ".env"
    calls: list[list[str]] = []

    def fake_sudo_run(cmd: list[str], sudo_password: str) -> None:
        calls.append(cmd)

    monkeypatch.setattr(smoke, "_sudo_run", fake_sudo_run)

    smoke._sudo_install_text(target, "SECRET=value\n", "0600", "pw")

    assert calls == [["install", "-D", "-m", "0600", calls[0][-2], str(target)]]
    staged = Path(calls[0][-2])
    with pytest.raises(ValueError):
        staged.relative_to(target.parent)
    assert not staged.exists()


def test_cli_wrapper_passes_smoke_options(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from agmind.cli import ops_cmd
    from agmind.ops import root_owned_backup_smoke

    captured: dict[str, list[str] | None] = {}

    def fake_main(argv: list[str] | None = None) -> int:
        captured["argv"] = argv
        return 7

    monkeypatch.setattr(root_owned_backup_smoke, "main", fake_main)

    rc = ops_cmd.cmd_root_owned_backup_smoke(
        root=tmp_path / "root",
        output=tmp_path / "backup.tar.gz",
        dry_run=True,
        keep=True,
    )

    assert rc == 7
    assert captured["argv"] == [
        "--root",
        str(tmp_path / "root"),
        "--output",
        str(tmp_path / "backup.tar.gz"),
        "--dry-run",
        "--keep",
    ]
