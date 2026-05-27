"""Tests for agmind.core.files.write_text_atomic."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from agmind.core.files import write_text_atomic

pytestmark = pytest.mark.backend_any


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_write_text_atomic_creates_secret_mode_under_permissive_umask(
    tmp_path: Path,
) -> None:
    old = os.umask(0)  # would make a naive create 0o666 / world-readable
    try:
        target = tmp_path / "secret.env"
        write_text_atomic(target, "POSTGRES_PASSWORD=x\n", mode=0o600)
    finally:
        os.umask(old)

    assert target.read_text() == "POSTGRES_PASSWORD=x\n"
    assert _mode(target) == 0o600
    assert not (tmp_path / ".secret.env.tmp").exists()


def test_write_text_atomic_overwrites_stale_temp(tmp_path: Path) -> None:
    target = tmp_path / "state.json"
    stale = tmp_path / ".state.json.tmp"
    stale.write_text("leftover", encoding="utf-8")  # O_EXCL would fail without cleanup

    write_text_atomic(target, "fresh", mode=0o600)

    assert target.read_text() == "fresh"
    assert not stale.exists()


def test_write_text_atomic_inherits_existing_target_mode(tmp_path: Path) -> None:
    target = tmp_path / "creds"
    write_text_atomic(target, "a", mode=0o600)
    write_text_atomic(target, "b")  # no explicit mode -> inherit 0o600

    assert target.read_text() == "b"
    assert _mode(target) == 0o600
