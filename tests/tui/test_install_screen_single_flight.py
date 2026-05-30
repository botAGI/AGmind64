"""Race fix: InstallProgressScreen must be single-flight.

A failed deploy left the install worker blocked in a non-cancellable
``subprocess.run(["docker","compose","up",...])``. Textual's
``@work(exclusive=True, thread=True)`` cannot force-kill a running thread, so a
re-entrant ``action_retry`` (or a double dispatch) started a SECOND
``docker compose up`` on the same project while the first was still in flight —
two concurrent ``up``s raced to create the same container names
(``Conflict. The container name "/agmind-watchtower" is already in use``).

The screen must refuse to dispatch a new run while one is active: a single-flight
guard, not the false-safety of ``exclusive=True`` on a blocking thread worker.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agmind.cli.tui.install_screen import InstallProgressScreen
from agmind.install.orchestrator import InstallConfig, InstallResult
from agmind.install.steps import default_steps

pytestmark = pytest.mark.backend_any


def _cfg(tmp_path: Path) -> InstallConfig:
    return InstallConfig(
        domain="lab.example.com",
        cf_api_token="t" * 40,
        services=["llama-llm"],
        install_dir=tmp_path / "opt",
    )


def test_begin_run_is_single_flight(tmp_path: Path) -> None:
    """First dispatch is allowed; a second while active is refused; allowed again
    once the run ends."""
    screen = InstallProgressScreen(_cfg(tmp_path), default_steps())
    assert screen._run_active is False
    assert screen._begin_run() is True
    assert screen._run_active is True
    # Re-entry while a run is in flight must be refused.
    assert screen._begin_run() is False
    assert screen._run_active is True
    # After the run finishes, a fresh dispatch is allowed.
    screen._end_run()
    assert screen._run_active is False
    assert screen._begin_run() is True


def test_action_retry_refused_while_run_active_does_not_touch_widgets(tmp_path: Path) -> None:
    """While a run is active, action_retry must early-return WITHOUT preparing a
    retry or touching widgets (no app is running here — widget access would raise).
    This is the guard that stops a second concurrent `docker compose up`."""
    screen = InstallProgressScreen(_cfg(tmp_path), default_steps())
    screen._run_active = True
    failed = InstallResult(success=False, steps=(), message="boom")
    screen.result = failed
    screen.cancel_event.set()  # a still-running worker's watchdog flag

    # Must not raise (no widget access), must not reset run state.
    screen.action_retry()

    assert screen.result is failed, "retry must not reset result while a run is active"
    assert screen.cancel_event.is_set(), "retry must not clear cancel_event while active"
    assert screen._run_active is True


def test_finalize_ends_the_run(tmp_path: Path) -> None:
    """_finalize must clear the single-flight flag so the next retry is allowed."""
    screen = InstallProgressScreen(_cfg(tmp_path), default_steps())
    screen._run_active = True
    # Not mounted (no app) -> _finalize returns early, but must still end the run.
    screen._finalize()
    assert screen._run_active is False
