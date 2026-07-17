"""Single source of truth for the generated runtime secret keys + their generators.

Both the installer (``EnvWriteStep`` via ``_runtime_env``) and
``agmind ops rotate-secrets`` use these, so the two cannot drift on which keys
exist, how they are generated (e.g. homarr needs 64-hex, Authelia needs 64-char),
or which rotation bucket each falls into.

Rotation buckets (Правила: rotating the wrong secret is worse than not rotating):
- ``rotatable``       — auth-cred / shared handshake; safe to rotate if every
  holder is force-recreated together.
- ``init_only``       — the image sets the password only on first init of an
  empty data dir; rotating the env is a no-op and breaks auth until an in-DB
  reset (ALTER USER / admin reset) is run.
- ``encrypt_at_rest`` — the key decrypts data at rest; rotating renders existing
  data permanently undecryptable.
"""

from __future__ import annotations

from agmind.core.secrets import generate_hex_secret, generate_secret

RUNTIME_SECRET_KEYS: tuple[str, ...] = (
    "POSTGRES_PASSWORD",
    "GRAFANA_PASSWORD",
    "MYSQL_ROOT_PASSWORD",
    "MINIO_ROOT_PASSWORD",
    "REDIS_PASSWORD",
    # Elasticsearch xpack.security bootstrap password (the `elastic` superuser). RAGFlow auths
    # to ES with it (ES_USER defaults to elastic). live-audit 2026-06-05 elasticsearch-xpack-disabled.
    "ELASTIC_PASSWORD",
    # milvus-minio root password — SEPARATE trust domain from the app minio's MINIO_ROOT_PASSWORD
    # (live-audit 2026-06-05 shared-minio-root-cred-two-trust-domains).
    "MILVUS_MINIO_ROOT_PASSWORD",
    "N8N_ENCRYPTION_KEY",
    "HOMARR_SECRET_ENCRYPTION_KEY",
    # Authelia first-admin login password — hashed (argon2id) into users_database.yml at
    # materialize so the SSO never ships the upstream example password. Surfaced plaintext
    # in credentials.txt for the operator. (The AUTHELIA_* 64-char keys below are different
    # — those are the session/storage/jwt secrets read as env.)
    "AUTHELIA_ADMIN_PASSWORD",
    # Dify plugin-daemon ↔ dify-api inner-API handshake (must be shared + generated).
    "DIFY_PLUGIN_DAEMON_KEY",
    "DIFY_PLUGIN_INNER_API_KEY",
    # agent-db (pgvector) root password — its OWN trust domain (separate from the dify postgres).
    # Read by the agent apps as ${AGENT_DB_PASSWORD}; written to a 0600 FILE for the DB server.
    "AGENT_DB_PASSWORD",
)

# Authelia required secrets (read by the container as AUTHELIA_* env). 64-char so
# Authelia's length warnings are satisfied; the redis session password reuses REDIS_PASSWORD.
AUTHELIA_SECRET_KEYS: tuple[str, ...] = (
    "AUTHELIA_SESSION_SECRET",
    "AUTHELIA_STORAGE_ENCRYPTION_KEY",
    "AUTHELIA_IDENTITY_VALIDATION_RESET_PASSWORD_JWT_SECRET",
)

ALL_GENERATED_SECRET_KEYS: tuple[str, ...] = RUNTIME_SECRET_KEYS + AUTHELIA_SECRET_KEYS

# homarr's SECRET_ENCRYPTION_KEY must be EXACTLY 64 hex chars (the base64 default
# makes homarr abort at boot). Everything else: 32-byte token, Authelia 64-char.
_HEX_SECRET_KEYS = frozenset({"HOMARR_SECRET_ENCRYPTION_KEY"})

