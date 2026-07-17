"""Guard: the strix_halo GRUB per-param toggle idiom covers ``lockup_timeout`` (D-02).

The ``agmind_strix_halo.*`` per-param toggles (``ttm_page_pool`` / ``amd_iommu_off`` /
``zswap_off``) gate optional entries appended to the ``agmind_grub_params`` set_fact in
``ansible/roles/strix_halo/tasks/main.yml``; the assembled list is joined into the
``/etc/default/grub.d/99-agmind.cfg`` drop-in. GRUB param coverage carried ZERO test
coverage before this file — this is a greenfield hermetic guard (yaml.safe_load only, no
live ``ansible-playbook`` run, no host GRUB touched).

Adds one more toggle: ``amdgpu.lockup_timeout=60000`` — batched Vulkan submits on the
iGPU exceed the kernel's default ``amdgpu.lockup_timeout`` (~2000ms) and trigger a
compute-ring reset (llama.cpp #21724); raising the timeout avoids false GPU resets under
the np4 batching load (plan 14-01).

Mirrors ``tests/ansible/test_secret_task_no_log.py`` / ``test_render_compose_idempotency.py``
(``yaml.safe_load`` -> filter dicts -> select-by-key) and the folded-scalar substring-selector
pattern (GOTCHA G.4-a): ``agmind_grub_params`` is a single ``>-`` folded scalar string, so
assert via substring match, not whitespace-split.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.backend_any

# tests/ansible/test_strix_halo_grub_toggles.py -> parents[2] == repo root.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_GROUP_VARS = _REPO_ROOT / "ansible" / "group_vars" / "all.yml"
_STRIX_HALO_TASKS = _REPO_ROOT / "ansible" / "roles" / "strix_halo" / "tasks" / "main.yml"

# Substring selectors. GOTCHA G.4-a: the set_fact value is a single ``>-`` folded scalar
# string, so substring-match on the folded value — do NOT split on whitespace.
_LOCKUP_TIMEOUT_PARAM = "amdgpu.lockup_timeout=60000"
_LOCKUP_TIMEOUT_GUARD = "agmind_strix_halo.lockup_timeout"
_PAGES_LIMIT_MARKER = "ttm.pages_limit="
_GRUB_PARAMS_SET_FACT_NAME = "Assemble agmind GRUB params from toggles"


def _load_group_vars() -> dict[str, object]:
    data = yaml.safe_load(_GROUP_VARS.read_text(encoding="utf-8"))
    assert isinstance(data, dict), f"expected a mapping in {_GROUP_VARS}"
    return data


def _load_tasks() -> list[dict[str, object]]:
    tasks = yaml.safe_load(_STRIX_HALO_TASKS.read_text(encoding="utf-8"))
    assert isinstance(tasks, list), f"expected a task list in {_STRIX_HALO_TASKS}"
    return [t for t in tasks if isinstance(t, dict)]


def _grub_params_set_fact_task() -> dict[str, object]:
    for task in _load_tasks():
        if task.get("name") == _GRUB_PARAMS_SET_FACT_NAME:
            return task
    raise AssertionError(
        f"missing '{_GRUB_PARAMS_SET_FACT_NAME}' set_fact task in {_STRIX_HALO_TASKS}"
    )


def _grub_params_expression() -> str:
    task = _grub_params_set_fact_task()
    set_fact = task.get("ansible.builtin.set_fact")
    assert isinstance(set_fact, dict), (
        f"'{_GRUB_PARAMS_SET_FACT_NAME}' task must use ansible.builtin.set_fact"
    )
    expr = set_fact.get("agmind_grub_params")
    assert isinstance(expr, str), "agmind_grub_params set_fact value must be a string"
    return expr


def test_role_yaml_files_exist() -> None:
    assert _GROUP_VARS.exists(), f"missing group_vars file: {_GROUP_VARS}"
    assert _STRIX_HALO_TASKS.exists(), f"missing role task file: {_STRIX_HALO_TASKS}"


def test_group_vars_declares_lockup_timeout_toggle() -> None:
    """``agmind_strix_halo.lockup_timeout`` exists with a truthy default (group_vars)."""
    strix_halo = _load_group_vars().get("agmind_strix_halo")
    assert isinstance(strix_halo, dict), "agmind_strix_halo block missing from group_vars/all.yml"
    assert "lockup_timeout" in strix_halo, (
        "agmind_strix_halo.lockup_timeout toggle missing from group_vars/all.yml"
    )
    assert strix_halo["lockup_timeout"] is True, (
        "agmind_strix_halo.lockup_timeout must default to true (matches the three "
        "existing per-param toggles: ttm_page_pool / amd_iommu_off / zswap_off)"
    )


def test_grub_params_set_fact_contains_conditional_lockup_timeout() -> None:
    """The ``agmind_grub_params`` set_fact carries a guarded 60000 lockup_timeout entry."""
    expr = _grub_params_expression()
    assert _LOCKUP_TIMEOUT_PARAM in expr, (
        f"agmind_grub_params set_fact missing conditional '{_LOCKUP_TIMEOUT_PARAM}' entry"
    )
    assert _LOCKUP_TIMEOUT_GUARD in expr, (
        f"agmind_grub_params set_fact's {_LOCKUP_TIMEOUT_PARAM} entry is not guarded by "
        f"'{_LOCKUP_TIMEOUT_GUARD}' (per-param toggle idiom)"
    )


def test_pages_limit_entry_remains_unconditional() -> None:
    """The mandatory ``ttm.pages_limit=`` entry is NOT gated by any toggle (regression guard).

    The new lockup_timeout toggle is additive; it must not turn the one mandatory GTT
    prerequisite entry into an optional one.
    """
    expr = _grub_params_expression()
    assert _PAGES_LIMIT_MARKER in expr, "ttm.pages_limit= entry missing from agmind_grub_params"
    # The mandatory entry is the leading, unconditional list literal — it must not be
    # wrapped in an `if ... else []` conditional branch itself. Locate the substring and
    # confirm no `if`/`else` guard appears before the next ` + ` list-concatenation term.
    idx = expr.index(_PAGES_LIMIT_MARKER)
    # Walk backwards to the start of this list literal (`[`); a conditional branch would
    # have an `if agmind_strix_halo.` guard between this `[` and the *previous* `]`.
    list_start = expr.rindex("[", 0, idx)
    prefix_between_lists = expr[:list_start]
    # Only the pages_limit list itself should appear before any `if` guard; if an `if`
    # appears in the prefix, it belongs to an EARLIER conditional branch, which is fine —
    # what matters is that the pages_limit list's OWN term (from `[` to the matching `]`)
    # contains no `if`/`else`.
    list_end = expr.index("]", list_start)
    pages_limit_term = expr[list_start : list_end + 1]
    assert "if" not in pages_limit_term and "else" not in pages_limit_term, (
        "ttm.pages_limit= entry must remain unconditional (mandatory GTT prerequisite), "
        f"found a conditional guard in its list term: {pages_limit_term!r}"
    )
    # And it must not require the new lockup_timeout toggle either.
    assert prefix_between_lists.count(_LOCKUP_TIMEOUT_GUARD) == 0, (
        "ttm.pages_limit= entry must not be gated by the lockup_timeout toggle"
    )
