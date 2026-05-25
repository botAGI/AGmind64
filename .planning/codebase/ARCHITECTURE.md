# AGmind Architecture

Last updated: 2026-05-24.

AGmind is a private LLM/RAG platform for AMD Strix Halo and generic x86_64.
The runtime target is a local, self-hosted stack with one-command install,
capability-aware service composition, and day-2 operations.

## Layer Model

```text
Layer 5  CI / hardware evidence
         .github/workflows/ci.yml
         self-hosted runner, Docker backend matrix, Strix Halo smoke

Layer 4  Host orchestration
         infra/proxmox/vm-compose
         ansible/install.yml + 11 roles
         infrastructure provisioning, privileged bootstrap, Docker, models,
         services, observability, security

Layer 3  Declarative catalogs
         templates/services/*.yaml
         templates/components/*.yaml
         templates/deploy-targets/*.yaml
         templates/tool-candidates/*.yaml
         templates/models.yaml
         templates/schemas/{service,component,deploy-target,tool-candidate}.json
         templates/observability/*

Layer 2  Python domain runtime
         agmind/compute, services, schemas, install, deploy, ops, cluster,
         diagnostics, models, migrations, secrets, governance

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

### RAG Retrieval Policy

Dify and RAGFlow do not share a generic vector-store contract. Dify consumes
`vector_db` and can use Qdrant, Milvus, or Weaviate from the current catalog.
RAGFlow consumes `search_index` and uses Elasticsearch as its current
`DOC_ENGINE` provider. Setup must keep those decisions explicit: selecting
Milvus for Dify should not silently imply Milvus for RAGFlow, and should not
pull Qdrant as a hidden Dify dependency.
If an imported or hand-edited state contains multiple Dify vector providers,
the operator summary must show the ambiguity even though renderer output
remains deterministic.

Inside the Dify component, only `dify-api` and `dify-worker` are direct
consumers of model/vector providers. `dify-web`, `dify-sandbox`, and
`dify-plugin-daemon` remain stack leaf services; stack-level requirements live
in `templates/components/dify.yaml`.

RAGFlow is an app plus stateful runtime dependencies: MySQL (`rag_flow` DB),
MinIO, Redis, and a `search_index` provider. The service descriptor should
carry explicit non-secret host/port wiring and secret references, while the
component contract declares the required capabilities. Profile-based renders
must include this closure too; shared Redis therefore belongs to both `rag` and
`ragflow` profiles.

Capability env injection is renderer-shared. Compose and Kubernetes both call
the same descriptor merge helper before rendering objects. Provider resolution
is consumer-aware: a service that provides a capability is only preferred for a
consumer when `capability_bindings` has an explicit env binding for that
consumer; otherwise the resolver falls back to deterministic ownership for
non-env capabilities. Dify vector DB priority follows the operator-facing
topology policy: Milvus, then Weaviate, then Qdrant.

`agmind.services.deployment_topology` is the shared operator report for
selected services. Setup preview and final confirmation must use it for RAG
storage lines, missing runtime dependency warnings, and compatibility warnings
so Compose/Kubernetes/operator UX cannot drift into separate explanations of
the same topology. `agmind render topology` exposes the same report for
terminal and CI checks, including structured warning counts and retrieval
provider fields in JSON mode. Its `--fail-on-warning` flag is the non-mutating
CI gate for topology ambiguity and missing runtime dependency checks.
The topology report separates non-blocking informational notes from warnings:
optional integration gaps such as `dify_external_kb` appear in JSON/profile
summaries as `info` records and do not trip `--fail-on-warning` or standard
profile governance.
`scripts/topology_check.py` runs that policy over standard profile lanes and
feeds aggregate governance, pre-commit, and self-hosted CI. The reusable policy
lives in `agmind.services.topology_checks`; the script remains a thin entrypoint.

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

`.github/workflows/kubernetes-proof.yml` is the manual live-cluster proof lane.
It is `workflow_dispatch` only, requires a self-hosted runner labeled `k3s`,
uses the same `uv` install pattern, runs strict Kubernetes render validation,
runs the contract-defined k3s server dry-run proof into
`local-kubernetes-proof/k3s`, verifies the bundle with `if: always()`, writes
the verifier JSON report to `verification.json`, and prints always-run bundle
diagnostics before uploading the proof artifacts, including
checksum-verified `run-metadata.json`, for review.
`scripts/kubernetes_proof_workflow_check.py` validates that this manual
workflow stays aligned with the `k3s` deployment target proof contract,
including the always-run verifier, verifier report artifact, and bundle
diagnostic step. Proof artifact checks are scoped to the upload-artifact step
so diagnostic or `tee` references alone are not accepted. Aggregate governance
includes this guard as `kubernetes-proof-workflow`.

## Key Modules

| Module | Responsibility |
|--------|----------------|
| `agmind/cli/__init__.py` | Typer app and command registration |
| `agmind/addons/` | Optional tool candidate catalog and admission contracts |
| `agmind/cli/tui/` | Textual setup/install/status/deploy UI |
| `agmind/compute/base.py` | Backend and handle contracts |
| `agmind/compute/_registry.py` | Entry-point discovery and auto-select |
| `agmind/compute/backends/` | CPU, Vulkan, ROCm, NPU backend implementations |
| `agmind/compute/clients/llama_server.py` | OpenAI-compatible llama-server client |
| `agmind/services/registry.py` | Descriptor loading and legacy bridge |
| `agmind/services/selection.py` | Setup-time component stack and dependency closure |
| `agmind/services/renderer.py` | Docker Compose rendering |
| `agmind/services/compatibility.py` | Capability/conflict checks |
| `agmind/components/` | Component ownership, version policy, and deploy-level conflict checks |
| `agmind/install/orchestrator.py` | Install workflow coordinator |
| `agmind/install/steps.py` | Install step implementations |
| `agmind/deploy/runner.py` | Render/apply/rollback deployment runner |
| `agmind/deploy/targets.py` | Deployment target contracts and target catalog loader |
| `agmind/ci/monitor.py` | Read-only GitHub Actions run and self-hosted runner monitor via `gh` |
| `agmind/governance.py` | Aggregate M7 governance checks |
| `agmind/ops/backup.py` | Backup and restore tarballs |
| `agmind/cluster/detect.py` | LAN peer discovery |
| `agmind/cluster/inspect.py` | Local runtime/cluster inspection and deploy target recommendation |
| `agmind/diagnostics/doctor.py` | Host readiness checks |
| `agmind/schemas/service.py` | Pydantic service descriptor contract |

## Deployment Targets

Deployment target contracts separate "where AGmind runs" from "which services
AGmind runs".

```text
templates/deploy-targets/*.yaml
  -> agmind.deploy.targets.DeploymentTarget
  -> agmind.deploy.target_checks.validate_deploy_target_report()
  -> scripts/deploy_target_check.py
  -> agmind targets list/status/validate
  -> agmind cluster inspect recommendation enrichment
  -> target runtime/provisioner/configurator/storage/secrets boundary
  -> renderer/provisioner implementation selected by future deploy commands
```

Deploy target and Kubernetes proof workflow gates share
`agmind.deploy.target_checks.DeploymentCheckReport`. The report keeps issue
severity, kind, target id, and message structured for JSON consumers; legacy
validators remain as message-list wrappers for older call sites.
`agmind targets validate --json` exposes this report directly.

Current target ladder:

- `ubuntu-compose`: supported v1.0 lane, operator-provided Ubuntu host,
  Ansible configurator, Docker Compose runtime.
- `proxmox-vm-compose`: experimental homelab lane, OpenTofu provisions Ubuntu
  VM nodes on Proxmox, Ansible configures them, Compose remains runtime.
- `k3s`: research Kubernetes lane. `agmind render kubernetes` now emits a
  plain-manifest MVP with portability warnings. `scripts/kubernetes_dry_run.py`
  is the proof harness for `kubectl apply --dry-run=server`; `--artifact-dir`
  saves manifest/report evidence, and `--require-amd-gpu` records allocatable
  `amd.com/gpu` node evidence before server dry-run. Aggregate `summary.json`
  also records invocation metadata (`kubectl`, context, namespace, cluster/GPU
  requirements, artifact directory, summary path, and selected target ids), so
  skipped local runs and future real proof runs are reviewable as standalone
  bundles. The dry-run target reports and aggregate summary also include the
  same actionable render warning records as the render-check gate, enriched
  with whether each warning code is expected by the deployment target. The

Cluster inspection bridges operator context to this target ladder. `agmind
cluster inspect` probes Docker/Compose, `kubectl`, Kubernetes nodes/storage
classes, k3s markers, Proxmox host/guest hints, and mDNS peers, then recommends
`k3s`, `proxmox-vm-compose`, `ubuntu-compose`, or `unknown` with confidence,
reasons, and warnings. It does not mutate the host and does not replace target
contracts; it is the detection layer that wizard/deploy commands can consume.
  bundle also carries integrity evidence: target reports include rendered
  manifest byte size and SHA256 digest, `proof-command.txt` records the exact
  command that reproduces the proof bundle, `run-metadata.json` records
  allowlisted GitHub Actions and runner provenance, and `checksums.txt` covers
  persisted manifest, dry-run report, proof command, run metadata, and summary
  files.
  `--verify-artifact-dir <dir>` validates copied or uploaded bundles by
  checking `summary.json`, `checksums.txt`, persisted file hashes, and
  per-target manifest byte/digest metadata, and by confirming
  `summary.json::ok` matches its target statuses, `target_ids` matches the
  summary target records, `proof_command --target` flags match `target_ids`,
  `proof_command --require-cluster` matches `summary.json::require_cluster`,
  and by confirming `proof-command.txt`, `run-metadata.json`, and target
  dry-run reports match `summary.json`. It also derives required proof members
  from `summary.json` and rejects bundles where required files are missing
  entirely or missing from `checksums.txt`; checksum member paths must stay
  inside the artifact directory. The dry-run harness accepts repeatable
  `--target <id>` flags, and
  the current `k3s` verification command is pinned to `--target k3s` and
  `--artifact-dir local-kubernetes-proof/k3s` before the real cluster proof.
  Deploy-target validation rejects Kubernetes `--require-cluster` proof
  commands that omit the target id, artifact directory, matching verifier
  command, or expected bundle artifact declarations. A manual
  `kubernetes-proof` workflow runs that contract on a k3s-labeled self-hosted
  runner and uploads the proof bundle. `scripts/kubernetes_proof_workflow_check.py`
  prevents drift between the workflow and target contract. The renderer
  has a narrow Traefik adaptation that replaces Docker provider/socket use with
  Kubernetes provider args, and a Portainer omission policy for the current
  Docker-socket-based Compose UI. It also maps known `/dev/dri` inference
  devices to the Kubernetes extended resource `amd.com/gpu`. The Kubernetes
  renderer resolves explicit non-empty descriptor defaults in env and command
  fields without reading host env. Explicit empty descriptor defaults render
  as empty strings only in Kubernetes env entries; command interpolation still
  requires non-empty values so unsafe model paths remain warning debt. The
  renderer maps secret-like unresolved env values to operator-managed
  `secretKeyRef` entries. It also maps supported Docker security fields to
  Kubernetes `securityContext`: `seccomp=unconfined`, Linux capabilities, and
  numeric supplemental groups. Named groups such as `video` and `render` are
  treated as covered when `/dev/dri` is rendered through the AMD GPU
  device-plugin resource; unrelated named groups still require numeric GID
  policy. No Secret objects are generated yet, so External Secrets/SOPS/manual
  Secret materialization remains required before support promotion. The k3s
  target declares expected warning codes for AMD GPU device-plugin
  prerequisites and Kubernetes omissions. When `AGMIND_RERANK_FILE` is empty,
  `llama-rerank` is omitted from Kubernetes output with an explicit warning,
  matching installer semantics where an empty rerank file disables the service.
  Real cluster evidence is still required before support promotion.

The first Proxmox module skeleton lives in `infra/proxmox/vm-compose`.
It uses the `bpg/proxmox` provider, creates cloud-init snippets plus cloned VM
resources, and exposes outputs shaped for the upcoming Ansible inventory
bridge.

`scripts/proxmox_inventory.py` converts `tofu output -json` into local Ansible
inventory YAML. Generated Proxmox inventories include `agmind_nodes`,
`agmind_master`, and `agmind_workers`, matching `ansible/install.yml`.

`agmind targets list/status/validate` exposes the same target ladder to
operators. The validator checks supported/experimental targets for verification
commands, non-future renderers, existing provisioner modules, and existing
Ansible playbooks. Research targets may point at early renderer prototypes while
remaining outside the supported apply path. `scripts/deploy_target_check.py`
wires this into pre-commit and the self-hosted `deploy-target-validate` CI job;
`schema-validate` also checks `templates/deploy-targets/*.yaml` against
`templates/schemas/deploy-target.json`.

## Optional Tool Candidates

Optional homelab/enterprise tools start in `templates/tool-candidates/*.yaml`,
not in `templates/services/*.yaml`.

```text
templates/tool-candidates/*.yaml
  -> agmind.addons.candidates.ToolCandidate
  -> agmind.addons.checks.validate_tool_candidates()
  -> scripts/tool_candidate_check.py
  -> agmind tools list/status/validate
  -> service/component descriptor work only after admission checks
```

The candidate catalog records admission contracts, deploy target references,
storage/secrets assumptions, ports, risks, and the next implementation step.
This keeps ComfyUI, n8n, Keycloak, Vault/Infisical, Harbor, backup runners, and
other larger stacks out of the runtime until their image, version, license,
storage, secrets, and port stories are verified. `proxmox-exporter` is the
first promoted candidate and remains opt-in via the `proxmox` profile.
Its token config is materialized by the services Ansible role before compose
render/up, or skipped when the operator declares an externally managed config.
`agmind.deploy.proxmox_exporter` validates that config locally and can probe a
running exporter endpoint during real Proxmox smoke. Accepted service-profile
candidates must also pass `scripts/tool_candidate_check.py` runtime admission:
descriptor, component owner, digest pin, profiles, and ports.

Operators can inspect the same state through `agmind tools list`,
`agmind tools status <id>`, and `agmind tools validate`. The script and CLI both
use `agmind.addons.checks`, so CI and day-2 operations report the same
admission errors. `schema-validate` checks `templates/tool-candidates/*.yaml`
against `templates/schemas/tool-candidate.json`, and the self-hosted
`tool-candidate-validate` job runs `scripts/tool_candidate_check.py`.

## Governance Gate

`agmind governance validate` and `scripts/governance_check.py` run the local M7
governance layer as one aggregate report:

- component contracts and deploy-level conflicts;
- deployment target references;
- optional tool candidate admission;
- dependency constraint planes.

The aggregate gate reuses the individual scripts instead of replacing them, so
CI keeps separate visibility while operators get one local smoke command.
The self-hosted `governance-validate` job depends on the focused M7 jobs and
emits the combined report; a narrow pre-commit hook runs the aggregate command
when aggregate wrapper files change.
The current aggregate set is seven checks: components, deploy targets, optional
tools, dependency constraints, topology, Kubernetes render, and Kubernetes proof
workflow drift.
In JSON mode, governance runs every gate in structured mode and stores its
parsed report under each check's `payload` field. The same JSON document also
includes a compact top-level `summary` for CI/operator dashboards: check
coverage, pass/fail counts, payload coverage, component/service/deploy/tool/
constraint/topology counters, Kubernetes warning severity totals, and
`total_warnings`, `total_infos`, `total_errors`, `failed_checks`,
`warning_checks`, `info_checks`, top-level `health_status`, and per-gate
`check_health` rows with status labels plus `status_counts`.
Text mode keeps each gate's readable stdout but runs the structured payload
path too, then appends the same status and warning/info/error totals to the
final operator-facing `governance OK/FAILED` line. If a failed check has no
parsed payload, it is still treated as one error and the final line still shows
`status=failed` with the aggregate totals. Structured `error_count > 0` also
overrides a zero child exit code for aggregate `ok`, counts, per-check JSON,
health rows, `GovernanceCheckResult.ok`, and text status. Raw child process
status remains available as `returncode`. A non-zero child `returncode` is also
an aggregate error floor when a structured payload omits `error_count`.
If a JSON-capable check returns missing, malformed, or non-object `--json`
output, structured aggregate mode treats that as a hard error and records an
`invalid structured JSON payload for <check>` diagnostic. That diagnostic is
also exposed as per-check `payload_error`, while the top-level summary includes
`payload_error_count` and `payload_error_checks`.
Child check entrypoints are always invoked with explicit argv tuples. Text
runs use empty argv and payload runs use `("--json",)`, so aggregate command
flags cannot leak into nested script `main()` calls.

## Deployment Stack

Profiles are composed from the descriptor catalog:

- `core`: inference servers, vector DB, reverse proxy
- `rag`: Dify, Docling, Postgres, Redis, plugin daemon/sandbox
- `ragflow`: RAGFlow with MySQL, Elasticsearch, MinIO
- `ui`: Open WebUI
- `observability`: Prometheus, Grafana, Loki, Alloy, Alertmanager, exporters, cAdvisor, Portainer
- `proxmox`: Proxmox VE exporter, intended to be combined with `observability`
- `security`: Authelia and host security support
- `full`: everything compatible together

The renderer handles exact profile/service filtering, Traefik/nginx/caddy
alternatives, capability environment injection, resources, volumes, and health
checks. Setup wizard selections go through `agmind.services.selection` first:
stack components such as Dify expand from one checked service into their
component siblings, recursive `depends_on` services, and deterministic
mandatory capability providers. Optional service-level integrations such as
Dify's RagFlow external KB remain opt-in.

## Boundaries

- `agmind/` may render files and call subprocesses, but host bootstrap goes
  through Ansible.
- `templates/services/*.yaml` is the service source of truth. Do not recreate
  monolithic `templates/services.yaml`.
- Runtime Docker images are not dev test images. Hardware smoke uses a small
  `python3 -c` backend check, not `pytest` inside runtime images.
- `.planning/` is project memory. `.claude/` is not project memory and is
  ignored.
