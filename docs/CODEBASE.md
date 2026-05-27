# AGmind Codebase Map

This map is the responsibility contract for the repository layout. New code
should land in the narrowest matching domain instead of growing root-level
modules.

## Python Package

- `agmind/core/` - shared low-level utilities: logging, `.env` parsing, secret
  file helpers. Root `agmind/log.py`, `agmind/_env.py`, and `agmind/secrets.py`
  are compatibility shims only.
- `agmind/cli/` - Typer CLI commands and Textual TUI entrypoints. Command
  modules should orchestrate domain APIs, not contain domain logic.
- `agmind/install/` - fresh install planning, bootstrap steps, Ansible command
  resolution, and install verification.
- `agmind/deploy/` - day-2 deploy, diff, snapshots, rollback, garbage
  collection, deploy targets, and Proxmox helpers.
- `agmind/ops/` - operator actions after deployment: backup, restore, service
  logs/shell helpers, root-owned backup smoke checks.
- `agmind/diagnostics/` - host preflight and doctor reports.
- `agmind/cluster/` - peer discovery, inventory, cluster inspection, and routing.
- `agmind/compute/` - CPU/Vulkan/ROCm/NPU backend contracts, detection,
  registry, and inference clients.
- `agmind/models/` - curated model catalog and model selection helpers.
- `agmind/services/` - service descriptor loading, selection, compose render,
  Kubernetes render/dry-run support, topology, retrieval policy, and service
  compatibility.
- `agmind/components/` - component contracts and deploy conflict checks.
- `agmind/addons/` - optional tool candidate contracts and validation.
- `agmind/governance/` - aggregate governance gate that composes docs,
  component, deploy-target, tool-candidate, dependency, topology, Kubernetes
  render, and proof workflow checks.
- `agmind/schemas/` - Pydantic schemas exported for template validation.
- `agmind/config/` - runtime config rendering/writing helpers.
- `agmind/migrations/` - user state schema migrations and migration CLI support.
- `agmind/ci/` - CI monitor data models and GitHub Actions status collection.
- `agmind/i18n/` - translation loading and message catalogs.

## Scripts

- `scripts/checks/` - CI/pre-commit/governance checks. These are executable
  wrappers or validators and should not own install/deploy business logic.
- `scripts/proof/` - proof/smoke commands that exercise rendered output or
  privileged flows.
- `scripts/ops/` - operator helper scripts used outside the Python package.
- `scripts/dev/` - local developer maintenance scripts such as schema export.

## Templates And Infra

- `templates/services/` - service descriptor source of truth.
- `templates/components/` - component-level contracts and update policy.
- `templates/deploy-targets/` - deployment target contracts.
- `templates/tool-candidates/` - optional operator tool catalog.
- `templates/schemas/` - generated JSON Schema for descriptors/contracts.
- `templates/observability/` - Prometheus/Grafana/Loki/Alloy runtime config.
- `infra/` - OpenTofu and deployment infrastructure roots.
- `ansible/` - host provisioning boundary for Ubuntu/Compose targets.
- `docker/` - build images and runtime containers.
- `constraints/` - dependency constraint planes used by install and CI.

## Tests

Tests mirror the runtime domains:

- `tests/core/`, `tests/cli/`, `tests/tui/`, `tests/install/`, `tests/deploy/`
- `tests/ops/`, `tests/services/`, `tests/components/`, `tests/addons/`
- `tests/governance/`, `tests/cluster/`, `tests/compute/`, `tests/models/`
- `tests/infra/`, `tests/observability/`, `tests/migrations/`, `tests/i18n/`
- `tests/diagnostics/`, `tests/ci/`

Path-sensitive tests should derive the repository root with
`Path(__file__).resolve().parents[2]` from one-level-deep test domains.

## Boundary Rules

- Install code prepares a fresh node; deploy code applies, snapshots, rolls
  back, and garbage-collects an existing rendered deployment.
- Descriptor parsing and rendering live under `agmind/services/`; CLI commands
  only call those APIs.
- Governance checks call domain validators and renderers; they should not
  duplicate the validation logic they aggregate.
- Root-level package files should stay limited to public entrypoints and
  compatibility shims.
- New tests go next to the domain they verify, not in the top-level `tests/`
  directory.
