"""Tests для agmind.secrets — credentials.txt с chmod 600."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from agmind.secrets import (
    _MASK,
    _is_valid_key,
    generate_secret,
    get_creds_path,
    mask_value,
    read_creds,
    write_creds,
)

pytestmark = pytest.mark.backend_any


def test_generate_secret_default_length() -> None:
    s = generate_secret()
    assert len(s) >= 32  # token_urlsafe returns >= length чарактеров


def test_generate_secret_custom_length() -> None:
    s = generate_secret(length=16)
    assert len(s) >= 16


def test_generate_secret_uniqueness() -> None:
    assert generate_secret() != generate_secret()


def test_get_creds_path_default() -> None:
    os.environ.pop("AGMIND_CREDENTIALS_PATH", None)
    assert str(get_creds_path()) == "/opt/agmind/credentials.txt"


def test_get_creds_path_env_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AGMIND_CREDENTIALS_PATH", str(tmp_path / "creds.txt"))
    assert get_creds_path() == tmp_path / "creds.txt"


def test_write_creds_creates_file_chmod_600(tmp_path: Path) -> None:
    p = tmp_path / "creds.txt"
    write_creds({"DB_PASS": "supersecret"}, path=p)
    assert p.exists()
    mode = stat.S_IMODE(p.stat().st_mode)
    assert mode == 0o600


def test_write_creds_creates_parent_dirs(tmp_path: Path) -> None:
    p = tmp_path / "deep" / "nested" / "creds.txt"
    write_creds({"K": "v"}, path=p)
    assert p.exists()


def test_write_creds_atomic_replaces_existing(tmp_path: Path) -> None:
    p = tmp_path / "creds.txt"
    write_creds({"K1": "old"}, path=p)
    write_creds({"K2": "new"}, path=p)
    content = p.read_text()
    assert "K2=new" in content
    assert "K1=old" not in content


def test_write_creds_rejects_invalid_key(tmp_path: Path) -> None:
    p = tmp_path / "creds.txt"
    with pytest.raises(ValueError, match="Invalid credential key"):
        write_creds({"invalid-key": "v"}, path=p)
    with pytest.raises(ValueError):
        write_creds({"1starts_with_digit": "v"}, path=p)
    with pytest.raises(ValueError):
        write_creds({"has space": "v"}, path=p)


def test_read_creds_missing_returns_empty(tmp_path: Path) -> None:
    assert read_creds(tmp_path / "missing.txt") == {}


def test_read_creds_roundtrip(tmp_path: Path) -> None:
    p = tmp_path / "creds.txt"
    src = {"DB_PASS": "secret123", "API_KEY": "abc-xyz"}
    write_creds(src, path=p)
    assert read_creds(p) == src


def test_read_creds_skips_comments(tmp_path: Path) -> None:
    p = tmp_path / "creds.txt"
    p.write_text("# header\nKEY=value\n# trailing\n", encoding="utf-8")
    os.chmod(p, 0o600)
    assert read_creds(p) == {"KEY": "value"}


def test_read_creds_rejects_loose_permissions(tmp_path: Path) -> None:
    p = tmp_path / "creds.txt"
    p.write_text("K=v\n", encoding="utf-8")
    os.chmod(p, 0o644)  # too permissive
    with pytest.raises(PermissionError, match="expected 0o600"):
        read_creds(p)


def test_mask_value_empty() -> None:
    assert mask_value("") == _MASK


def test_mask_value_short() -> None:
    assert mask_value("abc") == _MASK


def test_mask_value_long_keeps_prefix() -> None:
    out = mask_value("super-secret-1234", keep=4)
    assert out.startswith("supe")
    assert _MASK in out
    assert "secret" not in out


def test_is_valid_key_empty() -> None:
    assert _is_valid_key("") is False


def test_is_valid_key_letters_and_digits() -> None:
    assert _is_valid_key("KEY_123") is True


def test_is_valid_key_starts_with_underscore() -> None:
    assert _is_valid_key("_PRIVATE") is True


def test_is_valid_key_starts_with_digit() -> None:
    assert _is_valid_key("1FOO") is False


def test_is_valid_key_with_dash() -> None:
    assert _is_valid_key("kebab-case") is False


def test_is_valid_key_with_space() -> None:
    assert _is_valid_key("has space") is False
