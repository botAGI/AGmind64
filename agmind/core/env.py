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


def _unescape_double_quoted(value: str) -> str:
    """Undo docker-compose double-quoted escaping (``\\\\`` and ``\\"``).

    Single left-to-right pass so ``\\\\`` decodes to one backslash without then
    treating a following quote as escaped. The inverse of the escaping applied
    by :func:`compose_env_quote`. Unknown ``\\x`` sequences are left verbatim
    (compose treats only ``\\n \\t \\r \\\\ \\"`` as escapes; we conservatively
    pass others through to avoid silently mangling literals).
    """
    out: list[str] = []
    i = 0
    n = len(value)
    while i < n:
        ch = value[i]
        if ch == "\\" and i + 1 < n and value[i + 1] in ('"', "\\"):
            out.append(value[i + 1])
            i += 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


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
        # Strip surrounding quotes if any (compose env-file semantics)
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            quote_char = value[0]
            value = value[1:-1]
            # Double-quoted form supports \\ and \" escapes (no $ interpolation);
            # single-quoted form is literal. Aligns with compose_env_quote.
            if quote_char == '"':
                value = _unescape_double_quoted(value)
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


# Characters that force docker-compose env-file double-quoting. A value is left
# bare (literal-to-EOL) unless it contains one of these. Whitespace, quotes,
# backslash, and `#` (inline-comment trigger for the reader) all require quoting.
_COMPOSE_BARE_SAFE = re.compile(r"^[A-Za-z0-9._:+=/@-]*$")


def compose_env_quote(value: str) -> str:
    """Quote a value for a docker-compose ``--env-file`` (env-file semantics).

    Aligned with :func:`parse_env_text`'s reader so values round-trip:

    - A value with no whitespace/quote/backslash/special stays **bare**
      (unquoted), byte-identical to the legacy output — this preserves
      idempotency for ``token_urlsafe`` secrets, image tags, and digests.
    - Otherwise the value is wrapped in DOUBLE quotes with ``\\`` and ``"``
      backslash-escaped, matching compose's double-quoted form (which supports
      ``\\\\``/``\\"`` escapes and does NOT interpolate ``$``).

    This intentionally differs from :func:`shell_quote` (POSIX single-quote
    escaping for a shell), whose contract is unrelated to compose env-files.
    """
    if "\n" in value or "\r" in value:
        raise ValueError("compose env-file values must not contain literal newline characters")
    if value and _COMPOSE_BARE_SAFE.match(value):
        return value
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'
