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
    "N8N_ENCRYPTION_KEY",
    "HOMARR_SECRET_ENCRYPTION_KEY",
    # Dify plugin-daemon ↔ dify-api inner-API handshake (must be shared + generated).
    "DIFY_PLUGIN_DAEMON_KEY",
    "DIFY_PLUGIN_INNER_API_KEY",
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
    {"POSTGRES_PASSWORD", "MYSQL_ROOT_PASSWORD", "MINIO_ROOT_PASSWORD", "GRAFANA_PASSWORD"}
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
