# R20 — Component boundaries and version contracts

- **Date:** 2026-05-23
- **Status:** audit + design direction, not implementation
- **Driver:** every tool has its own core, adapter/runtime dependencies, and
  recommended versions; AGmind needs clearer component separation before adding
  more homelab/enterprise tools.

## Scope

This pass audited four local dependency planes:

1. Python package dependencies in `pyproject.toml` and the current `.venv`;
2. container/service pins in `templates/services/*.yaml`;
3. backend build dependencies in `docker/Dockerfile.{base,cpu,vulkan,rocm}`;
4. operational tooling in Ansible, GitHub Actions, pre-commit, and planning.

It also ran the current upstream scanner:

```bash
$HOME/.local/bin/uv pip check --python .venv/bin/python
.venv/bin/python scripts/version_check.py --json /tmp/agmind-version-online.json
```

Result: Python environment is internally compatible, but version governance is
not yet strict enough for reproducible homelab/enterprise deploys.

## Current component inventory

AGmind currently has **33 service descriptors**:

| Tier | Count | Services |
|---|---:|---|
| inference | 3 | `llama-llm`, `llama-embed`, `llama-rerank` |
| app | 8 | `dify-*`, `ragflow`, `openwebui`, `docling` |
| storage | 8 | `postgres`, `redis`, `qdrant`, `weaviate`, `milvus`, `mysql`, `elasticsearch`, `minio` |
| edge | 4 | `traefik`, `caddy`, `nginx`, `authelia` |
| ops | 10 | Prometheus/Grafana/Loki/Alloy/exporters/Portainer/Alertmanager |

Capabilities currently found:

| Capability | Providers | Consumers |
|---|---|---|
| `llm_inference` | `llama-llm` | Dify services, `ragflow`, `openwebui` |
| `embedding_inference` | `llama-embed` | Dify services, `ragflow` |
| `reranker` | `llama-rerank` | none yet |
| `vector_db` | `qdrant`, `weaviate`, `milvus` | Dify services |
| `search_index` | `elasticsearch` | `ragflow` |
| `dify_external_kb` | `ragflow` | `dify-api` |
| `reverse_proxy` | `traefik`, `caddy`, `nginx` | none |
| `tls_termination` | `traefik`, `caddy` | none |
| state capabilities | `postgres_db`, `redis_cache`, `mysql_db`, `object_storage` | not modeled as consumers |

The capability graph is useful, but it is mixing at least three concepts:

- a **single provider** capability, e.g. `llm_inference`;
- a **mutually substitutable provider class**, e.g. `vector_db`;
- a **stack membership marker**, e.g. every Dify container `provides:
  dify_stack`.

Those need to become separate fields.

## Dependency conflict findings

### 1. Python dependencies are compatible but under-governed

`uv pip check` reports all installed packages compatible. Current installed
versions include:

| Package | Installed | Declared |
|---|---:|---|
| Python | 3.12.3 | `>=3.12` |
| `ansible-core` | 2.21.0 | `>=2.16` |
| `textual` | 8.2.7 | `>=0.80` |
| `rich` | 15.0.0 | `>=13.7` |
| `typer` | 0.25.1 | `>=0.12` |
| `pydantic` | 2.13.4 | `>=2.7` |
| `pytest` | 9.0.3 | `>=8.0` |
| `mypy` | 2.1.0 | `>=1.10` |

Risk: broad lower bounds allow major drift. Backlog already has
`DEF-PYTEST9-CAPLOG`, which is the kind of issue this drift creates. The repo
needs constraints/lock policy per component plane instead of only lower bounds.

### 2. Version checker has blind spots and one comparison bug

`scripts/version_check.py` says it scans `pyproject.toml`, but
`build_reports()` currently scans only service descriptors and Dockerfiles.
ADR-0012 already lists pyproject scanning as future P.8, so the script header is
ahead of the implementation.

Online run on 2026-05-23 found real update pressure:

| Component | Current | Latest/probed | Status |
|---|---:|---:|---|
| `ragflow` | v0.25.5 | v1.0 | major |
| `mysql` | 8.0.46-oraclelinux9 | 9.7.0 | major |
| `portainer-ce` | 2.41.1 | 2.42.0 | minor |
| `prometheus` | v3.5.3 | v3.11.3 | minor |
| `docling-serve-cpu` | v1.18.0 | v1.19.0 | minor |
| `authelia` | 4.39.6 | 4.39.19 | patch |
| `nginx` | 1.31.0-alpine | 1.31.1 | patch |
| `qdrant` | v1.18.0 | v1.18.1 | patch |

