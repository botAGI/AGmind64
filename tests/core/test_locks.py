"""Tests for agmind.core.locks.deploy_lock.

Mirrors tests/deploy/test_runner_single_flight.py's single-flight contract
against the relocated lock — the relocation (agmind/deploy/runner.py's
`_deploy_lock` moved here as the public `deploy_lock`, with a re-export
alias left in runner.py) must be behavior-identical.
"""

from __future__ import annotations

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
