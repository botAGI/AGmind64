"""Compute backend abstraction.

См. ADR-0002 (`docs/adr/0002-compute-backend-abstraction.md`).

Публичный API:
    get_backend(profile=None) -> Backend
    Backend (ABC)
    DeviceInfo (dataclass)

Backends регистрируются в `agmind.compute._registry`. Auto-select
работает через `agmind.compute.detect` + env vars из `agmind.compute.config`.
"""

from __future__ import annotations

from agmind.compute.base import Backend, DeviceInfo
from agmind.compute.config import Profile, read_config
from agmind.compute._registry import get_backend, list_available_backends

__all__ = [
    "Backend",
    "DeviceInfo",
    "Profile",
    "get_backend",
    "list_available_backends",
    "read_config",
]
