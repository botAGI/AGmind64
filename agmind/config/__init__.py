"""Config layer — .env generation, placeholder substitution.

См. agmind.config.env для file ops.
"""

from __future__ import annotations

from agmind.config.env import render_env, write_env

__all__ = ["render_env", "write_env"]
