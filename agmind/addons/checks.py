"""Admission checks for optional tool candidates."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def _owner_ids_for_service(
    component_contracts: Mapping[str, Any],
    service_name: str,
) -> list[str]:
    owners: list[str] = []
    for component_id, contract in component_contracts.items():
        runtime = getattr(contract, "runtime", None)
        service_descriptors = getattr(runtime, "service_descriptors", ())
        if service_name in service_descriptors:
            owners.append(str(component_id))
    return sorted(owners)


def _descriptor_ports(descriptor: Any) -> set[str]:
    ports: set[str] = set()
    for port_spec in getattr(descriptor, "ports", ()) or ():
        parts = str(port_spec).split(":")
        if len(parts) == 2:
            ports.update(parts)
        elif len(parts) == 3:
            ports.update(parts[1:])
    return ports


def validate_tool_candidates(
    candidates: Mapping[str, Any],
    deploy_targets: Mapping[str, Any],
    descriptors: Mapping[str, Any],
    component_contracts: Mapping[str, Any],
) -> list[str]:
    """Validate candidate catalog references and accepted runtime admission."""
    deploy_target_ids = set(deploy_targets)
    errors: list[str] = []

    for candidate in candidates.values():
        missing_targets = sorted(set(candidate.dependencies.deploy_targets) - deploy_target_ids)
        for target in missing_targets:
            errors.append(f"{candidate.id}: unknown deployment target {target}")

        if candidate.status == "accepted" and not candidate.verification.commands:
            errors.append(f"{candidate.id}: accepted candidates must define verification commands")

        if candidate.admission.service_descriptor_required and not candidate.dependencies.profiles:
            errors.append(
                f"{candidate.id}: service-profile candidates must define at least one profile"
            )

        if candidate.status != "accepted":
            continue

        descriptor = descriptors.get(candidate.id)
        if candidate.admission.service_descriptor_required:
            if descriptor is None:
                errors.append(
                    f"{candidate.id}: accepted candidate requires service descriptor {candidate.id}"
                )
                continue

            missing_profiles = sorted(
                set(candidate.dependencies.profiles) - set(getattr(descriptor, "profiles", ()))
            )
            if missing_profiles:
                errors.append(
                    f"{candidate.id}: candidate profiles not present in descriptor: "
                    f"{', '.join(missing_profiles)}"
                )

            missing_ports = sorted(
                set(candidate.dependencies.ports) - _descriptor_ports(descriptor)
            )
            if missing_ports:
                errors.append(
                    f"{candidate.id}: candidate ports not present in descriptor: "
                    f"{', '.join(missing_ports)}"
                )

            if candidate.admission.image_pin_required and not getattr(descriptor, "digest", None):
                errors.append(
                    f"{candidate.id}: accepted candidate requires digest-pinned descriptor"
                )

        if candidate.admission.component_contract_required:
            owners = _owner_ids_for_service(component_contracts, candidate.id)
            if len(owners) != 1:
                errors.append(
                    f"{candidate.id}: accepted candidate requires exactly one component owner"
                )

    return errors
