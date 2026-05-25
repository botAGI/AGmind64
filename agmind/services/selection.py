"""Service selection expansion for setup-time component stacks."""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from agmind.components import ComponentContract
from agmind.schemas import ServiceDescriptor

_PREFERRED_CAPABILITY_PROVIDERS = (
    "llama-llm",
    "llama-embed",
    "qdrant",
    "postgres",
    "redis",
    "mysql",
    "elasticsearch",
    "minio",
    "traefik",
)


def resolve_service_selection(
    descriptors: Mapping[str, ServiceDescriptor],
    *,
    services: Iterable[str],
    component_contracts: Mapping[str, ComponentContract] | None = None,
) -> dict[str, ServiceDescriptor]:
    """Expand explicit setup service choices into a deployable service closure.

    Low-level renderer filtering intentionally stays exact. This resolver is for
    setup flows where picking one service from a tightly-coupled stack should
    carry its siblings and mandatory runtime requirements.
    """

    selected = {service for service in services if service in descriptors}
    contracts = component_contracts or {}
    owners = _service_component_owners(contracts)
    stack_components = _stack_component_ids(contracts)

    changed = True
    while changed:
        changed = False

        active_stack_components = {
            component_id
            for service in selected
            for component_id in owners.get(service, ())
            if component_id in stack_components
        }

        for component_id in sorted(active_stack_components):
            contract = contracts[component_id]
            for service in contract.runtime.service_descriptors:
                if service in descriptors and service not in selected:
                    selected.add(service)
                    changed = True

        for service in sorted(tuple(selected)):
            descriptor = descriptors[service]
            for dependency in descriptor.depends_on:
                if dependency in descriptors and dependency not in selected:
                    selected.add(dependency)
                    changed = True

        for component_id in sorted(active_stack_components):
            contract = contracts[component_id]
            for capability in contract.requires.capabilities:
                provider = _choose_capability_provider(
                    descriptors,
                    selected=selected,
                    capability=capability,
                )
                if provider is not None and provider not in selected:
                    selected.add(provider)
                    changed = True

    return {name: descriptors[name] for name in sorted(selected)}


def _service_component_owners(
    contracts: Mapping[str, ComponentContract],
) -> dict[str, tuple[str, ...]]:
    owners: dict[str, list[str]] = {}
    for component_id, contract in contracts.items():
        for service in contract.runtime.service_descriptors:
            owners.setdefault(service, []).append(component_id)
    return {service: tuple(sorted(component_ids)) for service, component_ids in owners.items()}


def _stack_component_ids(contracts: Mapping[str, ComponentContract]) -> set[str]:
    return {
        component_id
        for component_id, contract in contracts.items()
        if any(capability.endswith("_stack") for capability in contract.provides)
    }


def _choose_capability_provider(
    descriptors: Mapping[str, ServiceDescriptor],
    *,
    selected: set[str],
    capability: str,
) -> str | None:
    candidates = [
        name for name, descriptor in descriptors.items() if capability in descriptor.provides
    ]
    if not candidates:
        return None

    selected_providers = sorted(set(candidates) & selected)
    if selected_providers:
        return selected_providers[0]

    preferred_rank = {name: index for index, name in enumerate(_PREFERRED_CAPABILITY_PROVIDERS)}

    def sort_key(name: str) -> tuple[int, int, int, str]:
        descriptor = descriptors[name]
        return (
            preferred_rank.get(name, len(preferred_rank)),
            0 if "core" in descriptor.profiles else 1,
            0 if "rag" in descriptor.profiles else 1,
            name,
        )

    return sorted(candidates, key=sort_key)[0]


__all__ = ["resolve_service_selection"]
