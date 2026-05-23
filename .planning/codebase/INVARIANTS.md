# AGmind Invariants

Last updated: 2026-05-23.

These rules are the guardrails for future changes.

## Hardware And Compute

### I.1 RADV is the Vulkan path

Use Mesa RADV for Vulkan. AMDVLK is forbidden for AGmind Vulkan runtime.

Enforced by:

- Vulkan backend checks
- `agmind doctor`
- audit/review

### I.2 Backends are selected through `agmind.compute`

Application code calls `get_backend()` or backend handles. Direct runtime
engine imports belong in `agmind/compute/backends/_engines/`.

### I.3 Heavy engine imports stay lazy

`llama_cpp`, Torch, ROCm/HIP, and Vulkan-specific imports must not be required
for `import agmind`.

### I.4 CPU fallback must remain available

If entry-point discovery fails, CPU backend fallback remains the safety net.

## Service Catalog

### I.5 `templates/services/*.yaml` is the service source of truth

Do not recreate monolithic `templates/services.yaml`.

### I.6 Service descriptors validate before merge

Every descriptor must pass `templates/schemas/service.json`.

### I.7 No `:latest`

Runtime images use pinned versions and, where practical, digests.

### I.8 Capability graph is explicit

Use `provides`, `consumes`, and `conflicts_with` rather than hidden renderer
special cases.

## Models

### I.9 `templates/models.yaml` is the model source of truth

LLM/embed/rerank/VLM selections belong in the model catalog.

### I.10 Model files are artifacts, not source

`/models/`, `*.gguf`, `*.safetensors`, and similar payloads stay ignored.

## CLI And TUI

### I.11 CLI is a leaf layer

Domain modules must not import CLI/TUI modules.

### I.12 CLI handlers stay thin

Handlers parse input and delegate. Business logic belongs in domain modules.

### I.13 TUI state must be testable

Wizard state, install state, and status/deploy views should have focused tests
for transitions and rendering assumptions.

## Install And Host Mutation

### I.14 Ansible owns privileged bootstrap

Python can orchestrate; Ansible mutates apt, groups, sysctl, Docker daemon,
firewall, and host service bootstrap.

### I.15 Secrets are files with restrictive modes

Production credentials are written with mode `0600` and masked in logs.

## CI And Tooling

### I.16 Self-hosted CI uses system Python and `uv`

Do not reintroduce `actions/setup-python` into the normal self-hosted CI path
unless the runner/toolcache issue is intentionally solved.

### I.17 Host pytest lane installs `.[dev]`, not backend extras

Native `llama-cpp-python` backend builds belong in Docker/backend lanes.
`test-cpu` covers core Python behavior and lazy fallback.

### I.18 Runtime image smoke is not pytest

Backend runtime images are production images. Strix smoke checks
`get_backend().device_info()` inside the image. The smoke job must wait for
the Docker backend matrix, otherwise it can test stale `agmind-*:ci` tags left
on the self-hosted runner.

### I.19 Audit must stay green

`scripts/audit_forbidden.py --fail` is a merge gate.

### I.20 Executable scripts must be executable in Git

If tests require a script to be executable, track mode `100755`, not just local
filesystem chmod.

## Planning Discipline

### I.21 `.planning/` is the durable GSD memory

Update `STATE`, `ROADMAP`, `BACKLOG`, session notes, and codebase maps when
work changes the project state.

### I.22 Claude artifacts are not project memory

`.claude/` and `CLAUDE.md` are ignored and removed from source control.

### I.23 ADR for durable architecture shifts

New extension surfaces, backend strategy changes, or deploy architecture
changes need an ADR.

### I.24 Recon before new external systems

Before adding a new backend/engine/vendor service, add a research note under
`.planning/research/` with dated evidence.
