"""Guard: ansible.cfg uses a stdout callback that exists in modern ansible-core.

The bootstrap step runs ``ansible-playbook`` with ``ansible/ansible.cfg`` on the
real host. That host ships ansible-core 2.21 and (via our loose ``>=`` galaxy
pins) community.general 13.x. The short callback name ``yaml`` historically
resolved to ``community.general.yaml``, which was REMOVED in community.general
12.0.0::

    [ERROR]: The 'community.general.yaml' callback plugin has been removed. The
    plugin has been superseded by the option `result_format=yaml` in callback
    plugin ansible.builtin.default from ansible-core 2.13 onwards.

So ``stdout_callback = yaml`` makes ansible-playbook abort with rc=1 before any
task runs. The built-in, version-stable equivalent is the ``default`` callback
with ``result_format = yaml`` (ansible-core 2.13+). This test parses ansible.cfg
and asserts that configuration, so a regression back to a removed community.general
callback is caught deterministically (no ansible invocation needed).
"""

from __future__ import annotations

import configparser
from pathlib import Path

import pytest

pytestmark = pytest.mark.backend_any

# tests/ansible/test_ansible_cfg_callback.py -> parents[2] == repo root.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_ANSIBLE_CFG = _REPO_ROOT / "ansible" / "ansible.cfg"

# Short/long callback names that no longer ship in ansible-core + community.general
# (removed in community.general 12.0.0). Selecting any of these aborts ansible-playbook.
_REMOVED_STDOUT_CALLBACKS = {"yaml", "community.general.yaml"}


def _defaults() -> configparser.SectionProxy:
    parser = configparser.ConfigParser()
    read = parser.read(_ANSIBLE_CFG, encoding="utf-8")
    assert read, f"ansible.cfg not found / unreadable: {_ANSIBLE_CFG}"
    assert parser.has_section("defaults"), "ansible.cfg missing [defaults] section"
    return parser["defaults"]


def test_stdout_callback_is_not_a_removed_community_general_plugin() -> None:
    defaults = _defaults()
    callback = (defaults.get("stdout_callback") or "").strip()
    assert callback, "ansible.cfg [defaults] must set stdout_callback"
    assert callback not in _REMOVED_STDOUT_CALLBACKS, (
        f"stdout_callback={callback!r} resolves to the community.general.yaml callback "
        "removed in community.general 12.0.0 — ansible-playbook aborts with rc=1. "
        "Use stdout_callback = default + result_format = yaml instead."
    )


def test_stdout_callback_is_builtin_default_with_yaml_result_format() -> None:
    """The chosen replacement: built-in default callback rendering YAML output."""
    defaults = _defaults()
    assert (defaults.get("stdout_callback") or "").strip() == "default"
    assert (defaults.get("result_format") or "").strip() == "yaml"
