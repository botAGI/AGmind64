# AGmind Dependency Map

Last updated: 2026-05-23.

## Internal Tiers

### Tier 0 - foundations

These modules should not import high-level project modules:

- `agmind.log`
- `agmind._env`
- `agmind.i18n`
- `agmind.config.env`
- `agmind.compute.base`
- `agmind.compute.config`
- `agmind.schemas.service`

### Tier 1 - detection and primitives

- `agmind.compute.detect` depends on logging and host/system probes.
- `agmind.secrets` depends on logging and filesystem permissions.
- `agmind.models` depends on service YAML parsing and compute detection.
- `agmind.services.capability_bindings` is table-like and should remain light.

### Tier 2 - domain services

- `agmind.compute._registry` discovers backend entry points and imports backend
  classes lazily.
- `agmind.compute.backends.*` implement `Backend` and may import heavy runtime
  engines lazily from `_engines/`.
- `agmind.compute.clients.llama_server` is the HTTP client for running
  llama-server containers.
- `agmind.services.registry`, `renderer`, `compatibility` own service graph
  loading and compose generation.
- `agmind.install.*`, `deploy.*`, `ops.*`, `cluster.*`, `diagnostics.*` are
  workflow/domain modules.

### Tier 3 - user surface

- `agmind.cli.*` and `agmind.cli.tui.*` are leaf modules. They may import
  domain services lazily, but domain modules should not import CLI/TUI modules.
- `agmind.__main__` only delegates to `agmind.cli.app`.

## Heavy Dependency Rules

- `llama_cpp` imports stay inside engine methods.
- Torch imports stay in backend/runtime lanes, not import-time package paths.
- PyYAML is allowed when installed; `services.registry` keeps a tiny fallback
  parser for narrow legacy compatibility.
- Textual imports stay inside TUI modules.
- Ansible is invoked by install steps as a subprocess; Ansible modules should
  not become Python runtime dependencies.

## External Dependencies

### Core install

From `pyproject.toml`:

- `numpy`
- `typer`
- `rich`
- `questionary`
- `platformdirs`
- `pydantic`
- `ansible-core`
- `structlog`
- `textual`
- `pyfiglet`
- `PyYAML`
- `huggingface_hub`
- `zeroconf`

### Optional extras

- `cpu`: `torch`, `onnxruntime`, `llama-cpp-python`, `scipy`
- `vulkan`: `llama-cpp-python`, `scipy`
- `rocm`: `torch`, `onnxruntime`, `llama-cpp-python`, `scipy`
- `dev`: `pytest`, `pytest-asyncio`, `pytest-cov`,
  `pytest-benchmark`, `ruff`, `mypy`, `pre-commit`, type stubs

## Version Governance

Dependency planes:

- Python app/runtime dependencies: `pyproject.toml` plus constraints files.
- Backend build dependencies: Dockerfile pip specs and backend constraints.
- Container service images: `templates/services/*.yaml`.
- Component ownership and update policy: `templates/components/*.yaml`.
- Ansible collections: `ansible/requirements.yml`.
- Model artifacts: `templates/models.yaml`.

Weekly update reports must show both service pins and component policies.

Deployment target contracts live in `templates/deploy-targets/*.yaml`. They do
not currently drive version scanning, but they define which runtime,
provisioner, configurator, storage, and secrets planes are valid for future
OpenTofu/Ansible/Kubernetes work.
Deploy target and Kubernetes proof workflow validation should use
`agmind.deploy.target_checks.DeploymentCheckReport` when automation needs
machine-readable output. The older `validate_deploy_targets()` and
`validate_kubernetes_proof_workflow()` functions intentionally remain
message-list wrappers for compatibility with existing tests and scripts.
`agmind targets validate --json` and `agmind governance validate --json` are
the preferred operator/automation surfaces for these structured reports.
Component, tool-candidate, and constraint scripts also support `--json`; keep
their count/error payloads available so aggregate governance JSON stays fully
machine-readable. Aggregate governance JSON includes a top-level `summary` so
automation can read check coverage and common domain counters without parsing
the nested script payloads first. Keep `total_warnings`, `total_infos`, and
`total_errors` aligned with the structured count fields exposed by each gate.

The first Proxmox target module in `infra/proxmox/vm-compose` uses OpenTofu
with the `bpg/proxmox` provider pinned to `~> 0.93.0`. Real Proxmox API tokens
belong in local ignored `terraform.tfvars`, environment injection, or a future
SOPS/age lane, never in tracked files.

`scripts/proxmox_inventory.py` consumes `tofu output -json` and writes a local
generated Ansible inventory. These generated files are ignored with
`ansible/inventory/*.generated.yml` because they contain environment-specific
hostnames and IP addresses.

`agmind.cluster.inspect` is the read-only bridge between local host probes and
deploy target selection. It shells out only to existing local tools (`docker`,
`kubectl`, `pveversion`, `systemd-detect-virt`) through injectable command
probes, and reuses mDNS peer discovery from `agmind.cluster.detect`.

Optional tool candidates live in `templates/tool-candidates/*.yaml`. They are
not runtime dependencies until accepted. A candidate must graduate through
image/version, license, port, storage, secrets, component ownership, and
service descriptor checks before it can affect deployments.

Tool candidates may carry `recommended_version` and `version_source` for
research-backed addon baselines. This is required for Kubernetes add-ons such as
Longhorn and External Secrets Operator where version ownership exists before an
AGmind service descriptor exists.