Scanner limitations:

- `cadvisor` is pinned at v0.57.0 but probe returned v0.55.1; the script
  marked it as `minor`, even though current is newer than probe. Need
  `newer_than_probe` or `registry_incomplete`.
- `grafana/grafana:13.0.1+security-01` vs `13.0.1-security-01` is treated as
  up-to-date by numeric compare, but the tag normalization should be explicit.
- GHCR/Docker Hub/Quay tag styles still produce errors for Open WebUI,
  Dify Web, Milvus, MinIO, and Weaviate. These need per-source adapters or
  GitHub-release mappings.

### 3. Reverse proxy conflicts are deploy-level conflicts

The revised compatibility checker correctly stopped claiming that Traefik,
Caddy, and Nginx are inherently incompatible services. But their default host
ports still collide:

```text
80:  caddy, nginx, traefik
443: caddy, nginx, traefik
```

This should be modeled as `host_port_conflict`, not as
`ServiceDescriptor.conflicts_with`. Same service can coexist in different
deploy targets or if published ports are disabled/remapped.

### 4. Dify stack is modeled as five providers

Every Dify service currently provides `dify_stack`, producing a redundant
provider warning:

```text
dify_stack: dify-api, dify-plugin-daemon, dify-sandbox, dify-web, dify-worker
```

This is not a provider conflict; it is a stack membership marker. Proposed fix:
move this to `stack: dify` or `component_group: dify`, and reserve `provides`
for behavioral contracts another service can consume.

### 5. Runtime build deps are not separated from Python app deps

Dockerfiles install backend-specific dependencies directly:

- CPU/Vulkan install PyTorch from the CPU wheel index;
- Vulkan builds `llama-cpp-python>=0.3.23` with `GGML_VULKAN=ON`;
- ROCm uses AMD nightly PyTorch index for gfx1151 and builds
  `llama-cpp-python>=0.3.23` with HIP flags;
- all backend Dockerfiles use unpinned pip installs for several heavy packages.

This is acceptable for a moving research branch, but not for universal deploy.
Backend dependencies need separate constraints:

```text
constraints/core.txt
constraints/dev.txt
constraints/backend-cpu.txt
constraints/backend-vulkan.txt
constraints/backend-rocm-gfx1151.txt
constraints/service-version-matrix.yaml
```

### 6. Model catalog is duplicated

There are two model recommendation sources:

- `templates/models.yaml`: detailed tiered inventory, LLM/embed/rerank/VLM,
  llama.cpp build requirements, known issues;
- `agmind/install/models.py`: wizard runtime catalog.

They are already drifting. Example: embed primary in `templates/models.yaml`
uses `gpustack/bge-m3-GGUF`, while the runtime wizard uses
`lm-kit/bge-m3-gguf`. The runtime catalog should be generated from one source,
or the YAML should become the canonical source read by the wizard.

### 7. Dify/RAGFlow dependencies need explicit upstream-compatible baselines

Current local choices are deliberate but need a contract:

- Dify uses local `postgres:17.10-alpine3.22` and `redis:8.4.3-alpine`, while
  upstream examples commonly pin older DB/cache baselines. Keep AGmind's
  recommendation if verified, but represent upstream-compatible minimum
  separately from AGmind recommended.
- RAGFlow consumes `search_index` (`elasticsearch`) plus MySQL/MinIO/Redis.
  The v1.0 major should be held until R18/RAGFlow compatibility is revisited.

## Proposed component model

Add a first-class `ComponentContract` layer. Service descriptors describe how
to run a container; component contracts describe version ownership and
compatibility.

```yaml
id: dify
kind: app_stack
owner: agmind-core
core:
  upstream: https://github.com/langgenius/dify
  recommended_version: "1.14.2"
  update_policy: grouped
  hold_reason: "api/web/worker/plugin-daemon must co-bump"
runtime:
  service_descriptors:
    - dify-api
    - dify-web
    - dify-worker
    - dify-sandbox
    - dify-plugin-daemon
requires:
  python: []
  services:
    - capability: postgres_db
      min_version: "15"
      recommended_version: "17.10-alpine3.22"
    - capability: redis_cache
      min_version: "6"
      recommended_version: "8.4.3-alpine"
optional_providers:
  vector_db:
    recommended: qdrant
    allowed: [qdrant, weaviate, milvus]
verification:
  commands:
    - "agmind render compose --profile core,rag --domain ci.example.com"
    - "docker compose config --quiet"
```

