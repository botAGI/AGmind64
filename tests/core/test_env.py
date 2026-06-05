"""Tests для agmind._env — .env file parser без python-dotenv."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from agmind.core.env import (
    env_get,
    parse_env_file,
    parse_env_file_or_empty,
    parse_env_text,
    shell_quote,
)
from agmind.install.steps import _env_line

pytestmark = pytest.mark.backend_any


def test_parse_env_file_or_empty_missing_and_present(tmp_path: Path) -> None:
    assert parse_env_file_or_empty(tmp_path / "absent.env") == {}
    f = tmp_path / ".env"
    f.write_text("A=1\nB=two\n", encoding="utf-8")
    assert parse_env_file_or_empty(f) == {"A": "1", "B": "two"}


@pytest.mark.skipif(os.geteuid() == 0, reason="root bypasses file read perms")
def test_parse_env_file_or_empty_unreadable_returns_empty_not_raise(tmp_path: Path) -> None:
    """Review (live deploy): /opt/agmind/.env is root:root 0600 — display commands use this
    helper so they degrade to {} instead of crashing; bare parse_env_file still raises."""
    f = tmp_path / ".env"
    f.write_text("SECRET=x\n", encoding="utf-8")
    os.chmod(f, 0o000)
    try:
        assert parse_env_file_or_empty(f) == {}
        with pytest.raises(OSError):
            parse_env_file(f)  # the strict variant still surfaces the permission error
    finally:
        os.chmod(f, 0o600)


def test_parse_env_text_simple() -> None:
    assert parse_env_text("FOO=bar") == {"FOO": "bar"}


def test_parse_env_text_export_prefix() -> None:
    assert parse_env_text("export FOO=bar") == {"FOO": "bar"}


def test_parse_env_text_quoted_values() -> None:
    text = """
FOO="hello world"
BAR='literal $string'
BAZ=unquoted
"""
    out = parse_env_text(text)
    assert out["FOO"] == "hello world"
    assert out["BAR"] == "literal $string"
    assert out["BAZ"] == "unquoted"


def test_parse_env_text_comment_lines_ignored() -> None:
    text = """
# top comment
FOO=bar
# another
BAZ=qux
"""
    out = parse_env_text(text)
    assert out == {"FOO": "bar", "BAZ": "qux"}


def test_parse_env_text_trailing_comment_unquoted() -> None:
    """Trailing # is stripped from unquoted values."""
    out = parse_env_text("FOO=bar # trailing comment")
    assert out["FOO"] == "bar"


def test_parse_env_text_trailing_comment_quoted_preserved() -> None:
    """В кавычках # — часть значения."""
    out = parse_env_text('FOO="bar # not a comment"')
    assert out["FOO"] == "bar # not a comment"


def test_parse_env_text_invalid_lines_skipped() -> None:
    out = parse_env_text("noequalssign\nFOO=bar\n  \n")
    assert out == {"FOO": "bar"}


def test_parse_env_text_key_with_underscore_and_digits() -> None:
    out = parse_env_text("_PRIVATE=1\nNAME_2=value")
    assert out == {"_PRIVATE": "1", "NAME_2": "value"}


def test_parse_env_text_value_with_spaces_around() -> None:
    out = parse_env_text("FOO=  spaced  ")
    assert out["FOO"] == "spaced"


def test_parse_env_file_missing_returns_empty(tmp_path: Path) -> None:
    assert parse_env_file(tmp_path / "missing.env") == {}


def test_parse_env_file_reads_disk(tmp_path: Path) -> None:
    p = tmp_path / "x.env"
    p.write_text("KEY=value\n", encoding="utf-8")
    assert parse_env_file(p) == {"KEY": "value"}


def test_env_get_explicit_path(tmp_path: Path) -> None:
    p = tmp_path / "explicit.env"
    p.write_text("A=1\nB=2\n", encoding="utf-8")
    assert env_get("A", env_file=p) == "1"
    assert env_get("B", env_file=p) == "2"
    assert env_get("MISSING", env_file=p, default="fallback") == "fallback"


def test_env_get_default_when_file_missing(tmp_path: Path) -> None:
    assert env_get("X", env_file=tmp_path / "no.env", default="def") == "def"


def test_env_line_roundtrips_space_and_embedded_quote() -> None:
    """G.7: a value with a space AND an embedded quote round-trips writer→reader.

    docker-compose env-file semantics: the writer (`_env_line`) and reader
    (`parse_env_text`) must agree. The current `shlex.quote` writer produces
    POSIX shell single-quote escaping (e.g. ``'a '\\''b'`` for ``a 'b``) which
    the single-layer-strip reader does NOT undo, so the value is corrupted on
    round-trip. This must hold for a value carrying a space + both quote kinds.
    """
    v = """has space and a " and a ' quote"""
    assert parse_env_text(_env_line("FOO", v))["FOO"] == v


def test_env_line_roundtrips_space_only() -> None:
    """A value with only a space (no quote) round-trips writer→reader."""
    v = "has space only"
    assert parse_env_text(_env_line("FOO", v))["FOO"] == v


def test_env_line_roundtrips_embedded_double_quote() -> None:
    """A value with an embedded double quote round-trips via escaping."""
    v = 'embedded "quote" here'
    assert parse_env_text(_env_line("FOO", v))["FOO"] == v


def test_env_line_rejects_literal_newline_value() -> None:
    with pytest.raises(ValueError, match="literal newline"):
        _env_line("SECRET", "line1\nline2")


def test_env_line_simple_value_byte_identical_bare() -> None:
    """G.7-a idempotency: simple alnum/_- values stay bare (no quotes).

    `_runtime_env` secrets (token_urlsafe), image tags, and digests are
    [A-Za-z0-9_-]/`.`/`:` — they MUST keep emitting unquoted exactly as today
    or every install churns `.env`/`version.env`.
    """
    assert _env_line("K", "abc_DEF-123") == "K=abc_DEF-123"


def test_env_line_token_urlsafe_shaped_value_bare() -> None:
    """A token_urlsafe-shaped secret (alnum/_-) emits bare and round-trips."""
    token = "Xy9_aB-cD3fG7hJ_kL2mN-pQ5rS8tU0vW1xY4zZ6"
    line = _env_line("SECRET", token)
    assert line == f"SECRET={token}"
    assert parse_env_text(line)["SECRET"] == token


def test_env_line_image_tag_and_digest_bare() -> None:
    """Image tags and sha256 digests stay byte-identical (bare)."""
    assert _env_line("V", "1.14.2") == "V=1.14.2"
    digest = "sha256:" + "a" * 64
    assert _env_line("D", digest) == f"D={digest}"


def test_shell_quote_simple() -> None:
    assert shell_quote("simple") == "simple"


def test_shell_quote_with_spaces() -> None:
    assert "'" in shell_quote("with spaces")


def test_shell_quote_with_special_chars() -> None:
    assert shell_quote("$VAR;rm -rf /") != "$VAR;rm -rf /"
