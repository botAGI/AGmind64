"""Pure logic for `agmind ops rotate-secrets` — selective .env secret rotation.

All filesystem / docker I/O lives in the CLI layer (``agmind.cli.ops_cmd``); this
module is hardware-free and fully unit-testable: classify which secrets to rotate
(4-bucket taxonomy from :mod:`agmind.install.secret_keys`), regenerate with the
installer's exact generators, rewrite the .env in place preserving everything
else, and derive each secret's consuming services (so the CLI can
``up -d --force-recreate`` every holder atomically — `restart` keeps the old env).
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from agmind.core.env import compose_env_quote
from agmind.install.secret_keys import (
    ALL_GENERATED_SECRET_KEYS,
    classify,
    generate_for,
)

_VAR_REF_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)")


@dataclass(frozen=True)
class RotationPlan:
    rotate: tuple[str, ...]
    skipped_init: tuple[str, ...]
    refused_encrypt: tuple[str, ...]
    warnings: tuple[str, ...]


def plan_rotation(
    env: Mapping[str, str],
    *,
    include: Sequence[str] = (),
    force_destructive: bool = False,
) -> RotationPlan:
    """Classify each present generated secret into rotate / skip / refuse.

    Default rotates only the ``rotatable`` bucket. ``init_only`` keys rotate only
    when named in ``include`` (with a loud in-DB-reset warning). ``encrypt_at_rest``
    keys rotate only with ``force_destructive`` (data becomes undecryptable).
    """
    include_set = set(include)
    rotate: list[str] = []
    skipped_init: list[str] = []
    refused: list[str] = []
    warnings: list[str] = []

    for key in ALL_GENERATED_SECRET_KEYS:
        if key not in env:
            continue
        bucket = classify(key)
        if bucket == "rotatable":
            rotate.append(key)
        elif bucket == "init_only":
            if key in include_set:
                rotate.append(key)
                warnings.append(
                    f"{key}: INIT-ONLY — the container keeps the stored password; "
                    "also run the in-DB reset (ALTER USER / admin password reset)"
                )
            else:
                skipped_init.append(key)
        elif bucket == "encrypt_at_rest":
            if force_destructive:
                rotate.append(key)
                warnings.append(
                    f"{key}: ENCRYPT-AT-REST — rotating makes existing data permanently undecryptable"
                )
            else:
                refused.append(key)

    return RotationPlan(tuple(rotate), tuple(skipped_init), tuple(refused), tuple(warnings))


def apply_rotation(env: Mapping[str, str], plan: RotationPlan) -> dict[str, str]:
    """Return a copy of ``env`` with the planned keys regenerated."""
    new = dict(env)
    for key in plan.rotate:
        new[key] = generate_for(key)
    return new


def rewrite_env_text(text: str, new_values: Mapping[str, str]) -> str:
    """Rewrite a .env, replacing only ``new_values`` keys; everything else verbatim."""
    out: list[str] = []
    for line in text.split("\n"):
        stripped = line.lstrip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in new_values:
                value = new_values[key]
                out.append(f"{key}={compose_env_quote(value) if value else ''}")
                continue
        out.append(line)
    return "\n".join(out)


def secret_consumers(descriptors: Mapping[str, Any]) -> dict[str, list[str]]:
    """Map each generated secret key → the services that reference it (env/command/health)."""
    keyset = set(ALL_GENERATED_SECRET_KEYS)
    consumers: dict[str, set[str]] = {}
    for name in sorted(descriptors):
        desc = descriptors[name]
        blobs: list[str] = list(desc.env.values())
        if getattr(desc, "command", None):
            blobs.extend(str(x) for x in desc.command)
        health = getattr(desc, "health", None)
        if health is not None and getattr(health, "test", None):
            blobs.extend(str(x) for x in health.test)
        for blob in blobs:
            for match in _VAR_REF_RE.finditer(str(blob)):
                key = match.group(1)
                if key in keyset:
                    consumers.setdefault(key, set()).add(name)
    return {k: sorted(v) for k, v in consumers.items()}


def holders_for(rotated: Sequence[str], consumers: Mapping[str, Sequence[str]]) -> list[str]:
    """The union of services consuming any rotated secret (to force-recreate together)."""
    holders: set[str] = set()
    for key in rotated:
        holders.update(consumers.get(key, ()))
    return sorted(holders)
