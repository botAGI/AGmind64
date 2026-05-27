"""Тонкий reader .env-файлов с поддержкой quoted values + multiline.

Намеренно НЕ зависит от python-dotenv — нужно минимальное API без
implicit side-effects (как `load_dotenv()` который мутирует os.environ).
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path

_LINE_RE = re.compile(
    r"""^\s*
        (?:export\s+)?
        (?P<key>[A-Za-z_][A-Za-z0-9_]*)
        \s*=\s*
        (?P<value>.*)
        $""",
    re.VERBOSE,
)


def parse_env_text(text: str) -> dict[str, str]:
    """Parse .env-style text, return key→value mapping.

    Supports:
    - `KEY=value` and `export KEY=value`
    - quoted values: `KEY="value with spaces"`, `KEY='literal'`
    - `# comment` lines and trailing comments after value (если value
      не в кавычках)
    """
    result: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = _LINE_RE.match(line)
        if not m:
            continue
        key = m.group("key")
        value = m.group("value").strip()
        # Trim trailing comment if value is not quoted
        if value and value[0] not in ('"', "'"):
            if "#" in value:
                value = value.split("#", 1)[0].rstrip()
        # Strip surrounding quotes if any
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        result[key] = value
    return result


def parse_env_file(path: str | Path) -> dict[str, str]:
    """Read and parse a .env file. Missing file → empty dict (not raise)."""
    p = Path(path)
    if not p.exists():
        return {}
    return parse_env_text(p.read_text(encoding="utf-8"))


def env_get(key: str, env_file: str | Path | None = None, default: str = "") -> str:
    """Get value from .env file (or default if missing).

    If env_file is None — reads from $AGMIND_CONFIG_DIR/.env (recommended).
    """
    if env_file is None:
        from os import environ

        base = environ.get("AGMIND_CONFIG_DIR", "/etc/agmind")
        env_file = Path(base) / ".env"
    data = parse_env_file(env_file)
    return data.get(key, default)


def shell_quote(value: str) -> str:
    """Quote a string for shell-safe embedding in .env files."""
    return shlex.quote(value)
