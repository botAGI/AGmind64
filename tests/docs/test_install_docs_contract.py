"""Install docs must match the live setup/install contracts."""

from __future__ import annotations

from pathlib import Path

import pytest

from agmind.cli.tui.setup_wizard import STATE_PATH

pytestmark = pytest.mark.backend_any

ROOT = Path(__file__).resolve().parents[2]


def test_setup_state_path_docs_match_code() -> None:
    expected = "~/" + str(STATE_PATH.relative_to(Path.home()))
    docs = [
        ROOT / "agmind" / "cli" / "tui" / "setup_wizard.py",
        ROOT / "docs" / "examples" / "README.md",
    ]

    for path in docs:
        text = path.read_text(encoding="utf-8")
        assert expected in text
        assert "/var/lib/agmind/setup-state.json" not in text


def test_readmes_do_not_claim_full_profile_is_blocked() -> None:
    for name in ("README.md", "README.ru.md"):
        text = (ROOT / name).read_text(encoding="utf-8").lower()
        assert "full` profile is intentionally blocked" not in text
        assert "профиль `full` намеренно заблокирован" not in text
