"""Tests for `agmind estimate` — CLI smoke + JSON shape + strict exit code.

Host figures are injected via --ram/--gtt (GiB) so the command never touches
real hardware in CI.
"""

from __future__ import annotations

import json

import pytest

from agmind.cli import _HAS_TYPER

pytestmark = pytest.mark.backend_any

pytest.importorskip("typer")


def _runner():
    from typer.testing import CliRunner

    return CliRunner()


def _app():
    from agmind.cli import _make_app

    return _make_app()


@pytest.mark.skipif(not _HAS_TYPER, reason="typer not installed")
def test_estimate_is_registered_in_help() -> None:
    result = _runner().invoke(_app(), ["--help"])
    assert result.exit_code == 0
    assert "estimate" in result.output.lower()


@pytest.mark.skipif(not _HAS_TYPER, reason="typer not installed")
def test_estimate_human_output_lists_total() -> None:
    result = _runner().invoke(_app(), ["estimate", "--profile", "core", "--ram", "8", "--gtt", "4"])
    assert result.exit_code == 0, result.output
    assert "TOTAL" in result.output.upper()


@pytest.mark.skipif(not _HAS_TYPER, reason="typer not installed")
def test_estimate_json_shape() -> None:
    result = _runner().invoke(
        _app(),
        ["estimate", "--profile", "core", "--ram", "8", "--gtt", "4", "--json"],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["profiles"] == ["core"]
    assert payload["host"]["ram_bytes"] == 8 * 1024**3
    assert payload["host"]["gtt_bytes"] == 4 * 1024**3
    assert isinstance(payload["over_ram"], bool)
    assert payload["services"], "services list must be non-empty for core"


@pytest.mark.skipif(not _HAS_TYPER, reason="typer not installed")
def test_estimate_strict_exits_nonzero_on_overcommit() -> None:
    # Tiny RAM forces over-commit; --strict turns that into a non-zero exit.
    result = _runner().invoke(
        _app(),
        ["estimate", "--profile", "full", "--ram", "1", "--gtt", "1", "--strict"],
    )
    assert result.exit_code != 0


@pytest.mark.skipif(not _HAS_TYPER, reason="typer not installed")
def test_estimate_informational_exit_zero_even_when_over() -> None:
    # Without --strict, over-commit is informational (exit 0).
    result = _runner().invoke(
        _app(),
        ["estimate", "--profile", "full", "--ram", "1", "--gtt", "1"],
    )
    assert result.exit_code == 0, result.output


@pytest.mark.skipif(not _HAS_TYPER, reason="typer not installed")
def test_estimate_unknown_profile_errors_cleanly() -> None:
    result = _runner().invoke(
        _app(), ["estimate", "--profile", "does-not-exist", "--ram", "8", "--gtt", "4"]
    )
    assert result.exit_code != 0
    assert "Traceback" not in result.output
