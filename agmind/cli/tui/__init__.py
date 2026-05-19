"""Phase J: Textual TUI для AGmind (interactive wizard вместо CLI flags).

Sub-modules:
    setup_wizard — `agmind setup` (новый сервер / новый клиент)
    status_dashboard — `agmind status --tui` (live deployment view, Phase J.2)
"""

from __future__ import annotations

from agmind.cli.tui.setup_wizard import AgmindSetupApp, run_setup_wizard

__all__ = ["AgmindSetupApp", "run_setup_wizard"]