Recommended fields:

| Field | Meaning |
|---|---|
| `id` | stable component id, not necessarily a service name |
| `kind` | `core`, `backend`, `app_stack`, `stateful_service`, `edge`, `ops`, `deploy_target`, `model_family` |
| `core.upstream` | canonical upstream source |
| `core.min_version` | lowest supported upstream version |
| `core.recommended_version` | AGmind default |
| `core.current_pin` | actual committed pin if different |
| `core.update_policy` | `free`, `patch-auto`, `minor-review`, `major-hold`, `grouped`, `volatile` |
| `runtime.service_descriptors` | concrete services implementing the component |
| `requires` | capabilities, packages, host/kernel/runtime deps |
| `provides` | behavioral contracts consumed by other components |
| `conflicts` | real conflicts only: port, host device, data path, singleton |
| `verification` | exact commands/tests for bump acceptance |

## Recommended component boundaries

| Boundary | Core | Recommended contracts |
|---|---|---|
| AGmind core | CLI/TUI, schemas, renderer, deploy runner | Python 3.12, pinned dev/runtime constraints, no backend-heavy deps |
| Compute backend | CPU/Vulkan/ROCm image builds | one constraints file per backend, explicit llama.cpp build, GPU capability contract |
| Inference services | `llama-*` descriptors | model role (`llm/embed/rerank`), context/KV defaults, required server flags |
| App stacks | Dify, RAGFlow, Open WebUI, Docling, future ComfyUI | stack contract maps multiple service descriptors to one version policy |
| Stateful services | DB/cache/vector/search/object storage | storage class, backup policy, upgrade/migration policy |
| Edge/security | Traefik/Caddy/Nginx/Authelia | host port singleton rules, auth mode, TLS provider |
| Ops | Prometheus/Grafana/Loki/Alloy/exporters/Portainer | dashboard/provisioning version family, scrape contracts |
| Deploy target | Ubuntu Compose, Proxmox VM Compose, k3s, RKE2 | provisioner/runtime/storage/secrets profiles |
| Model artifacts | LLM/embed/rerank/VLM GGUFs | one canonical YAML catalog, generated wizard entries |

## Implementation order

Do this before adding ComfyUI/Proxmox/K8s as first-class features:

1. **Component contracts skeleton**
   - add `templates/components/*.yaml` or `templates/component_contracts/*.yaml`;
   - create contracts for `agmind-core`, `llama-cpp-gfx1151`, `dify`,
     `ragflow`, `edge-proxy`, `observability-stack`, `model-catalog`.
2. **Version check v2**
   - scan component contracts, pyproject deps, Ansible Galaxy, Dockerfile pip
     installs, Dockerfiles, service descriptors, and model catalog;
   - add statuses: `newer_than_probe`, `volatile`, `grouped_hold`,
     `source_error`;
   - map GH releases for components with non-semver registry tags.
3. **Compatibility checker split**
   - keep capability warnings;
   - add host-port/data-path singleton checks;
   - move Dify/RAGFlow stack membership out of `provides`.
4. **Constraints**
   - introduce per-plane constraints and use them in CI/Docker builds;
   - keep pyproject lower bounds for library consumers, constraints for
     reproducible AGmind deploys.
5. **Model catalog unification**
   - make YAML canonical or generate Python `CURATED_MODELS` from YAML;
   - add verification metadata per model file.

## Sources

- Dify self-hosted environment variables:
  https://docs.dify.ai/getting-started/install-self-hosted/environments
- RAGFlow docker environment:
  https://github.com/infiniflow/ragflow/blob/main/docker/.env
- llama.cpp build documentation:
  https://github.com/ggml-org/llama.cpp/blob/master/docs/build.md
- Docker Compose specification:
  https://docs.docker.com/reference/compose-file/
- ROCm install / compatibility docs:
  https://rocm.docs.amd.com/en/latest/compatibility/compatibility-matrix.html
- Grafana provisioning:
  https://grafana.com/docs/grafana/latest/administration/provisioning/
- Textual CSS and widgets:
  https://textual.textualize.io/guide/CSS/
