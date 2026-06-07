"""Tests for ``agmind backup-list`` (agmind.ops.backup.list_backups + the CLI cmd)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agmind.cli.ops_cmd import cmd_backup_list
from agmind.ops.backup import (
    BackupSource,
    create_backup,
    list_backups,
)

pytestmark = pytest.mark.backend_any


def _make_archive(
    tmp_path: Path,
    out: Path,
    *,
    domain: str = "example.com",
    extra_bytes: int = 0,
) -> Path:
    """Create a real agmind backup archive at ``out`` with a tiny mock repo."""
    install = tmp_path / f"opt-{out.stem}"
    install.mkdir()
    (install / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    env_text = f"AGMIND_DOMAIN={domain}\n" + ("# pad\n" * extra_bytes)
    (install / ".env").write_text(env_text, encoding="utf-8")
    create_backup(
        output_path=out,
        sources=[
            BackupSource("compose", install / "docker-compose.yml"),
            BackupSource("env", install / ".env"),
        ],
    )
    return out


# ---------- list_backups (ops layer) ----------


def test_list_backups_empty_dir(tmp_path: Path) -> None:
    assert list_backups(tmp_path) == []


def test_list_backups_finds_archives_newest_first(tmp_path: Path) -> None:
    older = _make_archive(tmp_path, tmp_path / "agmind-2026-05-19.tar.gz")
    newer = _make_archive(tmp_path, tmp_path / "agmind-2026-05-20.tar.gz", extra_bytes=50)

    # Force a deterministic mtime ordering (older < newer) so the sort is observable.
    import os

    os.utime(older, (1_700_000_000, 1_700_000_000))
    os.utime(newer, (1_800_000_000, 1_800_000_000))

    entries = list_backups(tmp_path)
    assert [e["name"] for e in entries] == [
        "agmind-2026-05-20.tar.gz",
        "agmind-2026-05-19.tar.gz",
    ]
    first = entries[0]
    assert first["path"] == str(newer)
    assert first["size_bytes"] > 0
    assert first["created_at"]  # ISO timestamp from metadata
    assert "env" in first["included"]
    assert first["ok"] is True


def test_list_backups_marks_corrupt_archive(tmp_path: Path) -> None:
    _make_archive(tmp_path, tmp_path / "good.tar.gz")
    bad = tmp_path / "bad.tar.gz"
    bad.write_bytes(b"not a gzip archive")

    entries = list_backups(tmp_path)
    by_name = {e["name"]: e for e in entries}
    assert by_name["good.tar.gz"]["ok"] is True
    assert by_name["bad.tar.gz"]["ok"] is False
    assert by_name["bad.tar.gz"]["error"]  # non-empty reason
    assert by_name["bad.tar.gz"]["created_at"] is None


# ---------- cmd_backup_list (CLI wrapper) ----------


def test_cmd_backup_list_empty_friendly(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc = cmd_backup_list(directory=tmp_path, as_json=False)
    assert rc == 0
    out = capsys.readouterr().out.lower()
    assert "no backups" in out


def test_cmd_backup_list_json_sorted_newest_first(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    older = _make_archive(tmp_path, tmp_path / "agmind-old.tar.gz")
    newer = _make_archive(tmp_path, tmp_path / "agmind-new.tar.gz", extra_bytes=80)
    import os

    os.utime(older, (1_700_000_000, 1_700_000_000))
    os.utime(newer, (1_800_000_000, 1_800_000_000))

    rc = cmd_backup_list(directory=tmp_path, as_json=True)
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert "backups" in payload
    names = [b["name"] for b in payload["backups"]]
    assert names == ["agmind-new.tar.gz", "agmind-old.tar.gz"]
    top = payload["backups"][0]
    assert top["size_bytes"] > 0
    assert top["created_at"]
    assert "env" in top["included"]
    assert top["ok"] is True


def test_cmd_backup_list_text_shows_name_and_size(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _make_archive(tmp_path, tmp_path / "agmind-2026-05-20.tar.gz")
    rc = cmd_backup_list(directory=tmp_path, as_json=False)
    assert rc == 0
    out = capsys.readouterr().out
    assert "agmind-2026-05-20.tar.gz" in out
    assert "KiB" in out or "MiB" in out or "B" in out


def test_cmd_backup_list_missing_dir(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc = cmd_backup_list(directory=tmp_path / "does-not-exist", as_json=False)
    assert rc == 0
    out = capsys.readouterr().out.lower()
    assert "no backups" in out
