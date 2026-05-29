"""Freeze fix: InstallProgressScreen must signal cancellation.

The install runs in a Textual thread-worker that blocks in a subprocess. Textual
cannot force-kill a thread worker, so on Cancel/Close/exit it WAITS for the worker —
freezing the TUI/VS Code (up to ~300s) unless the running child is killed. The screen
owns a ``cancel_event`` that the orchestrator/steps watch (a daemon watchdog kills the
child when it fires); the screen must set that event on cancel and on unmount so the
worker unblocks and the app can exit promptly.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from agmind.cli.tui.install_screen import InstallProgressScreen
from agmind.install.orchestrator import InstallConfig
from agmind.install.steps import default_steps

pytestmark = pytest.mark.backend_any


def _cfg(tmp_path: Path) -> InstallConfig:
    return InstallConfig(
        domain="lab.example.com",
        cf_api_token="t" * 40,
        services=["llama-llm"],
        install_dir=tmp_path / "opt",
    )


def test_install_screen_owns_a_cancel_event(tmp_path: Path) -> None:
    screen = InstallProgressScreen(_cfg(tmp_path), default_steps())
    assert isinstance(screen.cancel_event, threading.Event)
    assert not screen.cancel_event.is_set()


def test_install_screen_unmount_signals_cancellation(tmp_path: Path) -> None:
    """Tearing down the screen (Cancel/Close/exit) must set the cancel_event so a
    still-running subprocess is killed and the worker thread unblocks."""
    screen = InstallProgressScreen(_cfg(tmp_path), default_steps())
    assert not screen.cancel_event.is_set()
    screen.on_unmount()
    assert screen.cancel_event.is_set()
