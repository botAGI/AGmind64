"""Shared DNS domain validation."""

from __future__ import annotations

import re

_DOMAIN_RE = re.compile(
    r"^(?=.{1,253}\Z)"
    r"(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+"
    r"[a-z]{2,63}\Z"
)


def validate_domain(domain: str) -> str:
    """Return normalized DNS domain or raise ValueError."""
    value = domain.strip().rstrip(".").lower()
    if not value:
        raise ValueError("domain is required")
    if "." not in value:
        raise ValueError("domain must contain '.'")
    if not _DOMAIN_RE.fullmatch(value):
        raise ValueError("invalid DNS domain")
    return value
