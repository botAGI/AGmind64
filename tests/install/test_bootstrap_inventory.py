"""Regression guard: the install BootstrapStep must target the repo inventory.

The bootstrap play in ansible/install.yml is `hosts: agmind_nodes`. Invoking it
with a bare `-i localhost,` puts localhost only in the implicit `all` group, so
the play matches ZERO hosts and silently no-ops EVERY bootstrap task — including
the per-service data-dir chown — leaving /var/lib/agmind/* root:root and the
non-root images crash-looping. The fix routes it through inventory/hosts.yml
(localhost ∈ agmind_nodes + the agmind_* vars).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.backend_any

_ROOT = Path(__file__).resolve().parent.parent.parent
_STEPS = _ROOT / "agmind" / "install" / "steps.py"
_INSTALL_YML = _ROOT / "ansible" / "install.yml"


def test_bootstrap_step_uses_repo_inventory_not_bare_localhost() -> None:
    src = _STEPS.read_text(encoding="utf-8")
    assert '"inventory/hosts.yml"' in src, (
        "BootstrapStep must run ansible with -i inventory/hosts.yml so the "
        "agmind_nodes bootstrap play matches localhost"
    )
    assert '"localhost,"' not in src, (
        "bare `-i localhost,` makes the agmind_nodes bootstrap play match zero "
        "hosts -> data-dir chown silently no-ops -> root:root crash-loops"
    )


def test_bootstrap_play_targets_agmind_nodes() -> None:
    plays = yaml.safe_load(_INSTALL_YML.read_text(encoding="utf-8"))
    bootstrap_plays = [
        p
        for p in plays
        if any(
            (r.get("role") if isinstance(r, dict) else r) == "bootstrap"
            for r in (p.get("roles") or [])
        )
    ]
    assert bootstrap_plays, "no play applies the bootstrap role"
    for p in bootstrap_plays:
        assert p.get("hosts") == "agmind_nodes", (
            f"bootstrap play must target agmind_nodes (got {p.get('hosts')!r}); "
            "the BootstrapStep inventory must put localhost in that group"
        )
