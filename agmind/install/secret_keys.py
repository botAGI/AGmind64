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
    # Komodo operator console (ops profile). DATABASE_PASSWORD is shared mongo↔core.
    "KOMODO_DATABASE_PASSWORD",
    "KOMODO_INIT_ADMIN_PASSWORD",
    "KOMODO_WEBHOOK_SECRET",
    "KOMODO_JWT_SECRET",
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
        # JWT/webhook signing: rotating only invalidates live sessions / unsent webhooks.
        "KOMODO_JWT_SECRET",
        "KOMODO_WEBHOOK_SECRET",
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
        # mongo sets the root pw on first init of an empty data dir; the admin user is
        # seeded into mongo on core's first boot — rotating the env afterwards is a no-op.
        "KOMODO_DATABASE_PASSWORD",
        "KOMODO_INIT_ADMIN_PASSWORD",
        # Hashed into users_database.yml at materialize; rotating the env alone is a no-op
        # until a re-materialize/re-install regenerates the hash.
        "AUTHELIA_ADMIN_PASSWORD",
    }
)
ENCRYPT_AT_REST = frozenset(
    {"N8N_ENCRYPTION_KEY", "HOMARR_SECRET_ENCRYPTION_KEY", "AUTHELIA_STORAGE_ENCRYPTION_KEY"}
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
