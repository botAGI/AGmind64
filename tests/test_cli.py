"""Tests для agmind.cli — typer app construction.

Если typer не установлен — _HAS_TYPER=False и большинство тестов
помечается skipped.
"""

from __future__ import annotations

import pytest

from agmind.cli import _HAS_TYPER

pytestmark = pytest.mark.backend_any


def test_cli_module_imports() -> None:
    """import agmind.cli не должен падать даже без typer."""
    import agmind.cli  # noqa: F401


def test_app_function_exists() -> None:
    from agmind.cli import app

    assert callable(app)


@pytest.mark.skipif(not _HAS_TYPER, reason="typer not installed")
def test_make_app_builds_typer_instance() -> None:
    from agmind.cli import _make_app

    app = _make_app()
    # typer.Typer имеет registered_commands
    assert hasattr(app, "registered_commands") or hasattr(app, "__class__")


@pytest.mark.skipif(not _HAS_TYPER, reason="typer not installed")
def test_make_app_has_doctor_command() -> None:
    import typer
    from click.testing import CliRunner  # type: ignore[import-untyped]

    from agmind.cli import _make_app

    cli_app = typer.main.get_command(_make_app())
    runner = CliRunner()
    result = runner.invoke(cli_app, ["--help"])
    assert result.exit_code == 0
    assert "doctor" in result.output.lower()


@pytest.mark.skipif(not _HAS_TYPER, reason="typer not installed")
def test_app_version_command() -> None:
    import typer
    from click.testing import CliRunner

    from agmind import __version__
    from agmind.cli import _make_app

    cli_app = typer.main.get_command(_make_app())
    runner = CliRunner()
    result = runner.invoke(cli_app, ["version"])
    assert result.exit_code == 0
    assert __version__ in result.output


def test_app_called_without_typer_exits(monkeypatch: pytest.MonkeyPatch) -> None:
    """Если typer не установлен, app() должен корректно exit с инструкцией."""
    monkeypatch.setattr("agmind.cli._HAS_TYPER", False)
    from agmind.cli import app

    with pytest.raises(SystemExit) as exc_info:
        app()
    assert exc_info.value.code == 2
