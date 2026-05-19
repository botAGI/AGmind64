"""Service registry — declarative каталог компонентов стека.

См. `templates/services.yaml` (single source of truth) и `agmind/services/registry.py`
для runtime API.
"""

from __future__ import annotations

from agmind.services.registry import (
    Service,
    ServiceProfile,
    list_services,
    load_registry,
    services_for_profile,
)

__all__ = [
    "Service",
    "ServiceProfile",
    "list_services",
    "load_registry",
    "services_for_profile",
]
