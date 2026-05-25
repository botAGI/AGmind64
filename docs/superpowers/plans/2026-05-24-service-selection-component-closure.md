# Service Selection Component Closure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make setup wizard service selection expand tightly-coupled component stacks and their mandatory runtime dependencies, so selecting `dify-api` produces a deployable Dify stack.

**Architecture:** Keep `select_services()` as the exact low-level filter because render and conflict tests rely on explicit selection semantics. Add a focused service-selection resolver that can expand stack components, recursive `depends_on`, and mandatory component capabilities. Wire wizard-collected service selections through that resolver before preview/apply, and sync the multistep services screen so checked boxes visibly reflect the closure.

**Tech Stack:** Python 3.12, Pydantic service/component models, YAML service/component catalogs, pytest.

---

### Task 1: Capture The Dify Selection Gap

**Files:**
- Test: `tests/test_service_selection.py`

- [x] **Step 1: Write the failing Dify closure test**

```python
def test_service_selection_expands_dify_stack_and_mandatory_runtime_dependencies() -> None:
    from agmind.components import load_component_contracts
    from agmind.services.renderer import check_missing_dependencies, load_descriptors
    from agmind.services.selection import resolve_service_selection

    descriptors = load_descriptors()
    selected = resolve_service_selection(
        descriptors,
        services=["dify-api"],
        component_contracts=load_component_contracts(),
    )

    assert {
        "dify-api",
        "dify-web",
        "dify-worker",
        "dify-plugin-daemon",
        "dify-sandbox",
        "postgres",
        "redis",
        "qdrant",
        "llama-llm",
        "llama-embed",
    } <= set(selected)
    assert "ragflow" not in selected
    assert check_missing_dependencies(selected, descriptors) == {}
```

- [x] **Step 2: Run RED**

Run: `.venv/bin/python -m pytest tests/test_service_selection.py::test_service_selection_expands_dify_stack_and_mandatory_runtime_dependencies -q`

Expected: fail because `agmind.services.selection` does not exist yet.

### Task 2: Implement The Resolver

**Files:**
- Create: `agmind/services/selection.py`
- Test: `tests/test_service_selection.py`

- [x] **Step 1: Add focused selection resolver**

```python
def resolve_service_selection(
    descriptors: Mapping[str, ServiceDescriptor],
    *,
    services: Iterable[str],
    component_contracts: Mapping[str, ComponentContract] | None = None,
) -> dict[str, ServiceDescriptor]:
    ...
```

Rules:
- start from explicit service names that exist in the descriptor catalog;
- if a selected service belongs to a component whose `provides` contains a token ending in `_stack`, add all services owned by that component;
- recursively add `depends_on` services;
- for stack components, satisfy `component.requires.capabilities` by adding an existing selected provider or a deterministic default provider, preferring services from `SetupState` defaults/core profiles;
- do not satisfy optional service-level consumes such as `dify_external_kb`.

- [x] **Step 2: Run GREEN**

Run: `.venv/bin/python -m pytest tests/test_service_selection.py::test_service_selection_expands_dify_stack_and_mandatory_runtime_dependencies -q`

Expected: pass.

### Task 3: Wire Wizard Selection Through The Resolver

**Files:**
- Modify: `agmind/cli/tui/setup_wizard.py`
- Modify: `agmind/cli/tui/wizard_screens.py`
- Test: `tests/test_tui_setup.py`

- [x] **Step 1: Add wizard helper test**

```python
def test_expand_selected_services_for_setup_expands_dify_api() -> None:
    from agmind.cli.tui.setup_wizard import expand_selected_services_for_setup

    services = expand_selected_services_for_setup(["dify-api"])

    assert "dify-api" in services
    assert "dify-web" in services
    assert "dify-worker" in services
    assert "dify-plugin-daemon" in services
    assert "dify-sandbox" in services
    assert "postgres" in services
    assert "redis" in services
    assert "qdrant" in services
    assert "llama-llm" in services
    assert "llama-embed" in services
    assert "ragflow" not in services
```

- [x] **Step 2: Use the helper in setup collection**

In legacy `_collect_state()` and multistep `ServicesScreen._save_and_advance()`, replace the raw checkbox list with the expanded list before storing `SetupState.services`.

- [x] **Step 3: Run focused TUI/setup tests**

Run: `.venv/bin/python -m pytest tests/test_service_selection.py tests/test_tui_setup.py::test_expand_selected_services_for_setup_expands_dify_api -q`

Expected: pass.

### Task 4: Verify And Record

