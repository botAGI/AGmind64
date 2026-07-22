"""Tests for agmind.core.locks.deploy_lock.

Mirrors tests/deploy/test_runner_single_flight.py's single-flight contract
against the relocated lock — the relocation (agmind/deploy/runner.py's
`_deploy_lock` moved here as the public `deploy_lock`, with a re-export
alias left in runner.py) must be behavior-identical.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from agmind.core import locks

pytestmark = pytest.mark.backend_any


def test_deploy_lock_is_single_flight(tmp_path: Path) -> None:
    with locks.deploy_lock(tmp_path) as first:
        assert first is True
        # A second holder for the SAME install dir must be refused while the first holds it.
        with locks.deploy_lock(tmp_path) as second:
            assert second is False


def test_deploy_lock_released_after_exit(tmp_path: Path) -> None:
    with locks.deploy_lock(tmp_path) as first:
        assert first is True
    # Once released, a fresh acquire succeeds again.
    with locks.deploy_lock(tmp_path) as again:
        assert again is True


def test_lock_dir_prefers_xdg_runtime_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When XDG_RUNTIME_DIR is set, the lock must be created under it — not the
    shared, world-writable system temp dir."""
    xdg_dir = tmp_path / "xdg-runtime"
    xdg_dir.mkdir()
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(xdg_dir))
    assert locks._lock_dir() == xdg_dir


def test_lock_dir_fallback_is_stable_across_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When XDG_RUNTIME_DIR is unset (the common CI case), the fallback dir must be
    a STABLE per-uid path — identical across two separate calls in the same
    process — never a fresh mkdtemp() per call. An unstable fallback would make
    the two-call single-flight tests pass for the wrong reason (different lock
    files = no real contention)."""
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    first = locks._lock_dir()
    second = locks._lock_dir()
    assert first == second
    assert first.name == f"agmind-runtime-{os.getuid()}"


def test_deploy_lock_file_mode_is_0600(tmp_path: Path) -> None:
    """The created lock file must be mode 0o600 (owner-only), not 0o666
    (world-writable — lets any local user hold the flock forever)."""
    digest_dir = locks._lock_dir()
    before = {p for p in digest_dir.glob("agmind-deploy-*.lock")}
    with locks.deploy_lock(tmp_path):
        after = {p for p in digest_dir.glob("agmind-deploy-*.lock")}
        created = after - before
        assert created, "expected deploy_lock to create a lock file"
        for lock_file in created:
            mode = stat.S_IMODE(os.stat(lock_file).st_mode)
            assert mode == 0o600, f"expected 0o600, got {oct(mode)}"


def test_deploy_lock_fails_closed_on_eacces(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#24: a lock file we cannot open (EACCES — typically root:root left by a prior `sudo` op)
    must fail CLOSED (yield False → deploy refuses), not silently proceed without single-flight
    and let two concurrent deploys race the same container names."""

    def _eacces(*_a: object, **_k: object) -> int:
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(locks.os, "open", _eacces)
    with locks.deploy_lock(tmp_path) as acquired:
        assert acquired is False


def test_deploy_lock_proceeds_on_lock_infra_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A non-permission lock-infra failure (e.g. ENOSPC, missing runtime dir) is not contention —
    the deploy proceeds rather than being blocked on lock infrastructure."""

    def _enospc(*_a: object, **_k: object) -> int:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(locks.os, "open", _enospc)
    with locks.deploy_lock(tmp_path) as acquired:
        assert acquired is True
