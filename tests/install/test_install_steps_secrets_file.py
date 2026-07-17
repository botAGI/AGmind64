"""SPEC-15.4/D-04: Authelia's 4 secrets move from plain env to the native `_FILE` convention
(0600 files, mounted :ro) — narrows the docker-inspect / /proc-environ-visible plaintext
surface. AUTHELIA_SECRET_FILES (agmind/install/secret_keys.py) is the single source of truth
consumed by BOTH install materialization (_materialize_runtime_files) and `agmind ops
rotate-secrets`, mirroring the existing DB_SECRET_FILES pattern — without this parity a
rotated env leaves the FILE stale and desyncs auth (the SEC-3 class DB_SECRET_FILES already
fixed for DB servers)."""

from __future__ import annotations

import inspect
import stat
from pathlib import Path

import pytest

pytestmark = pytest.mark.backend_any

_EXPECTED = {
    ("authelia", "authelia_session_secret", "AUTHELIA_SESSION_SECRET"),
    ("authelia", "authelia_storage_encryption_key", "AUTHELIA_STORAGE_ENCRYPTION_KEY"),
    (
        "authelia",
        "authelia_reset_jwt_secret",
        "AUTHELIA_IDENTITY_VALIDATION_RESET_PASSWORD_JWT_SECRET",
    ),
    ("authelia", "authelia_session_redis_password", "REDIS_PASSWORD"),
}


def test_authelia_secret_files_registry_shape() -> None:
    from agmind.install.secret_keys import AUTHELIA_SECRET_FILES

    assert set(AUTHELIA_SECRET_FILES) == _EXPECTED
    assert len(AUTHELIA_SECRET_FILES) == 4


def test_materialize_runtime_files_consumes_the_shared_registry() -> None:
    """_materialize_runtime_files must consume AUTHELIA_SECRET_FILES (not an inline
    duplicate) — the same discipline test_db_secrets_file.py enforces for DB_SECRET_FILES."""
    from agmind.install import steps

    assert "AUTHELIA_SECRET_FILES" in inspect.getsource(steps._materialize_runtime_files)


def _cfg(tmp_path: Path, services: list[str]):
    from agmind.install.orchestrator import InstallConfig

    return InstallConfig(
        domain="lab.example.com",
        cf_api_token="",
        services=services,
        install_dir=tmp_path / "opt",
        models_dir=tmp_path / "var" / "models",
        config_dir=tmp_path / "etc" / "agmind",
    )


def test_materialize_writes_authelia_secret_files_when_selected(tmp_path: Path) -> None:
    from agmind.install.steps import _materialize_runtime_files

    cfg = _cfg(tmp_path, ["authelia", "redis"])
    runtime_env = {
        "AUTHELIA_SESSION_SECRET": "sess-value-0123456789",
        "AUTHELIA_STORAGE_ENCRYPTION_KEY": "storage-value-0123456789",
        "AUTHELIA_IDENTITY_VALIDATION_RESET_PASSWORD_JWT_SECRET": "jwt-value-0123456789",
        "REDIS_PASSWORD": "redis-pw-0123456789",
    }
    _materialize_runtime_files(cfg, runtime_env, lambda _e: None, "x")

    secrets_dir = tmp_path / "var" / "secrets"
    expected_files = {
        "authelia_session_secret": "sess-value-0123456789",
        "authelia_storage_encryption_key": "storage-value-0123456789",
        "authelia_reset_jwt_secret": "jwt-value-0123456789",
        "authelia_session_redis_password": "redis-pw-0123456789",
    }
    for fname, value in expected_files.items():
        path = secrets_dir / fname
        assert path.is_file(), f"{fname} was not written"
        # Byte-for-byte plain string — NOT JSON, NOT a wrapped/quoted value.
        assert path.read_text(encoding="utf-8") == value
        mode = stat.S_IMODE(path.stat().st_mode)
        assert mode == 0o600, f"{fname} must be 0600, got {oct(mode)}"


def test_materialize_skips_authelia_secret_files_when_not_selected(tmp_path: Path) -> None:
    from agmind.install.steps import _materialize_runtime_files

    cfg = _cfg(tmp_path, ["redis"])  # no authelia
    runtime_env = {
        "AUTHELIA_SESSION_SECRET": "sess-value-0123456789",
        "AUTHELIA_STORAGE_ENCRYPTION_KEY": "storage-value-0123456789",
        "AUTHELIA_IDENTITY_VALIDATION_RESET_PASSWORD_JWT_SECRET": "jwt-value-0123456789",
        "REDIS_PASSWORD": "redis-pw-0123456789",
    }
    _materialize_runtime_files(cfg, runtime_env, lambda _e: None, "x")

    secrets_dir = tmp_path / "var" / "secrets"
    for fname in (
        "authelia_session_secret",
        "authelia_storage_encryption_key",
        "authelia_reset_jwt_secret",
        "authelia_session_redis_password",
    ):
        assert not (secrets_dir / fname).exists(), f"{fname} must not be written without authelia"


def test_authelia_descriptor_uses_file_env_with_ro_mounts() -> None:
    from agmind.services.renderer import load_descriptors

    d = load_descriptors()["authelia"]
    expected_env = {
        "AUTHELIA_SESSION_SECRET_FILE": "/run/secrets/authelia_session_secret",
        "AUTHELIA_STORAGE_ENCRYPTION_KEY_FILE": "/run/secrets/authelia_storage_encryption_key",
        "AUTHELIA_IDENTITY_VALIDATION_RESET_PASSWORD_JWT_SECRET_FILE": (
            "/run/secrets/authelia_reset_jwt_secret"
        ),
        "AUTHELIA_SESSION_REDIS_PASSWORD_FILE": "/run/secrets/authelia_session_redis_password",
    }
    for key, value in expected_env.items():
        assert d.env[key] == value
    # No plain-env variant survives.
    for plain_key in (
        "AUTHELIA_SESSION_SECRET",
        "AUTHELIA_STORAGE_ENCRYPTION_KEY",
        "AUTHELIA_IDENTITY_VALIDATION_RESET_PASSWORD_JWT_SECRET",
        "AUTHELIA_SESSION_REDIS_PASSWORD",
    ):
        assert plain_key not in d.env

    for fname in (
        "authelia_session_secret",
        "authelia_storage_encryption_key",
        "authelia_reset_jwt_secret",
        "authelia_session_redis_password",
    ):
        assert f"/var/lib/agmind/secrets/{fname}:/run/secrets/{fname}:ro" in d.volumes, (
            f"missing :ro secret mount for {fname}"
        )