**Files:**
- Modify: `.planning/STATE.md`
- Modify: `.planning/BACKLOG.md`
- Modify: `.planning/codebase/ARCHITECTURE.md`
- Modify: `.planning/codebase/INDEX.md`
- Modify: `docs/superpowers/plans/2026-05-24-service-selection-component-closure.md`

- [x] **Step 1: Run focused verification**

Run: `.venv/bin/python -m pytest tests/test_service_selection.py tests/test_tui_setup.py tests/test_wizard_multistep.py -q`

Expected: pass.

- [x] **Step 2: Run governance guard**

Run: `.venv/bin/python scripts/governance_check.py`

Expected: `governance OK: 6 checks`.

- [x] **Step 3: Run final formatting/check gates**

Run: `.venv/bin/ruff format --check agmind/services/selection.py agmind/cli/tui/setup_wizard.py agmind/cli/tui/wizard_screens.py tests/test_service_selection.py tests/test_tui_setup.py && .venv/bin/ruff check agmind/services/selection.py agmind/cli/tui/setup_wizard.py agmind/cli/tui/wizard_screens.py tests/test_service_selection.py tests/test_tui_setup.py && git diff --check`

Expected: all pass.

## Verification Log

- RED service selection test:
  `.venv/bin/python -m pytest tests/test_service_selection.py::test_service_selection_expands_dify_stack_and_mandatory_runtime_dependencies -q`
  failed with `ModuleNotFoundError: No module named 'agmind.services.selection'`.
- GREEN service selection test:
  `.venv/bin/python -m pytest tests/test_service_selection.py::test_service_selection_expands_dify_stack_and_mandatory_runtime_dependencies -q`
  passed 1 test after adding `resolve_service_selection()`.
- RED setup helper test:
  `.venv/bin/python -m pytest tests/test_tui_setup.py::test_expand_selected_services_for_setup_expands_dify_api -q`
  failed because `expand_selected_services_for_setup` did not exist.
- RED multistep checkbox test:
  `.venv/bin/python -m pytest tests/test_wizard_multistep.py::test_services_screen_checking_dify_api_marks_component_closure -q`
  failed because checking `dify-api` did not check `dify-web`.
- GREEN focused user path:
  `.venv/bin/python -m pytest tests/test_wizard_multistep.py::test_services_screen_checking_dify_api_marks_component_closure tests/test_tui_setup.py::test_expand_selected_services_for_setup_expands_dify_api tests/test_service_selection.py::test_service_selection_expands_dify_stack_and_mandatory_runtime_dependencies -q`
  passed 3 tests.
- Focused setup/TUI/service-selection suite:
  `.venv/bin/python -m pytest tests/test_service_selection.py tests/test_tui_setup.py tests/test_wizard_multistep.py -q`
  passed 50 tests.
- Static formatting/check:
  `.venv/bin/ruff format agmind/services/selection.py` reformatted the new
  resolver file. Follow-up ruff format check and ruff check for the touched
  service-selection/setup/TUI/test files passed.
- Focused service/TUI/compat/deploy verification:
  `.venv/bin/python -m pytest tests/test_service_selection.py tests/test_tui_setup.py tests/test_wizard_multistep.py tests/test_service_compatibility.py tests/test_deploy_conflicts.py -q`
  passed 78 tests.
- Static type and lint verification:
  ruff format check, ruff check, and
  `.venv/bin/mypy agmind/services/selection.py agmind/cli/tui/setup_wizard.py agmind/cli/tui/wizard_screens.py`
  passed.
- Governance verification:
  `.venv/bin/python scripts/governance_check.py` passed with 6 checks, and
  `.venv/bin/python scripts/deploy_target_check.py` passed with 3 targets.
- Final workspace gates:
  `git diff --check` passed, and
  `.venv/bin/pre-commit run --all-files --show-diff-on-failure` passed. The
  full pre-commit run skipped deploy-target/governance hooks because their
  file globs were not selected, so those scripts were run explicitly.
- Expanded M7 regression:
  `.venv/bin/python -m pytest tests/test_component_contracts.py tests/test_component_update_report.py tests/test_dependency_constraints.py tests/test_deploy_conflicts.py tests/test_deploy_targets.py tests/test_governance_cmd.py tests/test_kubernetes_dry_run.py tests/test_kubernetes_render_check.py tests/test_kubernetes_renderer.py tests/test_model_catalog_unification.py tests/test_proxmox_exporter_ansible.py tests/test_proxmox_exporter_config.py tests/test_proxmox_exporter_service.py tests/test_proxmox_inventory.py tests/test_proxmox_module.py tests/test_targets_cmd.py tests/test_tool_candidates.py tests/test_tools_cmd.py tests/test_cli.py::test_governance_validate_command tests/test_service_selection.py -q`
  passed 186 tests.
