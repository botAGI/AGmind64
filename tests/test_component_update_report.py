from __future__ import annotations

import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.backend_any

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_component_report_renders_component_update_policy() -> None:
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import version_check as vc

    reports = vc.build_reports(probe_fn=lambda _image: "9.9.9")
    md = vc.render_markdown(reports)

    assert "`dify`" in md
    assert "`strict-pin`" in md
    assert "Dify api, web, worker, sandbox, and plugin daemon update as one stack." in md


def test_report_legend_mentions_component_and_probe_statuses() -> None:
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import version_check as vc

    md = vc.render_markdown([])

    assert "newer_than_probe" in md
    assert "strict-pin" in md
    assert "pinned-by-parent" in md


def test_report_has_issue66_update_instructions() -> None:
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import version_check as vc

    md = vc.render_markdown([])

    assert "How to update" in md
    assert "agmind upgrade --check" in md
    assert "agmind upgrade --component" in md
    assert "agmind doctor" in md
    assert "agmind upgrade --rollback" in md


def test_report_has_component_policy_terms() -> None:
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import version_check as vc

    md = vc.render_markdown([])

    for term in ["grouped", "major-hold", "volatile", "patch-auto"]:
        assert term in md
