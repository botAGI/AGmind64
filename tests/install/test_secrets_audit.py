"""Tests for the install-time weak/default-secret gate.

Descriptors interpolate ``${VAR:-default}``; if ``VAR`` has no generator the rendered compose
silently ships the weak default (e.g. dify's ``changeme-*`` / ``difyai123456``). The gate
resolves each secret-looking descriptor env value against the rendered ``.env`` and flags any
effective value that is a weak/default placeholder — mirroring the parent's ``_sec_check_weak_env``.
"""

from __future__ import annotations

import pytest

from agmind.install.secrets_audit import find_weak_secret_envs, resolve_env_value
from agmind.schemas import ServiceDescriptor

pytestmark = pytest.mark.backend_any


def _svc(name: str, env: dict[str, str]) -> ServiceDescriptor:
    return ServiceDescriptor(name=name, image="example/x:1", tier="app", env=env)


# ---- resolve_env_value (compose ${VAR} / ${VAR:-default} / ${VAR:?msg}) ----


def test_resolve_default_when_var_missing() -> None:
    assert resolve_env_value("${FOO:-changeme}", {}) == "changeme"


def test_resolve_uses_env_when_present() -> None:
    assert resolve_env_value("${FOO:-changeme}", {"FOO": "strong-value"}) == "strong-value"


def test_resolve_required_syntax() -> None:
    assert resolve_env_value("${FOO:?required}", {"FOO": "v"}) == "v"
    assert resolve_env_value("${FOO:?required}", {}) == ""  # missing required → empty for scan


def test_resolve_embedded_in_url() -> None:
    assert (
        resolve_env_value("redis://:${PW:?x}@redis:6379/1", {"PW": "s3cret"})
        == "redis://:s3cret@redis:6379/1"
    )


def test_resolve_plain_literal() -> None:
    assert resolve_env_value("redis", {}) == "redis"


# ---- find_weak_secret_envs ----


def test_flags_changeme_default_when_generator_missing() -> None:
    descriptors = {
        "dify-api": _svc(
            "dify-api", {"PLUGIN_DAEMON_KEY": "${DIFY_PLUGIN_DAEMON_KEY:-changeme-plugin-daemon-key}"}
        )
    }
    errors = find_weak_secret_envs(descriptors, {})  # generator absent → default leaks
    assert any("dify-api" in e and "PLUGIN_DAEMON_KEY" in e for e in errors)


def test_ok_when_generated_value_present() -> None:
    descriptors = {
        "dify-api": _svc(
            "dify-api", {"PLUGIN_DAEMON_KEY": "${DIFY_PLUGIN_DAEMON_KEY:-changeme-plugin-daemon-key}"}
        )
    }
    env = {"DIFY_PLUGIN_DAEMON_KEY": "x" * 32}
    assert find_weak_secret_envs(descriptors, env) == []


def test_flags_difyai123456_literal_on_secret_key() -> None:
    descriptors = {"svc": _svc("svc", {"API_KEY": "difyai123456"})}
    assert find_weak_secret_envs(descriptors, {}) != []


def test_ignores_non_secret_keys() -> None:
    descriptors = {"svc": _svc("svc", {"REDIS_HOST": "redis", "LOG_LEVEL": "test"})}
    assert find_weak_secret_envs(descriptors, {}) == []


def test_url_with_strong_embedded_password_is_ok() -> None:
    # CELERY_BROKER_URL embeds the generated redis password — not weak.
    descriptors = {
        "worker": _svc("worker", {"CELERY_BROKER_URL": "redis://:${REDIS_PASSWORD:?x}@redis:6379/1"})
    }
    assert find_weak_secret_envs(descriptors, {"REDIS_PASSWORD": "z" * 32}) == []


def test_real_catalog_with_generated_env_has_no_weak_secrets() -> None:
    # The actual install generates DIFY_PLUGIN_* keys, so the shipped catalog must be clean
    # when those generators are present.
    from agmind.install.steps import _RUNTIME_SECRET_KEYS
    from agmind.services.renderer import DEFAULT_SERVICES_DIR, load_descriptors

    descriptors = load_descriptors(DEFAULT_SERVICES_DIR)
    env = {key: "g" * 32 for key in _RUNTIME_SECRET_KEYS}
    env["REDIS_PASSWORD"] = "r" * 32
    env["POSTGRES_PASSWORD"] = "p" * 32
    env["MINIO_ROOT_PASSWORD"] = "m" * 32
    env["MYSQL_ROOT_PASSWORD"] = "y" * 32
    env["GRAFANA_PASSWORD"] = "f" * 32
    assert find_weak_secret_envs(descriptors, env) == []
