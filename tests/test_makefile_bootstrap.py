"""Guard: the clean-machine bootstrap entry point stays wired.

The first-run flow on a fresh host is `git clone … && cd AGmindx86 && make setup` — the
Makefile must create the .venv, install the agmind CLI into it, and expose setup/install
targets that run it. This guards those targets against accidental removal (there is no global
`agmind` until the install writes one, so the repo IS the bootstrap entry).
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.backend_any

_MAKEFILE = Path(__file__).resolve().parents[1] / "Makefile"


def _makefile() -> str:
    return _MAKEFILE.read_text(encoding="utf-8")


def test_makefile_exposes_bootstrap_setup_install_targets() -> None:
    text = _makefile()
    for target in ("setup:", "install:", "bootstrap:"):
        assert target in text, f"Makefile must define a `{target.rstrip(':')}` target"
    # They must be .PHONY so a stray ./setup file can't shadow them.
    phony_line = next((ln for ln in text.splitlines() if ln.startswith(".PHONY")), "")
    # .PHONY may continue across `\`-joined lines; check the whole declared block.
    phony_block = text.split(".PHONY:", 1)[1].split("\n\n", 1)[0]
    for target in ("setup", "install", "bootstrap"):
        assert target in phony_block, f"`{target}` must be in .PHONY (got: {phony_line[:60]}…)"


def test_bootstrap_recipe_creates_venv_and_installs_agmind() -> None:
    """The venv file-target must create .venv (uv OR python3 -m venv) and `pip install -e .`."""
    text = _makefile()
    # The recipe is the block under the $(VENV_AGMIND) file target.
    assert "$(VENV_AGMIND):" in text, "Makefile must have a $(VENV_AGMIND) file target"
    recipe = text.split("$(VENV_AGMIND):", 1)[1].split("\nbootstrap:", 1)[0]
    assert "uv venv" in recipe or "python3 -m venv" in recipe, "must create a venv"
    assert "-e ." in recipe, "must install the agmind package editable from the checkout"


def test_setup_target_runs_the_venv_cli() -> None:
    text = _makefile()
    setup_recipe = text.split("\nsetup:", 1)[1].split("\n\n", 1)[0]
    assert "$(VENV_AGMIND) setup" in setup_recipe, "make setup must run the venv's agmind setup"
    # It depends on the venv target so a fresh host bootstraps first.
    assert "$(VENV_AGMIND)" in text.split("\nsetup:", 1)[1].split("\n", 1)[0]
