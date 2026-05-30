"""Structural backstop: deploy() must be single-flight across processes.

The /agmind-watchtower name conflict came from two concurrent `docker compose up`
on the same project. The TUI guard (install_screen single-flight) stops in-app
re-entry, but a second process — e.g. `agmind deploy` run in another terminal while
the installer is mid-deploy — could still race. An advisory flock around the apply
critical section makes a concurrent apply structurally impossible.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agmind.deploy import runner

pytestmark = pytest.mark.backend_any


def test_deploy_lock_is_single_flight(tmp_path: Path) -> None:
    with runner._deploy_lock(tmp_path) as first:
        assert first is True
        # A second holder for the SAME install dir must be refused while the first holds it.
        with runner._deploy_lock(tmp_path) as second:
            assert second is False


def test_deploy_lock_released_after_exit(tmp_path: Path) -> None:
    with runner._deploy_lock(tmp_path) as first:
        assert first is True
    # Once released, a fresh acquire succeeds again.
    with runner._deploy_lock(tmp_path) as again:
        assert again is True


def test_deploy_apply_refused_while_lock_held(tmp_path: Path) -> None:
    """An apply must bail out fast with 'already in progress' when another deploy
    holds the lock — before any snapshot/compose mutation."""
    with runner._deploy_lock(tmp_path):
        result = runner.deploy(
            profiles=["core"],
            install_dir=tmp_path,
            apply=True,
            no_prompt=True,
        )
    assert result.success is False
    assert "in progress" in result.message.lower()


def test_deploy_dry_run_not_blocked_by_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A dry-run (apply=False) must NOT be gated by the apply lock — read-only diffs
    can run alongside an in-flight apply."""
    sentinel = runner.DeployResult(success=True, message="dry-run reached impl")

    def fake_impl(**_kwargs: object) -> runner.DeployResult:
        return sentinel

    monkeypatch.setattr(runner, "_deploy_impl", fake_impl)
    with runner._deploy_lock(tmp_path):
        result = runner.deploy(
            profiles=["core"],
            install_dir=tmp_path,
            apply=False,
            no_prompt=True,
        )
    assert result is sentinel
