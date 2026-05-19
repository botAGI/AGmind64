"""Pydantic v2 schemas для service descriptors (Phase H').

См. ADR-0005 (`docs/adr/0005-service-descriptor-schema.md`).

ServiceDescriptor — single source of truth для одного сервиса в стеке.
Парсится из `templates/services/<name>.yaml`, валидируется Pydantic,
конвертируется в legacy `agmind.services.registry.Service` через
`to_legacy_service()` пока Phase H'.C не переписан рендерер.

JSON Schema export для VSCode/IDE autocomplete:
    python -m scripts.export_schemas
    # → templates/schemas/service.json
"""

from agmind.schemas.service import (
    HealthCheck,
    ObservabilityConfig,
    ResourceLimits,
    RoutingConfig,
    ServiceDescriptor,
    ServiceTier,
)

__all__ = [
    "HealthCheck",
    "ObservabilityConfig",
    "ResourceLimits",
    "RoutingConfig",
    "ServiceDescriptor",
    "ServiceTier",
]
