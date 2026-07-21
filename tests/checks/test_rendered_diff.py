"""SPEC-16.1: hermetic tests for the rendered-diff PR gate helper.

Uses the REAL renderer (no docker/network) — the unit tier renders compose
YAML from the shipped descriptors, so these are host-hermetic backend_any.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "scripts" / "checks"))

import rendered_diff  # noqa: E402

from agmind.services.profile_sets import all_profile_names  # noqa: E402,I001

pytestmark = pytest.mark.backend_any


def test_render_writes_one_yaml_per_profile(tmp_path: Path) -> None:
    out = tmp_path / "render"
    written = rendered_diff.render_all_profiles(out)

    files = sorted(out.glob("*.yml"))
    # 14 canonical compose profiles (all_profile_names()); a profile add/remove
    # must move this count in lockstep, exactly like the service_count guards.
    assert len(files) == 14
    assert len(files) == len(all_profile_names())
    assert len(written) == 14
    assert {p.stem for p in files} == set(all_profile_names())
    # Deterministic + non-empty: each file is a rendered compose document.
    for f in files:
        assert "services:" in f.read_text(encoding="utf-8")


def test_diff_reports_no_changes_for_identical_dirs(tmp_path: Path) -> None:
    base = tmp_path / "base"
    head = tmp_path / "head"
    rendered_diff.render_all_profiles(base)
    rendered_diff.render_all_profiles(head)

    report = rendered_diff.build_diff_report(base, head)
    assert "No rendered changes" in report
    assert "14 profiles" in report


def test_diff_detects_a_mutated_profile(tmp_path: Path) -> None:
    base = tmp_path / "base"
    head = tmp_path / "head"
    rendered_diff.render_all_profiles(base)
    rendered_diff.render_all_profiles(head)

    target = head / "core.yml"
    target.write_text(target.read_text(encoding="utf-8") + "\n# mutated\n", encoding="utf-8")

    report = rendered_diff.build_diff_report(base, head)
    assert "No rendered changes" not in report
    assert "Changed" in report
    # Markdown names the changed profile and embeds a unified diff of it.
    assert "core" in report
    assert "```diff" in report


def test_diff_classifies_added_and_removed(tmp_path: Path) -> None:
    base = tmp_path / "base"
    head = tmp_path / "head"
    rendered_diff.render_all_profiles(base)
    rendered_diff.render_all_profiles(head)

    # A profile present only in head → added; only in base → removed.
    (head / "brand-new.yml").write_text("services: {}\n", encoding="utf-8")
    (base / "gone.yml").write_text("services: {}\n", encoding="utf-8")

    report = rendered_diff.build_diff_report(base, head)
    assert "Added" in report
    assert "brand-new" in report
    assert "Removed" in report
    assert "gone" in report
