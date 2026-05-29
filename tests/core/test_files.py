"""Tests for agmind.core.files.write_text_atomic."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from agmind.core import files as files_mod
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
    # No leftover temp matching the target's unique-temp pattern (mkstemp uses a
    # random suffix, so assert by glob rather than a fixed `.secret.env.tmp` name).
    assert not list(tmp_path.glob(".secret.env.*"))


def test_write_text_atomic_two_writes_do_not_collide(tmp_path: Path) -> None:
    target = tmp_path / "state.json"

    # Two sequential writes to the SAME target must both succeed; a unique temp
    # name (mkstemp) never collides with a stale/in-flight temp, and no temp is
    # left behind once the atomic replace completes.
    write_text_atomic(target, "first", mode=0o600)
    assert target.read_text() == "first"
    write_text_atomic(target, "second", mode=0o600)
    assert target.read_text() == "second"

    assert _mode(target) == 0o600
    assert not list(tmp_path.glob(".state.json.*"))


def test_write_text_atomic_content_correctness(tmp_path: Path) -> None:
    target = tmp_path / "data.txt"

    # New file.
    write_text_atomic(target, "hello\nworld\n")
    assert target.read_text() == "hello\nworld\n"

    # Overwrite an existing file.
    write_text_atomic(target, "replaced")
    assert target.read_text() == "replaced"

    # No leftover temp for the unique-name writer.
    assert not list(tmp_path.glob(".data.txt.*"))


def test_write_text_atomic_fsyncs_temp_fd_and_parent_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "durable.txt"

    fsynced_fds: list[int] = []
    real_fsync = os.fsync

    def recording_fsync(fd: int) -> None:
        fsynced_fds.append(fd)
        real_fsync(fd)

    monkeypatch.setattr(files_mod.os, "fsync", recording_fsync)

    write_text_atomic(target, "durable content")

    assert target.read_text() == "durable content"
    # Durability contract: at least the temp fd AND the parent-dir fd are
    # fsync'd (two distinct fsync calls), so a crash after replace cannot leave
    # truncated/half-durable state.
    assert len(fsynced_fds) >= 2
    assert not list(tmp_path.glob(".durable.txt.*"))


def test_write_text_atomic_inherits_existing_target_mode(tmp_path: Path) -> None:
    target = tmp_path / "creds"
    write_text_atomic(target, "a", mode=0o600)
    write_text_atomic(target, "b")  # no explicit mode -> inherit 0o600

    assert target.read_text() == "b"
    assert _mode(target) == 0o600
