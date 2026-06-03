"""Granular restore: `--dry-run` (read-only plan) and `--label` (selective) for restore.

Scoped to OUR config-category archive (compose/env/descriptors/…), not the parent's
per-service DB/volume slices (we don't store those). dry-run never mutates; a
selective restore leaves non-selected targets untouched; an unknown label hard-errors.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agmind.cli.ops_cmd import cmd_restore
from agmind.ops.backup import BackupSource, PlanRow, create_backup, restore_plan

pytestmark = pytest.mark.backend_any


def _backup(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    install = tmp_path / "opt"
    user = tmp_path / "user"
    system = tmp_path / "system"
    install.mkdir()
    (install / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    (install / ".env").write_text("AGMIND_DOMAIN=example.com\n", encoding="utf-8")
    desc = install / "templates" / "services"
    desc.mkdir(parents=True)
    (desc / "a.yaml").write_text("name: a\n", encoding="utf-8")
    (desc / "b.yaml").write_text("name: b\n", encoding="utf-8")

    out = tmp_path / "backup.tar.gz"
    create_backup(
        output_path=out,
        sources=[
            BackupSource("compose", install / "docker-compose.yml"),
            BackupSource("env", install / ".env"),
            BackupSource("descriptors", desc),
        ],
    )
    return out, install, user, system


def test_restore_plan_is_read_only_and_classifies_members(tmp_path: Path) -> None:
    out, install, user, system = _backup(tmp_path)
    (install / "docker-compose.yml").unlink()  # delete a target

    rows = restore_plan(out, install_dir=install, user_dir=user, system_dir=system)

    assert all(isinstance(r, PlanRow) for r in rows)
    by = {r.label: r for r in rows}
    assert {"compose", "env", "descriptors"} <= set(by)
    assert by["compose"].kind == "file"
    assert by["descriptors"].kind == "dir"
    assert "docker-compose.yml" in by["compose"].target
    # read-only: planning must NOT recreate the deleted target
    assert not (install / "docker-compose.yml").exists()


def test_restore_plan_filters_by_labels(tmp_path: Path) -> None:
    out, install, user, system = _backup(tmp_path)
    rows = restore_plan(
        out, install_dir=install, user_dir=user, system_dir=system, labels=["descriptors"]
    )
    assert [r.label for r in rows] == ["descriptors"]


def test_cmd_restore_dry_run_makes_no_changes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out, install, user, system = _backup(tmp_path)
    (install / ".env").unlink()

    rc = cmd_restore(
        out, yes=True, install_dir=install, user_dir=user, system_dir=system, dry_run=True
    )
    assert rc == 0
    assert not (install / ".env").exists(), "dry-run must not restore anything"
    assert "dry-run" in capsys.readouterr().out.lower()


def test_cmd_restore_selective_label_restores_only_that_label(tmp_path: Path) -> None:
    out, install, user, system = _backup(tmp_path)
    (install / ".env").unlink()
    (install / "docker-compose.yml").unlink()

    rc = cmd_restore(
        out, yes=True, install_dir=install, user_dir=user, system_dir=system, labels=["env"]
    )
    assert rc == 0
    assert (install / ".env").exists(), "selected label must be restored"
    assert not (install / "docker-compose.yml").exists(), "non-selected label must be untouched"


def test_cmd_restore_invalid_label_hard_errors(tmp_path: Path) -> None:
    out, install, user, system = _backup(tmp_path)
    (install / ".env").unlink()

    rc = cmd_restore(
        out, yes=True, install_dir=install, user_dir=user, system_dir=system, labels=["nope"]
    )
    assert rc != 0
    assert not (install / ".env").exists(), "an invalid label must restore nothing"


@pytest.mark.skipif(pytest.importorskip("typer") is None, reason="typer not installed")
def test_restore_cli_accepts_dry_run_and_label() -> None:
    # Behavioral wiring check, robust to rich-rendered help (which wraps by
    # terminal width). A missing backup makes cmd_restore return 2 ("file not
    # found") — which it only reaches if --dry-run/--label are accepted options;
    # an unknown option would be a typer usage error before cmd_restore runs.
    from typer.testing import CliRunner

    from agmind.cli import _make_app

    result = CliRunner().invoke(
        _make_app(),
        ["restore", "/no/such/agmind-backup.tar.gz", "--dry-run", "--label", "env"],
    )
    assert "No such option" not in result.output
    assert result.exit_code == 2
    assert "not found" in result.output.lower()
