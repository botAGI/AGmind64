# ADR-0013: Component Contracts and Safe Updates

- **Status:** accepted
- **Date:** 2026-05-23
- **Related:** ADR-0005, ADR-0011, ADR-0012, M7.A
- **Driver:** AGmind needs explicit version ownership before adding homelab and
  enterprise tools such as ComfyUI, Proxmox targets, Kubernetes renderers, and
  larger service packs.

## Context

`templates/services/*.yaml` is good at describing how one container runs:
image pin, ports, volumes, profiles, dependencies, capabilities, and resource
shape. It is not enough to describe a tool boundary.

Several AGmind tools are stacks rather than single services:

- Dify owns API, worker, web, sandbox, and plugin daemon descriptors.
- RAGFlow owns its app service plus stateful dependencies.
- Observability owns multiple exporters and dashboards.
- Backend builds span Dockerfiles, Python packages, hardware lanes, and model
  compatibility.

Encoding stack membership inside service capabilities created ambiguity:
`provides` should mean "another service can consume this behavior", not "this
service belongs to that product." Version updates also need grouped rollback:
bumping only one Dify container can create an incompatible partial stack.

## Decision

AGmind separates runtime descriptors from component contracts.

`templates/services/*.yaml` continues to describe one service runtime.
`templates/components/*.yaml` describes one upstream tool, app stack, backend,
model family, deploy target, or ops stack.

A component contract owns:

- upstream identity and recommended/current/minimum versions;
- update policy: `strict-pin`, `compatible-patch`, `compatible-minor`,
  `upstream-compatible`, or `manual-hold`;
- version source: registry, GitHub releases, PyPI, manual, or local;
- runtime artifacts such as service descriptors, Dockerfiles, Python packages,
  Ansible collections, model catalogs, ports, and files;
- required capabilities/components and known conflicts;
- verification commands and schema references.

Every first-class service descriptor must have exactly one component owner.
Stack membership belongs in `runtime.service_descriptors`, not in `provides`.
Deploy-level singleton conflicts such as host ports `80/443` are checked
outside the service capability graph.

## Implementation

- `agmind.components.contracts` defines the Pydantic contract model.
- `agmind.components.registry` loads `templates/components/*.yaml`.
- `agmind.components.checks` validates deploy-level conflicts.
- `scripts/checks/component_check.py` checks component loading, service ownership,
  component-to-service references, and selected profile port conflicts.
- `.pre-commit-config.yaml` runs the component check when component, service,
  or service/component code changes.
- `.github/workflows/ci.yml` runs component validation on the self-hosted
  runner before CPU tests and compose validation.
- `scripts/checks/version_check.py` includes component policies and non-container
  dependency pins in the weekly update report.
- `agmind upgrade --component <id>` can plan grouped updates, apply descriptor
  bumps, and rollback grouped state.

## Consequences

Positive:

- Adding ComfyUI, Proxmox, k3s, Nomad, Harbor, or other future tools now has a
  clear contract boundary before runtime integration.
- Optional service-profile tools now pass through a candidate/admission catalog
  before service descriptors are added.
- Weekly update reports are actionable by component rather than by isolated
  image tag.
- Grouped app stacks can be updated and rolled back as a unit.
- Service compatibility warnings remain soft, while deploy-level collisions
  can fail CI.

Trade-offs:

- New first-class tools require both service descriptors and component
  contracts.
- Update policy is conservative by default; some upstreams will need manual
  review until adapters become richer.
- Component contracts introduce another catalog that must stay in sync with
  service descriptors, so the CI/pre-commit gate is mandatory.

## Rollback

The component layer is additive. To disable it, remove the component validation
job and pre-commit hook, then stop loading component contracts in
`version_check.py` and `upgrade_cmd.py`. Existing service descriptors and
compose rendering remain usable because runtime service definitions are still
independent.
