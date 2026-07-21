"""SPEC-16.5: opt-in scheduled-backup systemd timer (`agmind ops backup-timer`).

Hermetic: systemctl / sudo / rclone are NOT on this host, so every subprocess is
mocked (`subprocess.run`) and unit files are written into a tmp `--unit-dir`
instead of /etc/systemd/system. No real daemon-reload / enable ever runs.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agmind.cli import _make_app

pytestmark = pytest.mark.backend_any


def _fake_run(calls: list[list[str]]):
    def run(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    return run


def test_backup_timer_install_writes_both_units(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    unit_dir = tmp_path / "systemd"
    calls: list[list[str]] = []
    monkeypatch.setattr(subprocess, "run", _fake_run(calls))

    result = CliRunner().invoke(
        _make_app(),
        [
            "ops",
            "backup-timer",
            "--install",
            "--schedule",
            "daily",
            "--remote",
            "r:bucket",
            "--unit-dir",
            str(unit_dir),
            "--output-dir",
            str(tmp_path / "backups"),
        ],
    )
    assert result.exit_code == 0, result.output

    service = (unit_dir / "agmind-backup.service").read_text(encoding="utf-8")
    timer = (unit_dir / "agmind-backup.timer").read_text(encoding="utf-8")

    # service ExecStart runs the installed CLI backup with the data tier + off-host push.
    # `backup` is a TOP-LEVEL command (`agmind backup`), not `agmind ops backup`, so the
    # ExecStart must resolve as `<bin> backup --include-data ...` — otherwise every fire
    # would die with "No such command 'backup'".
    execstart = next(line for line in service.splitlines() if line.startswith("ExecStart="))
    assert " backup --include-data" in execstart
    assert " ops backup " not in execstart
    assert "--remote r:bucket" in execstart
    # timer carries the schedule + catch-up + timers.target install.
    assert "OnCalendar=daily" in timer
    assert "Persistent=true" in timer
    assert "WantedBy=timers.target" in timer

    flat = [" ".join(c) for c in calls]
    assert any("systemctl daemon-reload" in f for f in flat)
    assert any("systemctl enable --now agmind-backup.timer" in f for f in flat)


def test_backup_timer_install_weekly_schedule(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    unit_dir = tmp_path / "systemd"
    monkeypatch.setattr(subprocess, "run", _fake_run([]))
    result = CliRunner().invoke(
        _make_app(),
        [
            "ops",
            "backup-timer",
            "--install",
            "--schedule",
            "weekly",
            "--unit-dir",
            str(unit_dir),
            "--output-dir",
            str(tmp_path / "b"),
        ],
    )
    assert result.exit_code == 0, result.output
    timer = (unit_dir / "agmind-backup.timer").read_text(encoding="utf-8")
    assert "OnCalendar=weekly" in timer


def test_backup_timer_install_raw_oncalendar_passthrough(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    unit_dir = tmp_path / "systemd"
    monkeypatch.setattr(subprocess, "run", _fake_run([]))
    result = CliRunner().invoke(
        _make_app(),
        [
            "ops",
            "backup-timer",
            "--install",
            "--schedule",
            "*-*-* 03:00:00",
            "--unit-dir",
            str(unit_dir),
            "--output-dir",
            str(tmp_path / "b"),
        ],
    )
    assert result.exit_code == 0, result.output
    timer = (unit_dir / "agmind-backup.timer").read_text(encoding="utf-8")
    assert "OnCalendar=*-*-* 03:00:00" in timer


def test_backup_timer_no_include_data_omits_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    unit_dir = tmp_path / "systemd"
    monkeypatch.setattr(subprocess, "run", _fake_run([]))
    result = CliRunner().invoke(
        _make_app(),
        [
            "ops",
            "backup-timer",
            "--install",
            "--no-include-data",
            "--unit-dir",
            str(unit_dir),
            "--output-dir",
            str(tmp_path / "b"),
        ],
    )
    assert result.exit_code == 0, result.output
    service = (unit_dir / "agmind-backup.service").read_text(encoding="utf-8")
    execstart = next(line for line in service.splitlines() if line.startswith("ExecStart="))
    assert " backup " in execstart
    assert "--include-data" not in execstart


def test_backup_timer_uninstall_disables_and_removes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    unit_dir = tmp_path / "systemd"
    unit_dir.mkdir()
    (unit_dir / "agmind-backup.service").write_text("stale", encoding="utf-8")
    (unit_dir / "agmind-backup.timer").write_text("stale", encoding="utf-8")
    calls: list[list[str]] = []
    monkeypatch.setattr(subprocess, "run", _fake_run(calls))

    result = CliRunner().invoke(
        _make_app(),
        ["ops", "backup-timer", "--uninstall", "--unit-dir", str(unit_dir)],
    )
    assert result.exit_code == 0, result.output
    assert not (unit_dir / "agmind-backup.service").exists()
    assert not (unit_dir / "agmind-backup.timer").exists()

    flat = [" ".join(c) for c in calls]
    assert any("systemctl disable --now agmind-backup.timer" in f for f in flat)
    assert any("systemctl daemon-reload" in f for f in flat)


def test_backup_timer_requires_exactly_one_action(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(subprocess, "run", _fake_run([]))
    # neither flag
    neither = CliRunner().invoke(_make_app(), ["ops", "backup-timer", "--unit-dir", str(tmp_path)])
    assert neither.exit_code == 2, neither.output
    # both flags
    both = CliRunner().invoke(
        _make_app(),
        ["ops", "backup-timer", "--install", "--uninstall", "--unit-dir", str(tmp_path)],
    )
    assert both.exit_code == 2, both.output


def test_backup_timer_exposes_flags() -> None:
    """backup-timer must expose its operator flags. Introspect the click params rather than
    parsing rendered --help text: typer 0.26 rich-wraps option names by terminal width, so a
    substring check on help output is CI-terminal-dependent (same trap as
    test_uninstall_force_help_distinct_from_upgrade_force, c9b642f — see CLAUDE.md journal)."""
    import typer

    group = typer.main.get_command(_make_app())
    timer_cmd = group.commands["ops"].commands["backup-timer"]  # type: ignore[attr-defined]
    declared = {opt for param in timer_cmd.params for opt in param.opts}
    for flag in (
        "--install",
        "--uninstall",
        "--schedule",
        "--remote",
        "--include-data",
        "--ask-sudo-password",
    ):
        assert flag in declared, f"missing {flag} among backup-timer options: {sorted(declared)}"
