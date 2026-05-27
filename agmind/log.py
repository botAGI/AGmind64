"""Backward-compatible logging API shim."""

from agmind.core.logging import bind_context, clear_context, logger, setup

__all__ = ["bind_context", "clear_context", "logger", "setup"]
