"""B1 consistency gate: redis server-side auth ↔ every consumer carries credential.

Правила Карпатого #10/#11:
  - redis.yaml MUST have `command:` containing `--requirepass` and `${REDIS_PASSWORD}`.
  - redis.yaml healthcheck MUST authenticate: `--no-auth-warning -a "${REDIS_PASSWORD}"`.
  - redis.yaml MUST NOT carry an `env.REDIS_PASSWORD` block (the official redis image
    ignores it — only Bitnami images read it; shipping it is confusing and misleading).
  - REDIS_PASSWORD is in `_RUNTIME_SECRET_KEYS` AND in both CI compose-validate env heredocs.

Bidirectional consistency rule (§4 of 08-RESEARCH-B1-redis-auth.md):
  - If redis.yaml HAS `--requirepass` → every service with REDIS_HOST or depends_on redis
    MUST carry REDIS_PASSWORD (or a CELERY_BROKER_URL embedding the password).
  - If redis.yaml has NO `--requirepass` → no service MUST carry REDIS_PASSWORD.

dify-sandbox is an explicit allowlist exemption (no redis usage confirmed).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.backend_any

SERVICES_DIR = Path(__file__).resolve().parent.parent.parent / "templates" / "services"
CI_YML = Path(__file__).resolve().parent.parent.parent / ".github" / "workflows" / "ci.yml"
STEPS_PY = Path(__file__).resolve().parent.parent.parent / "agmind" / "install" / "steps.py"

# Services that do NOT need redis credentials even though they may be on the same network.
# dify-sandbox: confirmed no redis env, no depends_on redis.
_SANDBOX_ALLOWLIST = {"dify-sandbox"}


def _load_yaml(name: str) -> dict:
    return yaml.safe_load((SERVICES_DIR / f"{name}.yaml").read_text(encoding="utf-8")) or {}


def _redis_descriptor() -> dict:
    return _load_yaml("redis")


def _all_service_names() -> list[str]:
    return [p.stem for p in sorted(SERVICES_DIR.glob("*.yaml"))]


def _service_uses_redis(descriptor: dict) -> bool:
    """Return True if the service has REDIS_HOST or REDIS_ADDR env OR depends_on redis.

    redis-exporter uses REDIS_ADDR instead of REDIS_HOST — both indicate redis usage.
    """
    env = descriptor.get("env") or {}
    depends_on = descriptor.get("depends_on") or []
    return "REDIS_HOST" in env or "REDIS_ADDR" in env or "redis" in depends_on


def _service_carries_redis_credential(descriptor: dict) -> bool:
    """Return True if the service carries the matching redis credential.

    Acceptable forms:
      - `REDIS_PASSWORD: ${...}` in env (explicit password var)
      - `AUTHELIA_SESSION_REDIS_PASSWORD: ${...}` in env (Authelia uses koanf env mapping)
      - `AUTHELIA_SESSION_REDIS_PASSWORD_FILE: /run/secrets/...` (SPEC-15.4: Authelia now reads
        the session-redis password via the native `_FILE` convention, mounted 0600 — same
        credential, no longer plain env)
      - `CELERY_BROKER_URL: redis://:${...PASSWORD...}@redis...` (embedded in URL)
    """
    env = descriptor.get("env") or {}
    if "REDIS_PASSWORD" in env:
        return True
    # Authelia uses koanf env convention: session.redis.password →
    # AUTHELIA_SESSION_REDIS_PASSWORD (confirmed in 08-RESEARCH-B1 §2e)
    if "AUTHELIA_SESSION_REDIS_PASSWORD" in env:
        return True
    if "AUTHELIA_SESSION_REDIS_PASSWORD_FILE" in env:
        return True
    celery_url = env.get("CELERY_BROKER_URL", "")
    # Must embed a non-empty password in the URL: redis://:${...}@redis...
    if re.search(r"redis://:.*\$\{[^}]*PASSWORD[^}]*\}@redis", celery_url):
        return True
    return False


# ---------------------------------------------------------------------------
# Test 1: redis.yaml MUST have --requirepass in command
# ---------------------------------------------------------------------------


def test_redis_has_requirepass_command() -> None:
    """redis.yaml must have `command: [redis-server, --requirepass, ...]` to activate
    server-side auth. The official redis image ignores REDIS_PASSWORD env — only
    --requirepass activates authentication."""
    desc = _redis_descriptor()
    command = desc.get("command") or []
    command_strs = [str(c) for c in command]
    assert "redis-server" in command_strs, "redis.yaml must include redis-server in command"
    assert "--requirepass" in command_strs, (
        "redis.yaml `command` must include `--requirepass` to activate server-side auth; "
        "the official redis image ignores the REDIS_PASSWORD env var"
    )
    # The password variable must be in the command
    assert any("REDIS_PASSWORD" in tok for tok in command_strs), (
        "redis.yaml `command` must reference `${REDIS_PASSWORD}` after --requirepass"
    )


# ---------------------------------------------------------------------------
# Test 2: healthcheck must authenticate (has -a flag and --no-auth-warning)
# ---------------------------------------------------------------------------


def test_redis_healthcheck_authenticates() -> None:
    """redis.yaml healthcheck must use `redis-cli -a ${REDIS_PASSWORD} ping` form.
    Without auth, the healthcheck will fail once --requirepass is active."""
    desc = _redis_descriptor()
    health = desc.get("health") or {}
    test_cmd = health.get("test") or []
    test_strs = [str(c) for c in test_cmd]
    assert "-a" in test_strs, (
        "redis.yaml healthcheck must include `-a <password>` flag for redis-cli; "
        "without auth the healthcheck fails once --requirepass is active"
    )
    assert "--no-auth-warning" in test_strs, (
        "redis.yaml healthcheck should use `--no-auth-warning` to suppress "
        "redis-cli deprecation warning in stderr"
    )


# ---------------------------------------------------------------------------
# Test 3: redis.yaml MUST NOT carry env.REDIS_PASSWORD (image ignores it)
# ---------------------------------------------------------------------------


def test_redis_no_env_redis_password() -> None:
    """The official redis:8.4.3-alpine image does NOT read REDIS_PASSWORD env var
    (that is a Bitnami convention). Shipping env.REDIS_PASSWORD is misleading and
    gives false confidence that auth is enabled. Remove it."""
    desc = _redis_descriptor()
    env = desc.get("env") or {}
    assert "REDIS_PASSWORD" not in env, (
        "redis.yaml must NOT carry `env.REDIS_PASSWORD` — the official redis image "
        "ignores it (Bitnami-only convention). Auth is activated only via `--requirepass` "
        "in `command:`. Remove the env entry to avoid false confidence."
    )


# ---------------------------------------------------------------------------
# Test 4: Правила §11 — REDIS_PASSWORD in _RUNTIME_SECRET_KEYS + both CI heredocs
# ---------------------------------------------------------------------------


def test_redis_password_in_runtime_secret_keys() -> None:
    """REDIS_PASSWORD must be in _RUNTIME_SECRET_KEYS in steps.py (generator present)."""
    from agmind.install.steps import _RUNTIME_SECRET_KEYS

    assert "REDIS_PASSWORD" in _RUNTIME_SECRET_KEYS, (
        "REDIS_PASSWORD must be in _RUNTIME_SECRET_KEYS in agmind/install/steps.py "
        "so it is generated on every `agmind install` (Правила §10)"
    )


def test_redis_password_in_both_ci_heredocs() -> None:
    """REDIS_PASSWORD must appear in BOTH CI compose-validate and compose-up-smoke
    env heredocs (Правила §11). Parse the file rather than hardcode line numbers."""
    ci_text = CI_YML.read_text(encoding="utf-8")

    # Find both <<'EOF' heredoc blocks (compose-validate + compose-up-smoke)
    heredoc_pattern = re.compile(
        r"cat\s*>\s*/tmp/agmind-compose-ci\.env\s*<<'EOF'(.*?)EOF",
        re.DOTALL,
    )
    blocks = heredoc_pattern.findall(ci_text)
    assert len(blocks) >= 2, (
        f"Expected at least 2 agmind-compose-ci.env heredoc blocks in ci.yml, "
        f"found {len(blocks)}. Both compose-validate and compose-up-smoke must "
        f"populate REDIS_PASSWORD."
    )
    for i, block in enumerate(blocks):
        assert "REDIS_PASSWORD" in block, (
            f"CI heredoc block #{i + 1} does not contain REDIS_PASSWORD=... "
            f"(Правила §11: every required secret must be in BOTH CI env heredocs)"
        )


# ---------------------------------------------------------------------------
# Test 5: Bidirectional consistency — server-auth ↔ all consumers carry credential
# ---------------------------------------------------------------------------


def test_redis_auth_consumer_consistency() -> None:
    """Bidirectional consistency gate (§4 of 08-RESEARCH-B1-redis-auth.md):

    IF redis.yaml has --requirepass:
        EVERY service that uses redis (REDIS_HOST env or depends_on redis)
        MUST carry the matching credential, UNLESS in the sandbox allowlist.
    IF redis.yaml has NO --requirepass:
        NO service that uses redis should carry REDIS_PASSWORD
        (half-auth = misconfiguration in either direction).
    """
    redis_desc = _redis_descriptor()
    command = redis_desc.get("command") or []
    command_strs = [str(c) for c in command]
    redis_has_auth = "--requirepass" in command_strs

    violations: list[str] = []

    for name in _all_service_names():
        if name == "redis":
            continue
        desc = _load_yaml(name)
        uses_redis = _service_uses_redis(desc)
        carries_credential = _service_carries_redis_credential(desc)

        if not uses_redis:
            continue  # Not a redis consumer — not relevant

        in_allowlist = name in _SANDBOX_ALLOWLIST

        if redis_has_auth:
            # Server requires auth → all consumers MUST carry credential (unless allowlisted)
            if not carries_credential and not in_allowlist:
                violations.append(
                    f"{name}: redis has --requirepass but this service lacks "
                    f"REDIS_PASSWORD env (or CELERY_BROKER_URL with embedded password)"
                )
        else:
            # Server has no auth → consumers MUST NOT send a password (half-auth is a bug)
            if carries_credential:
                violations.append(
                    f"{name}: redis has NO --requirepass but this service carries "
                    f"REDIS_PASSWORD — remove it for consistency (half-auth = misconfiguration)"
                )

    assert not violations, (
        "Redis auth consistency violations (server↔consumer parity failed):\n"
        + "\n".join(f"  - {v}" for v in violations)
    )


# ---------------------------------------------------------------------------
# Test 6: dify-worker CELERY_BROKER_URL embeds the password with correct form
# ---------------------------------------------------------------------------


def test_dify_worker_celery_broker_url_embeds_password() -> None:
    """Celery does NOT read REDIS_PASSWORD — it reads only CELERY_BROKER_URL.
    The URL must embed the credential as redis://:${REDIS_PASSWORD}@redis:6379/1
    (note the `:` empty-username prefix, per dify upstream shared.env.example)."""
    env = _load_yaml("dify-worker").get("env") or {}
    assert "CELERY_BROKER_URL" in env, (
        "dify-worker must have CELERY_BROKER_URL — Celery does not read REDIS_PASSWORD; "
        "the redis broker URL must carry the credential"
    )
    url = env["CELERY_BROKER_URL"]
    # Must match redis://:${...PASSWORD...}@redis pattern (empty username, password embedded)
    assert re.search(r"redis://:.*\$\{[^}]*REDIS_PASSWORD[^}]*\}@redis", url), (
        f"CELERY_BROKER_URL must embed the password as `redis://:${{REDIS_PASSWORD}}@redis...`; "
        f"got: {url!r}. The `:` before the password is required (empty username, "
        f"password follows). See dify upstream shared.env.example."
    )


# ---------------------------------------------------------------------------
# Test 7: dify-api ↔ dify-worker run-the-same-generation config parity
# ---------------------------------------------------------------------------


def test_dify_api_worker_generation_config_parity() -> None:
    """The worker runs advanced-chat/workflow generation (dispatched via the Celery broker),
    so it must share dify-api's plugin-daemon handshake AND code-execution config. A subset
    asymmetry is a silent deploy-blocker: chatflow/workflow apps enqueue to the broker, the
    worker picks them up, then fails to resolve the model ("Failed to request plugin daemon")
    or run Code nodes ("sandbox: Name or service not known"). The handshake keys MUST be
    byte-identical so they match at runtime. 2026-06-10 live blocker."""
    api = _load_yaml("dify-api").get("env") or {}
    worker = _load_yaml("dify-worker").get("env") or {}

    # Both enqueue/consume via the broker; missing it on either side = "Failed to enqueue".
    for svc_name, env in (("dify-api", api), ("dify-worker", worker)):
        assert "CELERY_BROKER_URL" in env, (
            f"{svc_name} must carry CELERY_BROKER_URL — without it the advanced-chat/workflow "
            f"streaming-task dispatch falls back to Celery's default amqp://localhost (no broker)"
        )

    # These keys MUST be present AND identical on both sides (runtime handshake/endpoint).
    shared = [
        "PLUGIN_DAEMON_URL",
        "PLUGIN_DAEMON_KEY",
        "INNER_API_KEY_FOR_PLUGIN",
        "CODE_EXECUTION_ENDPOINT",
        "CODE_EXECUTION_API_KEY",
    ]
    mismatches: list[str] = []
    for key in shared:
        if key not in api:
            mismatches.append(f"dify-api missing {key}")
        if key not in worker:
            mismatches.append(f"dify-worker missing {key}")
        if key in api and key in worker and api[key] != worker[key]:
            mismatches.append(f"{key} differs: api={api[key]!r} worker={worker[key]!r}")
    assert not mismatches, (
        "dify-api ↔ dify-worker generation-config parity failed (the worker runs workflow "
        "generation and must share api's plugin-daemon + code-exec config):\n"
        + "\n".join(f"  - {m}" for m in mismatches)
    )


def test_dify_api_worker_on_ssrf_net_for_code_exec() -> None:
    """Workflow Code nodes execute in dify-sandbox, caged on the internal-only ssrf-net. Both
    dify-api (editor single-step debug runs) and dify-worker (full workflow generation) must
    join ssrf-net to reach http://dify-sandbox:8194, else Code nodes fail with "sandbox: Name
    or service not known". 2026-06-10 live blocker."""
    for name in ("dify-api", "dify-worker"):
        nets = _load_yaml(name).get("networks") or []
        assert "ssrf-net" in nets, (
            f"{name} must be on ssrf-net to reach the caged dify-sandbox for Code-node "
            f"execution; got networks={nets}"
        )
