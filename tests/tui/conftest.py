"""TUI test isolation.

A Textual pilot test that drives the setup wizard to its save/quit handler writes
``setup-state.json`` + ``cf_dns_api_token`` via the module-level ``STATE_PATH`` /
``TOKEN_PATH`` — which point at the operator's REAL ``~/.local/share/agmind``. Left
unisolated, running the suite pollutes the operator's HOME with stale state (the exact
"old artifacts before a fresh deploy" class). Redirect those paths to a tmp dir for
every TUI test so no test can ever touch the real user-state dir.
"""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolate_wizard_user_state(
    monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> None:
    import agmind.cli.tui.setup_wizard as sw

    user_dir: Path = tmp_path_factory.mktemp("agmind-user-state")
    monkeypatch.setattr(sw, "_USER_DATA_DIR", user_dir, raising=False)
    monkeypatch.setattr(sw, "STATE_PATH", user_dir / "setup-state.json", raising=False)
    monkeypatch.setattr(sw, "TOKEN_PATH", user_dir / "cf_dns_api_token", raising=False)
