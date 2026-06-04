"""Audit H#1/M#24: Authelia must not ship the upstream EXAMPLE password — a generated
admin password is argon2id-hashed into users_database.yml at install, and the security
audit flags the default if it survives."""

from __future__ import annotations

from pathlib import Path

import pytest

from agmind.install.secret_keys import RUNTIME_SECRET_KEYS, classify
from agmind.install.steps import (
    _authelia_argon2_hash,
    _replace_authelia_password_hash,
    _stage_authelia_config,
)
from agmind.security.audit import scan_authelia_users_db

pytestmark = pytest.mark.backend_any

_UPSTREAM_SALT = "BpLnfgDsc2WD8F2q"


def _default_db(salt: str = _UPSTREAM_SALT) -> str:
    return f"users:\n  admin:\n    password: '$argon2id$v=19$m=65536,t=3,p=2${salt}$x'\n"


def test_admin_password_is_a_generated_secret() -> None:
    assert "AUTHELIA_ADMIN_PASSWORD" in RUNTIME_SECRET_KEYS
    assert classify("AUTHELIA_ADMIN_PASSWORD") in ("rotatable", "init_only")


def test_argon2_hash_is_argon2id() -> None:
    h = _authelia_argon2_hash("a-strong-password")
    assert h.startswith("$argon2id$")


def test_replace_swaps_only_the_password_line() -> None:
    src = _default_db()
    out = _replace_authelia_password_hash(src, "new-pw")
    assert _UPSTREAM_SALT not in out
    assert "$argon2id$" in out
    assert "  admin:" in out  # structure preserved


def test_stage_replaces_default_hash(tmp_path: Path) -> None:
    src = tmp_path / "authelia"
    src.mkdir()
    (src / "configuration.yml").write_text("x: '__AGMIND_DOMAIN__'\n", encoding="utf-8")
    (src / "users_database.yml").write_text(
        _default_db(),
        encoding="utf-8",
    )
    target = tmp_path / "out"
    _stage_authelia_config(src, target, domain="lab.example.com", admin_password="strongpw123")
    db = (target / "users_database.yml").read_text(encoding="utf-8")
    assert _UPSTREAM_SALT not in db
    assert "$argon2id$" in db
    # And the scan no longer flags it.
    assert scan_authelia_users_db(target / "users_database.yml") == []


def test_scan_flags_unreplaced_default(tmp_path: Path) -> None:
    db = tmp_path / "users_database.yml"
    db.write_text(
        _default_db(),
        encoding="utf-8",
    )
    findings = scan_authelia_users_db(db)
    assert len(findings) == 1
    assert findings[0].severity == "high"
    assert findings[0].check == "authelia-default-password"
