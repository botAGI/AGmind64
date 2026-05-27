"""Backward-compatible .env parser API shim."""

from agmind.core.env import env_get, parse_env_file, parse_env_text, shell_quote

__all__ = ["env_get", "parse_env_file", "parse_env_text", "shell_quote"]