`proxmox-exporter` is the first accepted optional runtime descriptor. It pins
`prompve/prometheus-pve-exporter:3.9.0` by digest and only joins deployments
when the `proxmox` profile is selected. Its Proxmox API token belongs in the
local mounted `/etc/agmind/proxmox-exporter/pve.yml`, not in tracked files.
Ansible renders that file from `agmind_proxmox_exporter_user`,
`agmind_proxmox_exporter_token_name`, and
`agmind_proxmox_exporter_token_value` only when the `proxmox` profile is
enabled and `agmind_proxmox_exporter_existing_config` is false.
`agmind.deploy.proxmox_exporter` validates the YAML using existing PyYAML and
uses Python stdlib `urllib` for optional endpoint probing, so no new runtime
dependency is introduced.

Current constraint planes:

- `constraints/core.txt`: direct runtime dependencies from
  `[project].dependencies`.
- `constraints/dev.txt`: developer/test tooling from the `dev` extra, with
  `-c core.txt`.
- `constraints/cpu.txt`: CPU backend packages and Dockerfile pip installs, with
  `-c core.txt`.
- `constraints/vulkan.txt`: Vulkan backend packages and Dockerfile pip installs,
  with `-c core.txt`.
- `constraints/rocm-gfx1151.txt`: ROCm gfx1151 backend packages and Dockerfile
  pip installs, with `-c core.txt`.

`scripts/constraints_check.py` is the guardrail: it validates plane existence,
specifier syntax, pyproject coverage, optional extra coverage, and backend
Dockerfile pip coverage.

## Model Catalog Governance

`templates/models.yaml` is the canonical model catalog. It now owns both the
full tier inventory and the setup wizard's curated short list under
`wizard_catalog`.

Runtime modules should load model choices through `agmind.models`. Legacy
imports from `agmind.install.models` remain supported as a compatibility facade,
but new model metadata must be added to YAML first.

Curated setup defaults must resolve to YAML entries for all active model roles:
LLM, embedding, and rerank. The default reranker is
`bge-reranker-v2-m3-q8`, matching the upstream AGmind `bge-reranker-v2-m3`
lane while using the x86 GGUF catalog shape.

## RAG Retrieval Dependencies

Dify vector stores are selected through the `vector_db` capability and injected
by `agmind.services.capability_bindings`. RAGFlow uses the separate
`search_index` capability; the current catalog provider is Elasticsearch.
`templates/services/dify-api.yaml` must not carry hardcoded Qdrant env or
`depends_on` entries, otherwise Milvus/Weaviate setup choices cannot replace the
Dify vector backend cleanly. Dify provider env injection is direct-consumer
only: `dify-api` and `dify-worker` consume LLM/embedding/vector providers;
web/sandbox/plugin-daemon are component members, not provider consumers.
RAGFlow uses `rag_flow` as the MySQL database name and references the shared
compose secrets through `${MYSQL_ROOT_PASSWORD}`, `${MINIO_ROOT_USER}`,
`${MINIO_ROOT_PASSWORD}`, and `${REDIS_PASSWORD}`.
Because Redis is shared by Dify and RAGFlow, its service descriptor carries
both `rag` and `ragflow` profiles.
Compose and Kubernetes renderers both merge capability env through
`agmind.services.renderer.descriptors_with_capability_env`; new render targets
must reuse that helper or an equivalent shared policy instead of reimplementing
provider choice. Dify vector priority is Milvus, Weaviate, then Qdrant when
multiple explicit providers are present, and setup summaries must still warn
that such a Dify selection is ambiguous.
Operator summaries should consume
`agmind.services.deployment_topology.build_deployment_topology_report*` so
retrieval, dependency, and compatibility warnings stay aligned across setup
preview, final confirmation, `agmind render topology`, and future deploy
commands. Automation should prefer the report JSON payload rather than parsing
the text block, and use `agmind render topology --fail-on-warning --json` when
topology warnings should fail a pipeline.
Optional topology notes are exposed as `infos`/`info_count` in the same payload;
consumers must not treat those records as `--fail-on-warning` failures.
`scripts/topology_check.py` is the repository gate for standard profile lanes;
it delegates to `agmind.services.topology_checks`. Do not duplicate topology
policy in workflow YAML or ad hoc shell checks.

## CI Dependencies

`agmind ci status` depends on the local GitHub CLI (`gh`) for read-only
operator visibility. It detects `owner/name` from `AGMIND_GITHUB_REPO` or
`git remote.origin.url`, then calls `gh run list` and `gh api
repos/<owner>/<repo>/actions/runners`. Missing `gh`/auth is surfaced as a
command warning; it is not a Python package dependency and should not be added
to `pyproject.toml`.

The self-hosted CI assumes:

- system `python3` is Python 3.12 compatible
- `$HOME/.local/bin/uv`
- `$HOME/.local/bin/uvx`
- `gh` for local operator runner visibility outside GitHub UI
- Docker daemon with access to `/dev/dri` and `/dev/kfd` on Strix Halo
- GitHub runner labels: `self-hosted`, `linux`, `x64`, `strix-halo`
- Real k3s proof runner label: `k3s`, plus kubeconfig access, `kubectl`, AMD
  GPU device-plugin allocatable `amd.com/gpu`, and permission to run
  server-side dry-run against the target cluster

The workflow intentionally does not use `actions/setup-python` for normal CI
jobs. The runner already has the needed Python, and setup-python toolcache
downloads stalled repeatedly.

## Import Cycle Policy

Keep the graph pointed upward:

```text
foundation -> domain -> orchestration -> CLI/TUI
```

Disallowed directions:

- domain module importing CLI/TUI
- schema/catalog module importing deploy/install
- backend registry importing heavy engine modules at import time
- test-only helpers imported by runtime code

## Update Checklist

When adding a dependency:

1. Decide whether it is core, optional extra, or dev-only.
2. Add a test that proves import without optional heavy deps still works.
3. Update `pyproject.toml`.
4. Update this file if the dependency changes a boundary.
5. Run `pre-commit`, `mypy`, and the relevant pytest marker set.
