"""Tests для agmind.config.env — render_env + write_env."""

from __future__ import annotations

import stat
from pathlib import Path

import pytest

from agmind.config.env import render_env, write_env

pytestmark = pytest.mark.backend_any


def test_render_env_no_placeholders() -> None:
    assert render_env("static text", {}) == "static text"


def test_render_env_simple_substitution() -> None:
    out = render_env("KEY=${VAL}", {"VAL": "hello"})
    assert out == "KEY=hello"


def test_render_env_multiple_substitutions() -> None:
    out = render_env(
        "A=${X}\nB=${Y}",
        {"X": "1", "Y": "2"},
    )
    assert out == "A=1\nB=2"


def test_render_env_unresolved_placeholder_raises() -> None:
    with pytest.raises(KeyError, match="Unresolved placeholder"):
        render_env("X=${MISSING}", {})


def test_render_env_partial_match_not_substituted() -> None:
    """${X} substituted only as exact placeholder pattern."""
    out = render_env("$X is literal", {"X": "value"})
    assert out == "$X is literal"


def test_render_env_complex_template() -> None:
    tpl = """
# Generated
DB_HOST=${HOST}
DB_PORT=${PORT}
LITERAL=$NOT_A_PLACEHOLDER
"""
    out = render_env(tpl, {"HOST": "localhost", "PORT": "5432"})
    assert "DB_HOST=localhost" in out
    assert "DB_PORT=5432" in out
    assert "$NOT_A_PLACEHOLDER" in out


def test_write_env_default_permissions(tmp_path: Path) -> None:
    p = tmp_path / "out.env"
    write_env(p, "K=v\n")
    assert p.exists()
    assert p.read_text() == "K=v\n"
    assert stat.S_IMODE(p.stat().st_mode) == 0o644


def test_write_env_secret_permissions(tmp_path: Path) -> None:
    p = tmp_path / "secret.env"
    write_env(p, "PASSWORD=top\n", mode=0o600)
    assert stat.S_IMODE(p.stat().st_mode) == 0o600


def test_write_env_creates_parent_dirs(tmp_path: Path) -> None:
    p = tmp_path / "deep" / "nested" / "out.env"
    write_env(p, "K=v\n")
    assert p.exists()


def test_write_env_atomic_replaces_existing(tmp_path: Path) -> None:
    p = tmp_path / "out.env"
    write_env(p, "OLD=1\n")
    write_env(p, "NEW=2\n")
    assert p.read_text() == "NEW=2\n"


def test_write_env_removes_temp_file_and_preserves_existing_on_chmod_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = tmp_path / ".env"
    write_env(p, "OLD_SECRET=keep\n", mode=0o600)

    def fail_chmod(path: str | bytes | Path, mode: int) -> None:
        if Path(path).name == ".env.tmp":
            raise PermissionError("chmod denied")

    monkeypatch.setattr("agmind.config.env.os.chmod", fail_chmod)

    with pytest.raises(PermissionError, match="chmod denied"):
        write_env(p, "NEW_SECRET=drop\n", mode=0o600)

    assert p.read_text(encoding="utf-8") == "OLD_SECRET=keep\n"
    assert not (tmp_path / ".env.tmp").exists()


def test_write_env_accepts_string_path(tmp_path: Path) -> None:
    str_path = str(tmp_path / "string.env")
    write_env(str_path, "K=v\n")
    assert Path(str_path).exists()
