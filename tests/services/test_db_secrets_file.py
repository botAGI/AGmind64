"""Live-audit 2026-06-05 (MED db-secrets-plaintext-docker-inspect / secrets-plaintext-env): DB
SERVERS read their password from a 0600 file (*_PASSWORD_FILE), not env, so the secret is not in
`docker inspect` / the socket-proxy inspect of the server container. Consumers keep the env var
(their images lack _FILE); the installer writes the file FROM the same generated secret."""

from __future__ import annotations

import pytest

from agmind.services.renderer import load_descriptors

pytestmark = pytest.mark.backend_any

_CASES = [
    ("postgres", "POSTGRES_PASSWORD", "POSTGRES_PASSWORD_FILE", "postgres_password"),
    ("mysql", "MYSQL_ROOT_PASSWORD", "MYSQL_ROOT_PASSWORD_FILE", "mysql_root_password"),
]


@pytest.mark.parametrize("svc,plain,file_key,fname", _CASES)
def test_db_server_uses_password_file_not_env(svc, plain, file_key, fname) -> None:
    d = load_descriptors()[svc]
    assert plain not in d.env, f"{svc} must not carry the plaintext password in env"
    assert d.env[file_key] == f"/run/secrets/{fname}"
    assert any(f"/var/lib/agmind/secrets/{fname}:/run/secrets/{fname}:ro" == v for v in d.volumes)


def test_installer_writes_db_secret_files() -> None:
    """The single-source DB_SECRET_FILES mapping drives both install materialization and
    rotate-secrets re-materialization (live-audit 2026-06-07 SEC-3)."""
    import inspect

    from agmind.install import steps
    from agmind.install.secret_keys import DB_SECRET_FILES

    expected = {
        ("postgres", "postgres_password", "POSTGRES_PASSWORD"),
        ("mysql", "mysql_root_password", "MYSQL_ROOT_PASSWORD"),
        ("agent-db", "agent_db_password", "AGENT_DB_PASSWORD"),
    }
    assert set(DB_SECRET_FILES) == expected
    # _materialize_runtime_files consumes the shared constant (not an inline duplicate)
    assert "DB_SECRET_FILES" in inspect.getsource(steps._materialize_runtime_files)
