"""Phase 10-02 (M8): the operator scenario catalog + `agmind render scenario`.

Every scenario must resolve a closure-complete, renderable, isolated stack; the catalog stays
lean (no de-selected services)."""

from __future__ import annotations

from pathlib import Path

import pytest

from agmind.cli.render_cmd import cmd_render_scenario
from agmind.services.scenarios import (
    SCENARIO_CATALOG,
    get_scenario,
    list_scenarios,
    scenario_names,
)

pytestmark = pytest.mark.backend_any

# Kept in the catalog "for interest" but out of any default/operator deploy.
_DESELECTED = {"dozzle", "netdata", "homarr", "uptime-kuma"}


def test_scenarios_exclude_deselected_services() -> None:
    for scenario in SCENARIO_CATALOG:
        overlap = set(scenario.services) & _DESELECTED
        assert not overlap, f"{scenario.name} includes de-selected service(s): {overlap}"


def test_lookup_helpers() -> None:
    assert get_scenario("core-rag") is not None
    assert get_scenario("does-not-exist") is None
    assert "core-rag" in scenario_names()
    assert scenario_names() == sorted(scenario_names())
    assert len(list_scenarios()) == len(SCENARIO_CATALOG)


def test_scenario_service_names_are_real() -> None:
    from agmind.services.renderer import load_descriptors

    catalog = set(load_descriptors())
    for scenario in SCENARIO_CATALOG:
        unknown = set(scenario.services) - catalog
        assert not unknown, f"{scenario.name} references unknown service(s): {unknown}"


@pytest.mark.parametrize("scenario", SCENARIO_CATALOG, ids=lambda s: s.name)
def test_every_scenario_renders_isolated_stack(scenario, tmp_path: Path) -> None:
    """End-to-end: each scenario resolves its closure and renders a namespaced stack dir."""
    out = tmp_path / scenario.name
    rc = cmd_render_scenario(name=scenario.name, out=out)
    assert rc == 0
    compose = (out / "compose.yaml").read_text(encoding="utf-8")
    assert (out / ".env.example").exists()
    assert (out / "README.md").exists()
    # namespaced: containers + project named after the scenario, not bare `agmind-`
    assert f"name: agmind-{scenario.name}" in compose
    assert f"agmind-{scenario.name}-{scenario.services[0]}" in compose


def test_core_rag_expands_backend_closure(tmp_path: Path) -> None:
    """A RAG scenario must pull its mandatory backends into the rendered stack."""
    out = tmp_path / "core-rag"
    assert cmd_render_scenario(name="core-rag", out=out) == 0
    compose = (out / "compose.yaml").read_text(encoding="utf-8")
    for required in ("postgres", "redis"):
        assert f"agmind-core-rag-{required}" in compose


@pytest.mark.skipif(
    hasattr(__import__("os"), "geteuid") and __import__("os").geteuid() == 0,
    reason="root bypasses filesystem permissions",
)
def test_scenario_write_to_unwritable_dir_is_graceful(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The default out dir (/opt/agmind/stacks/<name>) is root-owned; a non-sudo render must
    surface a clean ERROR + exit 1, not a raw PermissionError traceback."""
    parent = tmp_path / "ro"
    parent.mkdir()
    parent.chmod(0o500)  # r-x, no write
    try:
        rc = cmd_render_scenario(name="inference", out=parent / "stack")
        assert rc == 1
        assert "ERROR" in capsys.readouterr().err
    finally:
        parent.chmod(0o700)  # restore for tmp cleanup


def test_unknown_scenario_errors(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc = cmd_render_scenario(name="nope", out=tmp_path / "x")
    assert rc == 1
    assert "unknown scenario" in capsys.readouterr().err


def test_list_only_lists_and_succeeds(capsys: pytest.CaptureFixture[str]) -> None:
    rc = cmd_render_scenario(name=None, list_only=True)
    assert rc == 0
    assert "core-rag" in capsys.readouterr().out


def test_readme_is_honest_about_staging(tmp_path: Path) -> None:
    out = tmp_path / "inference"
    assert cmd_render_scenario(name="inference", out=out) == 0
    readme = (out / "README.md").read_text(encoding="utf-8")
    assert "compose layer only" in readme.lower() or "not self-sufficient" in readme.lower()
    assert "agmind install" in readme
