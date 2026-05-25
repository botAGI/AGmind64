"""Credentials management — credentials.txt с chmod 600.

Никогда не выводит секреты в stdout / логи. См. текущие security-инварианты
в `.planning/codebase/INVARIANTS.md`.
"""

from __future__ import annotations

import os
import secrets as stdlib_secrets
import stat
from pathlib import Path

from agmind.log import logger

log = logger(__name__)


_DEFAULT_CREDS_PATH = "/opt/agmind/credentials.txt"
_MASK = "********"


def get_creds_path() -> Path:
    """Resolve credentials.txt path from env или default."""
    path = os.environ.get("AGMIND_CREDENTIALS_PATH", _DEFAULT_CREDS_PATH)
    return Path(path)


def generate_secret(length: int = 32) -> str:
    """Cryptographically secure random secret (base32-ish hex)."""
    return stdlib_secrets.token_urlsafe(length)


def write_creds(creds: dict[str, str], path: Path | None = None) -> None:
    """Write credentials.txt atomically с chmod 600.

    Format:
        # AGmind credentials — DO NOT COMMIT
        KEY=value
        KEY2=value2

    Каждый key=value на отдельной строке. Значения **не** quoted в файле
    (чтобы простое cat | grep работало), но шеллу нужно cautious.

    Raises:
        OSError: если parent dir не существует / не writable.
        PermissionError: chmod 600 не удалось установить.
    """
    if path is None:
        path = get_creds_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    tmp = path.with_suffix(".tmp")
    lines = ["# AGmind credentials — DO NOT COMMIT", ""]
    for key, value in creds.items():
        if not _is_valid_key(key):
            raise ValueError(f"Invalid credential key: {key!r}")
        lines.append(f"{key}={value}")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    # Set 600 ДО renaming — atomic.
    os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)  # 0600
    tmp.replace(path)
    log.info("credentials written: %s (chmod 600)", path)


def read_creds(path: Path | None = None) -> dict[str, str]:
    """Read credentials.txt. Returns empty dict if missing.

    File **must** be chmod 600 — иначе raise PermissionError для safety.
    """
    if path is None:
        path = get_creds_path()
    if not path.exists():
        return {}
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode != 0o600:
        raise PermissionError(f"{path} has mode {oct(mode)}, expected 0o600. Fix: chmod 600 {path}")
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        out[key.strip()] = value.strip()
    return out


def mask_value(value: str, keep: int = 4) -> str:
    """Return masked representation for safe logging.

    `mask_value("super-secret-1234")` → `"supe****"` (first 4 chars + mask).
    """
    if not value:
        return _MASK
    if len(value) <= keep:
        return _MASK
    return value[:keep] + _MASK


def _is_valid_key(key: str) -> bool:
    if not key:
        return False
    if not (key[0].isalpha() or key[0] == "_"):
        return False
    return all(c.isalnum() or c == "_" for c in key)
