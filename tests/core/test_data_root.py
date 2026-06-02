"""Tests for `agmind.core.paths.data_root` — the single source of truth for the
directory that holds the bundled ``templates/`` / ``ansible/`` / ``scripts/`` trees.

It must resolve correctly in both install layouts:

- editable/dev checkout: the data dirs sit at the repo root, beside ``agmind/``.
- wheel: the data dirs are bundled *inside* the package
  (``site-packages/agmind/templates`` …) as package-data, so ``data_root()`` has
  to return the package dir itself.

A packaging guard also pins that every ``agmind`` subpackage is declared in
``pyproject.toml`` (so the explicit ``packages`` list cannot silently drift) and
that the three data dirs are wired as package-data.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from agmind.core.paths import _resolve_data_root, data_root

pytestmark = pytest.mark.backend_any

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_data_root_dev_resolves_to_repo_root_holding_templates() -> None:
    root = data_root()
    assert (root / "templates").is_dir()
    assert (root / "templates" / "services").is_dir()
    assert (root / "ansible" / "install.yml").is_file()


def test_resolve_prefers_repo_root_layout(tmp_path: Path) -> None:
    # editable: <repo>/templates exists, <repo>/agmind is the package dir.
    pkg = tmp_path / "agmind"
    pkg.mkdir()
    (tmp_path / "templates").mkdir()
    assert _resolve_data_root(pkg) == tmp_path


def test_resolve_falls_back_to_package_dir_in_wheel(tmp_path: Path) -> None:
    # wheel: site-packages/agmind/templates exists; site-packages has no templates.
    site = tmp_path / "site-packages"
    pkg = site / "agmind"
    (pkg / "templates").mkdir(parents=True)
    assert _resolve_data_root(pkg) == pkg


def test_repo_root_aliases_equal_data_root() -> None:
    from agmind.addons import candidates
    from agmind.components import registry as components_registry
    from agmind.deploy import targets
    from agmind.governance import REPO_ROOT as governance_root
    from agmind.install.orchestrator import DEFAULT_REPO_ROOT
    from agmind.services import renderer

    root = data_root()
    assert DEFAULT_REPO_ROOT == root
    assert renderer.REPO_ROOT == root
    assert components_registry.REPO_ROOT == root
    assert targets.REPO_ROOT == root
    assert candidates.REPO_ROOT == root
    assert governance_root == root


def _setuptools_table() -> dict:
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return data["tool"]["setuptools"]


def test_every_agmind_subpackage_is_declared() -> None:
    declared = set(_setuptools_table()["packages"])
    discovered = {
        ".".join(p.parent.relative_to(REPO_ROOT).parts)
        for p in (REPO_ROOT / "agmind").rglob("__init__.py")
    }
    missing = discovered - declared
    assert not missing, f"agmind subpackages not declared in pyproject: {sorted(missing)}"


def test_data_dirs_are_bundled_as_package_data() -> None:
    st = _setuptools_table()
    for name, src in (
        ("agmind.templates", "templates"),
        ("agmind.ansible", "ansible"),
        ("agmind.scripts", "scripts"),
    ):
        assert name in st["packages"], f"{name} missing from packages"
        assert st["package-dir"][name] == src, f"{name} package-dir mismatch"
        assert st["package-data"][name], f"{name} has no package-data glob"
