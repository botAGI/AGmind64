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
    (
        "komodo-mongo",
        "MONGO_INITDB_ROOT_PASSWORD",
        "MONGO_INITDB_ROOT_PASSWORD_FILE",
        "komodo_mongo_password",
    ),
]


@pytest.mark.parametrize("svc,plain,file_key,fname", _CASES)
def test_db_server_uses_password_file_not_env(svc, plain, file_key, fname) -> None:
    d = load_descriptors()[svc]
    assert plain not in d.env, f"{svc} must not carry the plaintext password in env"
    assert d.env[file_key] == f"/run/secrets/{fname}"
    assert any(f"/var/lib/agmind/secrets/{fname}:/run/secrets/{fname}:ro" == v for v in d.volumes)


def test_installer_writes_db_secret_files() -> None:
    """EnvWriteStep materialize writes the per-DB secret files from runtime_env."""
    import inspect

    from agmind.install import steps

    src = inspect.getsource(steps._materialize_runtime_files)
    for fname, env_key in (
        ("postgres_password", "POSTGRES_PASSWORD"),
        ("mysql_root_password", "MYSQL_ROOT_PASSWORD"),
        ("komodo_mongo_password", "KOMODO_DATABASE_PASSWORD"),
    ):
        assert fname in src and env_key in src