ROTATABLE = frozenset(
    {
        "REDIS_PASSWORD",
        "DIFY_PLUGIN_DAEMON_KEY",
        "DIFY_PLUGIN_INNER_API_KEY",
        "AUTHELIA_SESSION_SECRET",
        "AUTHELIA_IDENTITY_VALIDATION_RESET_PASSWORD_JWT_SECRET",
    }
)
INIT_ONLY = frozenset(
    {
        "POSTGRES_PASSWORD",
        "MYSQL_ROOT_PASSWORD",
        "MINIO_ROOT_PASSWORD",
        "GRAFANA_PASSWORD",
        # ES bootstraps the `elastic` password only on first init of an empty data dir;
        # rotating the env afterwards is a no-op (needs elasticsearch-reset-password).
        "ELASTIC_PASSWORD",
        # MinIO sets the root password on first init of an empty data dir (same as the app minio).
        "MILVUS_MINIO_ROOT_PASSWORD",
        # Hashed into users_database.yml at materialize; rotating the env alone is a no-op
        # until a re-materialize/re-install regenerates the hash.
        "AUTHELIA_ADMIN_PASSWORD",
        # pgvector sets the agent-db root password only on first init of an empty data dir;
        # rotating the env afterwards is a no-op (needs an in-DB ALTER USER reset).
        "AGENT_DB_PASSWORD",
    }
)
ENCRYPT_AT_REST = frozenset(
    {"N8N_ENCRYPTION_KEY", "HOMARR_SECRET_ENCRYPTION_KEY", "AUTHELIA_STORAGE_ENCRYPTION_KEY"}
)

# DB SERVERS that read their password from a 0600 secret FILE (db-secrets→FILE) rather than env.
# Single source of truth for: install materialization (_materialize_runtime_files) AND
# rotate-secrets (which must re-write the FILE + force-recreate the server, not just rewrite .env —
# the server references the secret via *_PASSWORD_FILE, a literal path, so it is invisible to the
# ${VAR}-scanning secret_consumers). (service, secret_filename, env_key). live-audit 2026-06-07
# rotation-desyncs-the-db-secret-FILE (SEC-3).
DB_SECRET_FILES: tuple[tuple[str, str, str], ...] = (
    ("postgres", "postgres_password", "POSTGRES_PASSWORD"),
    ("mysql", "mysql_root_password", "MYSQL_ROOT_PASSWORD"),
    # agent-db (pgvector) reads POSTGRES_PASSWORD_FILE while still root — no reader-uid entry.
    ("agent-db", "agent_db_password", "AGENT_DB_PASSWORD"),
)

# Secret FILES that the consuming image reads AFTER dropping to a non-root uid, so a root:root 0600
# file is unreadable (EACCES → crash-loop). For such an image, chown the file to the reader uid
# (keeping 0600) so only that uid + root can read it. postgres/mysql read their *_FILE while still
# root, so they need NO entry here. {secret_filename: reader_uid}. Currently empty — no DB server in
# the catalog drops to a non-root uid before reading its password file.
DB_SECRET_FILE_READER_UID: dict[str, int] = {}

# CONSUMER (not DB-server) secrets read from a 0600 file via the image's native `_FILE`
# convention. Authelia is a consumer of these secret values, not the server that owns them
# (the session-redis entry reuses REDIS_PASSWORD; redis itself still gets it via --requirepass).
# Single source of truth for: install materialization (_materialize_runtime_files) AND
# rotate-secrets (which must re-write the FILE + force-recreate authelia, not just rewrite .env —
# authelia references the secret via *_FILE, a literal path, invisible to the ${VAR}-scanning
# secret_consumers walk) — exact parity with DB_SECRET_FILES, SPEC-15.4 (closes the SEC-3 desync
# class for authelia). (service, secret_filename, env_key).
AUTHELIA_SECRET_FILES: tuple[tuple[str, str, str], ...] = (
    ("authelia", "authelia_session_secret", "AUTHELIA_SESSION_SECRET"),
    ("authelia", "authelia_storage_encryption_key", "AUTHELIA_STORAGE_ENCRYPTION_KEY"),
    (
        "authelia",
        "authelia_reset_jwt_secret",
        "AUTHELIA_IDENTITY_VALIDATION_RESET_PASSWORD_JWT_SECRET",
    ),
    ("authelia", "authelia_session_redis_password", "REDIS_PASSWORD"),
)


def generate_for(key: str) -> str:
    """Generate a fresh value for ``key`` using the installer's exact format."""
    if key in _HEX_SECRET_KEYS:
        return generate_hex_secret(32)
    if key in AUTHELIA_SECRET_KEYS:
        return generate_secret(64)
    return generate_secret(32)


def classify(key: str) -> str:
    """Return the rotation bucket for ``key`` (or ``"unknown"``)."""
    if key in ROTATABLE:
        return "rotatable"
    if key in INIT_ONLY:
        return "init_only"
    if key in ENCRYPT_AT_REST:
        return "encrypt_at_rest"
    return "unknown"
