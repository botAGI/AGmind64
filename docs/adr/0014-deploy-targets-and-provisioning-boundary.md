# ADR-0014: Deploy Targets and Provisioning Boundary

- **Status:** accepted
- **Date:** 2026-05-23
- **Related:** ADR-0013, M7.B, R19
- **Driver:** AGmind needs a universal homelab/enterprise deploy path without
  hiding Proxmox, Kubernetes, Nomad, storage, or secrets decisions inside
  Docker Compose service descriptors.

## Context

The current production path is coherent and should remain the v1.0 baseline:

```text
clean Ubuntu host -> Ansible -> Docker Compose -> AGmind services
```

This path does not describe how the host exists. That is acceptable for a
single manually prepared Ubuntu box, but it becomes ambiguous for Proxmox,
k3s, RKE2/Talos, Nomad, or enterprise environments where provisioning,
configuration, runtime rendering, storage, secrets, and verification differ.

`ServiceDescriptor` is intentionally runtime-specific today. It describes one
container in the Compose graph: image, ports, volumes, devices, profiles,
health, routing, and capability wiring. Extending service descriptors with
Proxmox VM fields, Kubernetes storage classes, OpenTofu state, or Vault
configuration would mix several layers in one catalog and make conflicts hard
to reason about.

## Decision

AGmind adds a first-class `DeploymentTarget` contract.

`templates/deploy-targets/*.yaml` defines supported or planned deployment
lanes. A target owns:

- runtime kind: `compose`, `kubernetes`, or `nomad`;
- provisioner: `none`, `opentofu-proxmox`, or `external`;
- configurator: `ansible`, `helm`, `kustomize`, `talosctl`, `kubectl`, or
  `none`;
- storage profile: `local-paths`, `proxmox-zfs`, `nfs`, `longhorn`, `ceph`, or
  `external`;
- secrets profile: `env-files`, `sops-age`, `external-secrets`, `vault`, or
  `infisical`;
- verification commands for that lane.

The initial target ladder is:

- `ubuntu-compose` — supported current path: operator provides an Ubuntu host,
  Ansible configures it, AGmind renders Docker Compose.
- `proxmox-vm-compose` — experimental homelab path: OpenTofu provisions Ubuntu
  VM nodes on Proxmox, then Ansible and the existing Compose renderer run.
- `k3s` — research path: Kubernetes runtime with Helm/Kustomize style
  rendering, Longhorn storage, and External Secrets.

OpenTofu is the infrastructure provisioning boundary. It may create VMs,
cloud-init config, disks, networks, and inventory outputs. It must not become
the service renderer.

Ansible remains the host configuration boundary for Ubuntu/Compose targets.
It installs packages, configures Docker and host settings, and runs the same
privileged bootstrap role whether the host was manually prepared or provisioned
by OpenTofu.

Kubernetes and Nomad support require separate renderers. They are target
implementations, not hidden branches inside `templates/services/*.yaml`.

## Implementation

- `agmind.deploy.targets` defines the Pydantic `DeploymentTarget` model and
  default loader for `templates/deploy-targets/`.
- `templates/deploy-targets/ubuntu-compose.yaml`,
  `templates/deploy-targets/proxmox-vm-compose.yaml`, and
  `templates/deploy-targets/k3s.yaml` define the first target ladder.
- `scripts/export_schemas.py` exports
  `templates/schemas/deploy-target.json` for editor and CI validation.
- `tests/test_deploy_targets.py` verifies target validation, duplicate-id
  rejection, repository baseline targets, and schema export.
- `infra/proxmox/vm-compose` provides the first OpenTofu root module skeleton
  for `proxmox-vm-compose`.
- `scripts/proxmox_inventory.py` converts `tofu output -json` into a local
  Ansible inventory for the existing install playbook.

## Consequences

Positive:

- Proxmox/OpenTofu work can proceed without changing service descriptors.
- The current Compose path remains stable while homelab and enterprise lanes
  become explicit.
- Storage and secrets choices are visible at the target layer before a renderer
  is selected.
- Future k3s/RKE2/Talos/Nomad work has a contract to attach to.

Trade-offs:

- A target catalog is another artifact that must be kept current.
- The Proxmox lane now has a module skeleton and inventory bridge, but still
  needs real `tofu init/validate/plan` evidence on a Proxmox host.
- k3s is recorded as research, so tests should validate the contract but not
  imply Kubernetes runtime support is complete.

## Rollback

The target layer is additive. To roll it back, remove the deploy target loader,
schema export, target YAML files, and target tests. Existing install, deploy,
Compose rendering, and Ansible behavior remain unchanged because this ADR does
not alter the current runtime path.
