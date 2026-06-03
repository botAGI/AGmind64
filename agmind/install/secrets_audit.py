"""Weak/default-secret detection for the install verifier.

Descriptors interpolate ``${VAR:-default}``. If ``VAR`` has no generator in
:mod:`agmind.install.steps` (``_RUNTIME_SECRET_KEYS``), the rendered compose silently ships
the weak default — exactly the dify ``changeme-*`` / ``difyai123456`` class. This module
resolves each secret-looking descriptor env value against the rendered ``.env`` and flags any
effective value that is a weak/default placeholder, so ``agmind verify install`` fails closed
instead of deploying a known-default secret. Mirrors the parent repo's ``_sec_check_weak_env``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

from agmind.schemas import ServiceDescriptor

# ${VAR}, ${VAR:-default}, ${VAR:?message} — compose interpolation forms.
_VAR_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*)|:\?[^}]*)?\}")

# A descriptor env key is "secret-looking" if its name carries one of these tokens.
_SECRET_KEY_RE = re.compile(r"(PASSWORD|SECRET|TOKEN|API_KEY|_KEY|^KEY)", re.IGNORECASE)

# Placeholder substrings that are never acceptable in a real secret value.
_WEAK_SUBSTRINGS = ("changeme", "difyai123456")
# Whole-value defaults (from the parent deny-list) that are too weak to ship.
_WEAK_EXACT = {"admin", "password", "admin123", "test", "123456"}


def resolve_env_value(raw: str, env: Mapping[str, str]) -> str:
    """Resolve compose ``${VAR}`` / ``${VAR:-default}`` / ``${VAR:?msg}`` against ``env``.

    A missing ``${VAR:?msg}`` resolves to empty (compose would fail the deploy — not our concern
    here; an empty value is simply not "weak"). A missing ``${VAR:-default}`` resolves to the
    default (the leak we hunt for).
    """

    def _sub(match: re.Match[str]) -> str:
        var, default = match.group(1), match.group(2)
        value = env.get(var)
        if value:
            return value
        return default if default is not None else ""

    return _VAR_RE.sub(_sub, raw)


def find_weak_secret_envs(
    descriptors: Mapping[str, ServiceDescriptor],
    env: Mapping[str, str],
) -> list[str]:
    """Return one message per secret-looking descriptor env key whose effective value is weak."""
    errors: list[str] = []
    for name in sorted(descriptors):
        for key, raw in descriptors[name].env.items():
            if not _SECRET_KEY_RE.search(key):
                continue
            effective = resolve_env_value(raw, env).lower()
            if not effective:
                continue
            if any(token in effective for token in _WEAK_SUBSTRINGS) or effective in _WEAK_EXACT:
                errors.append(f"{name}.{key} resolves to a weak/default secret")
    return errors
