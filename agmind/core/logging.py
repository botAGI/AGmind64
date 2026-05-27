"""Logging utilities — structlog-backed с backward-compatible API (Phase H'.D).

Сохранены сигнатуры `setup(level)` и `logger(name) -> logging.Logger` для
backward compat с существующим кодом и тестами. structlog настраивается под
капотом как formatter — все stdlib loggers (urllib, httpx) пропадают через тот
же JSON pipeline (см. structlog ProcessorFormatter docs).

Опции (env vars):
    AGMIND_LOG_LEVEL=INFO|DEBUG|WARNING|ERROR
    AGMIND_LOG_JSON=true|false (default: false — human-readable в terminal)

Контекст-binding для trace_id propagation:
    from agmind.core.logging import bind_context, clear_context
    bind_context(trace_id="abc123", model="qwen2-7b")
    log = logger(__name__)
    log.info("processed")  # появится с trace_id + model в JSON output
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Any, Final

# structlog — soft dependency. Если не установлен, fallback на чистый stdlib.
try:
    import structlog

    _HAS_STRUCTLOG = True
except ImportError:
    structlog = None  # type: ignore[assignment]
    _HAS_STRUCTLOG = False


_DEFAULT_FORMAT: Final[str] = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
_DATE_FORMAT: Final[str] = "%Y-%m-%dT%H:%M:%S%z"


def _coerce_level(level: str | int | None) -> int:
    """Normalize level (env string / int / None) to logging.INTEGER."""
    if level is None:
        level = os.environ.get("AGMIND_LOG_LEVEL", "INFO")
    if isinstance(level, str):
        level = level.upper()
        return getattr(logging, level, logging.INFO)
    return int(level)


def _env_bool(name: str, default: bool = False) -> bool:
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


def setup(level: str | int | None = None, *, json_output: bool | None = None) -> None:
    """Initialise root logger.

    Args:
        level: log level (string like "INFO" / "DEBUG" or int from logging).
            If None — берётся из env AGMIND_LOG_LEVEL, иначе INFO.
        json_output: если True — JSON output через structlog (для prod / Loki).
            Если None — читает env AGMIND_LOG_JSON (default false).
    """
    log_level = _coerce_level(level)

    if json_output is None:
        json_output = _env_bool("AGMIND_LOG_JSON", default=False)

    if json_output and _HAS_STRUCTLOG:
        _setup_structlog(log_level)
    else:
        # Fallback / dev mode: чистый stdlib basicConfig
        logging.basicConfig(
            level=log_level,
            format=_DEFAULT_FORMAT,
            datefmt=_DATE_FORMAT,
            stream=sys.stderr,
            force=True,
        )


def _setup_structlog(log_level: int) -> None:
    """Configure structlog + stdlib bridge (ProcessorFormatter pattern)."""
    assert structlog is not None  # for type checker

    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)

    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.StackInfoRenderer(),
        timestamper,
    ]

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # stdlib handler — все existing logging.getLogger() уходят через тот же pipeline
    formatter = structlog.stdlib.ProcessorFormatter(
        processor=structlog.processors.JSONRenderer(),
        foreign_pre_chain=shared_processors,
    )
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(formatter)
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(log_level)


def logger(name: str) -> logging.Logger:
    """Return a namespaced stdlib logger.

    API сохранён как до Phase H'.D для совместимости. Под капотом сообщения
    проходят через structlog ProcessorFormatter (если json_output активирован).
    """
    return logging.getLogger(name)


def bind_context(**kwargs: object) -> None:
    """Bind structured context (trace_id / request_id / model) на текущий ctx.

    Context propagates через contextvars — thread/asyncio-safe.
    Если structlog не установлен — silent no-op (graceful degrade).
    """
    if _HAS_STRUCTLOG:
        structlog.contextvars.bind_contextvars(**kwargs)


def clear_context() -> None:
    """Clear все bound context vars."""
    if _HAS_STRUCTLOG:
        structlog.contextvars.clear_contextvars()
