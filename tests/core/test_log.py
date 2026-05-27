"""Tests для agmind.log — logging utilities."""

from __future__ import annotations

import logging

import pytest

from agmind.core.logging import logger, setup

pytestmark = pytest.mark.backend_any


def test_logger_returns_namespaced() -> None:
    log = logger("agmind.test")
    assert isinstance(log, logging.Logger)
    assert log.name == "agmind.test"


def test_setup_with_string_level() -> None:
    setup(level="DEBUG")
    assert logging.getLogger().level == logging.DEBUG


def test_setup_with_int_level() -> None:
    setup(level=logging.WARNING)
    assert logging.getLogger().level == logging.WARNING


def test_setup_default_info(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGMIND_LOG_LEVEL", raising=False)
    setup()
    assert logging.getLogger().level == logging.INFO


def test_setup_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGMIND_LOG_LEVEL", "ERROR")
    setup()
    assert logging.getLogger().level == logging.ERROR


def test_setup_lowercase_level_normalized(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGMIND_LOG_LEVEL", "warning")
    setup()
    assert logging.getLogger().level == logging.WARNING


def test_logger_emits_to_configured_stream(capsys: pytest.CaptureFixture[str]) -> None:
    """Verify log записи попадают в configured stream (stderr by default).

    Использует capsys вместо caplog: Pytest 9 изменил caplog propagation
    для non-root loggers.
    """
    setup(level="INFO")
    log = logger("agmind.test.emit")
    log.info("hello world")
    captured = capsys.readouterr()
    assert "hello world" in captured.err
