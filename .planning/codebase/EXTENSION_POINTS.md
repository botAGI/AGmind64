# AGmind Extension Points

Last updated: 2026-05-23.

This file describes supported extension seams. It also marks what is not an
extension point, so new work does not revive cloud/Claude artifacts.

## Supported Extension Points

### 1. Compute Backends

Mechanism: Python package entry points.

```toml
[project.entry-points."agmind.backends"]
my_backend = "agmind_my_backend:MyBackend"
```

Contract:

- subclass or implement `agmind.compute.base.Backend`
- expose `available()`, `make()`, `device_info()`
- lazy-import heavy native libraries
- participate in auto-select only if core explicitly adds it to
  `_BACKEND_PRIORITY`; otherwise use explicit `AGMIND_BACKEND=my_backend`

Built-ins:

- `cpu`
- `vulkan`
- `rocm`
- `npu`

### 2. Service Descriptors

Mechanism: one YAML file under `templates/services/`.

Contract:

- validate against `templates/schemas/service.json`
- include profile membership and runtime image
- declare `provides`, `consumes`, and `conflicts_with` where relevant
- use pinned tags; no `:latest`
- keep secrets out of descriptors

Developer UX:

```bash
agmind service scaffold <name> --tier <tier>
agmind service validate
agmind render compose --profile core
```

### 3. Capability Bindings

Mechanism: `agmind/services/capability_bindings.py`.

Use this when a provider service should automatically populate env vars for a
consumer, for example vector DB or inference endpoints. Keep it table-driven.

### 4. Compose Renderer Profiles

Mechanism:

- descriptor `profiles`
- renderer profile selection
- compatibility checks

Use this for new bundles such as an alternative vector DB or UI. Do not fork a
second compose renderer.

### 5. Model Catalog

Mechanism: `templates/models.yaml` and `agmind.models`.

Use this for new LLM/embed/rerank/VLM options. The install wizard and model
commands should read from the catalog, not hardcode model lists.

### 6. CLI Commands

Mechanism: Typer subcommands under `agmind/cli/*_cmd.py` and registration in
`agmind/cli/__init__.py`.

Rules:

- CLI is a leaf layer.
- Import domain modules lazily inside handlers.
- Keep business logic out of CLI functions.

### 7. TUI Screens

Mechanism: Textual screens under `agmind/cli/tui/`.

Rules:

- keep state dataclasses explicit
- keep render/layout code separate from install/deploy logic
- cover interactions with Textual Pilot tests when possible

### 8. Install Steps

Mechanism:

- `agmind.install.orchestrator.InstallOrchestrator`
- step helpers in `agmind/install/steps.py`

Use this for first-run workflows. Privileged host mutation still belongs to
Ansible.

### 9. Observability

Mechanism:

- `templates/observability/*`
- service descriptors for Prometheus/Grafana/Loki/Alloy/exporters
- scripts such as `scripts/amdgpu_textfile.sh`

Dashboard JSON provision is still backlog work. Add dashboards as templates,
not generated one-off files.

### 10. Cluster

Mechanism:

- `agmind.cluster.detect`
- `agmind.cluster.inventory`
- `agmind.cluster.peer`
- `agmind.cluster.router`

Peers are workers for inference; local node remains master/full stack.

## Not Extension Points

- `.claude/` and `CLAUDE.md` are removed and ignored. Do not use them as live
  project state.
- `legacy/` migration context should not become an active integration surface.
- CI should not depend on a cloud toolchain or mutable local artifacts.
- Runtime Docker images should not be treated as dev images with pytest.

## Future Project Plugins

These are product-level plugin ideas, not required for the current CI/gate
work:

- `agmind backend` packaging helper for third-party `agmind.backends`
  packages.
- `agmind plugin list/install` marketplace command for service bundles.
- Thin Dify tool plugin for RAGFlow/Docling sidecars, following the
  sidecar-over-heavy-plugin pattern documented in research.
- Observability plugin bundle for custom exporters and Grafana dashboards.

Before implementing any of these, write or update an ADR and add a focused
research note under `.planning/research/`.
