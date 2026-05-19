"""Tests для agmind._env — .env file parser без python-dotenv."""

from __future__ import annotations

from pathlib import Path

import pytest

from agmind._env import env_get, parse_env_file, parse_env_text, shell_quote

pytestmark = pytest.mark.backend_any


def test_parse_env_text_simple() -> None:
    assert parse_env_text("FOO=bar") == {"FOO": "bar"}


def test_parse_env_text_export_prefix() -> None:
    assert parse_env_text("export FOO=bar") == {"FOO": "bar"}


def test_parse_env_text_quoted_values() -> None:
    text = '''
FOO="hello world"
BAR='literal $string'
BAZ=unquoted
'''
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


def test_shell_quote_simple() -> None:
    assert shell_quote("simple") == "simple"


def test_shell_quote_with_spaces() -> None:
    assert "'" in shell_quote("with spaces")


def test_shell_quote_with_special_chars() -> None:
    assert shell_quote("$VAR;rm -rf /") != "$VAR;rm -rf /"
