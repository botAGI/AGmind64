# AGmind Architecture

Last updated: 2026-05-23.

AGmind is a private LLM/RAG platform for AMD Strix Halo and generic x86_64.
The runtime target is a local, self-hosted stack with one-command install,
capability-aware service composition, and day-2 operations.

## Layer Model

```text
Layer 5  CI / hardware evidence
         .github/workflows/ci.yml
         self-hosted runner, Docker backend matrix, Strix Halo smoke

Layer 4  Host orchestration
         ansible/install.yml + 11 roles
         privileged bootstrap, Docker, models, services, observability, security

Layer 3  Declarative catalogs
         templates/services/*.yaml
         templates/models.yaml
         templates/schemas/service.json
         templates/observability/*

Layer 2  Python domain runtime
         agmind/compute, services, schemas, install, deploy, ops, cluster,
         diagnostics, models, migrations, secrets

Layer 1  User surface
         agmind CLI, Textual TUI, rendered compose, Docker runtime images
```

The design rule is simple: catalogs describe, Python decides, Ansible mutates
the host, Docker runs long-lived services, CI proves the current contract.

## Main Runtime Flows

### Install

```text
agmind install / TUI
  -> agmind.install.orchestrator
  -> doctor + bootstrap + model selection + env write + deploy
  -> Ansible for privileged host work
  -> agmind render compose
  -> docker compose up
```

The installer owns workflow state and progress events. Ansible owns apt,
groups, sysctl, GRUB-related host tasks, Docker setup, firewall, and service
bootstrap. Python should not silently take over privileged host mutation.

### Compose Rendering

```text
templates/services/*.yaml
  -> agmind.schemas.service.ServiceDescriptor
  -> agmind.services.registry.load_descriptors()
  -> agmind.services.compatibility checks
  -> agmind.services.renderer.render_compose()
  -> docker compose config / up
```

Descriptors expose `provides`, `consumes`, and `conflicts_with`. Capability
bindings inject consumer environment variables such as vector DB and inference
URLs.

### Compute Backend Selection

```text
AGMIND_BACKEND / AGMIND_ENGINE / workload profile
  -> agmind.compute.config.read_config()
  -> agmind.compute._registry entry-point discovery
  -> Backend.available()
  -> Backend.make()
  -> llama-server HTTP or llama-cpp in-process engine
```

Built-in backends are `cpu`, `vulkan`, `rocm`, and `npu`. Third-party backends
register through the `agmind.backends` Python entry-point group.

### Inference Services

```text
client / Dify / Open WebUI / app
  -> reverse proxy
  -> llama-llm / llama-embed / llama-rerank
  -> llama.cpp server image
  -> RADV Vulkan or ROCm/HIP on gfx1151
```

The default Strix Halo preference is Vulkan/RADV for token generation and
mixed workloads, ROCm for workloads where HIP is a better fit, CPU as a
portable fallback.

### CI

```text
push / PR
  -> pre-commit
  -> audit
  -> schema-validate
  -> test-cpu
  -> compose-validate
  -> docker-build cpu/vulkan/rocm
  -> test-strix-halo vulkan/rocm runtime smoke
```

The CI workflow is intentionally self-hosted and uses system `python3` plus the
preinstalled `uv`/`uvx`. `actions/setup-python` was removed because toolcache
downloads repeatedly stalled the runner.

## Key Modules

| Module | Responsibility |
|--------|----------------|
| `agmind/cli/__init__.py` | Typer app and command registration |
| `agmind/cli/tui/` | Textual setup/install/status/deploy UI |
| `agmind/compute/base.py` | Backend and handle contracts |
| `agmind/compute/_registry.py` | Entry-point discovery and auto-select |
| `agmind/compute/backends/` | CPU, Vulkan, ROCm, NPU backend implementations |
| `agmind/compute/clients/llama_server.py` | OpenAI-compatible llama-server client |
| `agmind/services/registry.py` | Descriptor loading and legacy bridge |
| `agmind/services/renderer.py` | Docker Compose rendering |
| `agmind/services/compatibility.py` | Capability/conflict checks |
| `agmind/install/orchestrator.py` | Install workflow coordinator |
| `agmind/install/steps.py` | Install step implementations |
| `agmind/deploy/runner.py` | Render/apply/rollback deployment runner |
| `agmind/ops/backup.py` | Backup and restore tarballs |
| `agmind/cluster/detect.py` | LAN peer discovery |
| `agmind/diagnostics/doctor.py` | Host readiness checks |
| `agmind/schemas/service.py` | Pydantic service descriptor contract |

## Deployment Stack

Profiles are composed from the descriptor catalog:

- `core`: inference servers, vector DB, reverse proxy
- `rag`: Dify, Docling, Postgres, Redis, plugin daemon/sandbox
- `ragflow`: RAGFlow with MySQL, Elasticsearch, MinIO
- `ui`: Open WebUI
- `observability`: Prometheus, Grafana, Loki, Alloy, Alertmanager, exporters, cAdvisor, Portainer
- `security`: Authelia and host security support
- `full`: everything compatible together

The renderer handles profile selection, Traefik/nginx/caddy alternatives,
capability environment injection, resources, volumes, and health checks.

## Boundaries

- `agmind/` may render files and call subprocesses, but host bootstrap goes
  through Ansible.
- `templates/services/*.yaml` is the service source of truth. Do not recreate
  monolithic `templates/services.yaml`.
- Runtime Docker images are not dev test images. Hardware smoke uses a small
  `python3 -c` backend check, not `pytest` inside runtime images.
- `.planning/` is project memory. `.claude/` is not project memory and is
  ignored.
