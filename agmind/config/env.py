"""Render и write .env-файлов с placeholder substitution.

Substitution: `${KEY}` → value. Unresolved placeholders → KeyError.
"""

from __future__ import annotations

import re
from pathlib import Path

from agmind.core.files import write_text_atomic

_PLACEHOLDER_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def render_env(template: str, vars: dict[str, str]) -> str:
    """Substitute ${KEY} placeholders. Raises KeyError on missing key.

    Args:
        template: text content of .env template.
        vars: substitution table.

    Returns:
        Rendered text.

    Raises:
        KeyError: если placeholder не resolved (нет в vars).
    """

    def _sub(m: re.Match[str]) -> str:
        key = m.group(1)
        if key not in vars:
            raise KeyError(f"Unresolved placeholder ${{{key}}} in env template")
        return vars[key]

    return _PLACEHOLDER_RE.sub(_sub, template)


def write_env(
    path: Path | str,
    content: str,
    *,
    mode: int = 0o644,
) -> None:
    """Atomic write of .env file с указанным permissions.

    Use mode=0o600 для secret-containing files (.env с paswords).
    """
    write_text_atomic(Path(path), content, mode=mode)
