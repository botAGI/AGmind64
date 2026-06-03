"""Tests for `agmind troubleshoot <topic>` — surface docs/TROUBLESHOOTING.md offline.

Parser + resolver are tested hermetically; CLI tests run against the real doc
(located via data_root()). Mirrors the parent contract: bare → topic list (exit 0),
known → section (exit 0), unknown → stderr (exit 1).
"""

from __future__ import annotations

import pytest

from agmind.cli import _HAS_TYPER
from agmind.cli.troubleshoot_cmd import _slugify, parse_topics, resolve_topic

pytestmark = pytest.mark.backend_any


# ---- slug + parser ----


def test_slugify_strips_section_prefix() -> None:
    assert _slugify("Section 3: GTT memory") == "gtt-memory"
    assert _slugify("Section 1: Vulkan / GPU detection") == "vulkan-gpu-detection"
    assert _slugify("Quick reference") == "quick-reference"


def test_parse_topics_real_doc_has_expected_sections() -> None:
    from agmind.core.paths import data_root

    text = (data_root() / "docs" / "TROUBLESHOOTING.md").read_text(encoding="utf-8")
    topics = parse_topics(text)
    for slug in ("gtt-memory", "rocm-hip", "emergency-rollback", "get-help"):
        assert slug in topics, f"missing {slug}"
        title, body = topics[slug]
        assert title and body.strip(), f"{slug} has empty title/body"


def test_parse_topics_ignores_preamble_before_first_h2() -> None:
    text = "# Title\nintro line\n\n## Section 1: Foo\nbody foo\n\n## Section 2: Bar\nbody bar\n"
    topics = parse_topics(text)
    assert set(topics) == {"foo", "bar"}
    assert "intro line" not in topics.get("foo", ("", ""))[1]


# ---- resolver ----


def test_resolve_exact_slug() -> None:
    topics = {"gtt-memory": ("", ""), "rocm-hip": ("", "")}
    assert resolve_topic("gtt-memory", topics, {}) == ["gtt-memory"]


def test_resolve_alias() -> None:
    topics = {"vulkan-gpu-detection": ("", "")}
    assert resolve_topic("gpu", topics, {"gpu": "vulkan-gpu-detection"}) == ["vulkan-gpu-detection"]


def test_resolve_substring_unique() -> None:
    topics = {"gtt-memory": ("", ""), "rocm-hip": ("", "")}
    assert resolve_topic("rocm", topics, {}) == ["rocm-hip"]


def test_resolve_ambiguous_returns_all_candidates() -> None:
    topics = {"docker-compose": ("", ""), "docker-logs": ("", "")}
    assert sorted(resolve_topic("docker", topics, {})) == ["docker-compose", "docker-logs"]


def test_resolve_unknown_returns_empty() -> None:
    assert resolve_topic("nope", {"gtt-memory": ("", "")}, {}) == []


# ---- CLI ----


def _invoke(args):
    from typer.testing import CliRunner

    from agmind.cli import _make_app

    return CliRunner().invoke(_make_app(), args)


@pytest.mark.skipif(not _HAS_TYPER, reason="typer not installed")
def test_troubleshoot_registered_in_help() -> None:
    result = _invoke(["--help"])
    assert result.exit_code == 0
    assert "troubleshoot" in result.output.lower()


@pytest.mark.skipif(not _HAS_TYPER, reason="typer not installed")
def test_troubleshoot_bare_lists_topics() -> None:
    result = _invoke(["troubleshoot"])
    assert result.exit_code == 0, result.output
    assert "gtt-memory" in result.output
    assert "rocm-hip" in result.output


@pytest.mark.skipif(not _HAS_TYPER, reason="typer not installed")
def test_troubleshoot_known_topic_prints_section() -> None:
    result = _invoke(["troubleshoot", "gtt-memory"])
    assert result.exit_code == 0, result.output
    assert "GTT" in result.output


@pytest.mark.skipif(not _HAS_TYPER, reason="typer not installed")
def test_troubleshoot_alias_resolves() -> None:
    result = _invoke(["troubleshoot", "gpu"])
    assert result.exit_code == 0, result.output


@pytest.mark.skipif(not _HAS_TYPER, reason="typer not installed")
def test_troubleshoot_unknown_topic_errors_cleanly() -> None:
    result = _invoke(["troubleshoot", "__definitely-not-a-topic__"])
    assert result.exit_code == 1
    assert "Traceback" not in result.output
