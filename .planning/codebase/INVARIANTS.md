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

Provider resolution for env injection must be consumer-aware. If multiple
services provide a capability, renderers must prefer a provider with an
explicit binding for the target consumer before falling back to deterministic
ownership. Compose and Kubernetes must share this merge path. Dify vector
provider priority must stay aligned with retrieval topology summary.

## Component Version Contracts

Every first-class tool must have exactly one component contract in
`templates/components/*.yaml`. Service descriptors define runtime shape;
component contracts define version ownership and update policy.

Stack membership must not be encoded as `provides`. Use component runtime
membership for Dify, RAGFlow, observability, and future app stacks. Reserve
`provides` for behavioral capabilities consumed by other services.

Deploy-level conflicts such as host ports `80/443` are not service capability
conflicts. They must be checked by component/deploy validation.

## Deploy Target Contracts

`templates/deploy-targets/*.yaml` owns deploy lane selection: runtime,
provisioner, configurator, storage profile, secrets profile, and target-level
verification commands.

Supported and experimental deployment targets must pass
`agmind targets validate`: no future renderer placeholders, verification
commands present, provisioner modules present when declared, and configurator
playbooks pointing at tracked files. Keep `scripts/deploy_target_check.py` in
pre-commit and CI whenever target contracts, target checks, or target CLI code
change.

Do not put OpenTofu, Proxmox, Kubernetes storage class, Vault, or target
inventory fields into `ServiceDescriptor`. Service descriptors describe
runtime services; deployment targets describe where and how the stack is
provisioned and configured.

Cluster/environment detection must stay read-only. `agmind cluster inspect`
may recommend a deployment target, but it must not create kube resources,
write inventories, apply OpenTofu, or mutate Docker/Proxmox state.

OpenTofu provisions infrastructure. Ansible configures Ubuntu/Compose hosts.
Kubernetes and Nomad require explicit renderers before they become supported
runtime targets. The current `agmind render kubernetes` path is a research
MVP; it must keep warning on Docker-only fields until real k3s evidence proves
the mapping. Kubernetes targets may not move to `experimental` or `supported`
while `blocker` severity render warnings remain.

Tracked OpenTofu modules may contain example variables only. Real
`terraform.tfvars`, state files, plan files, provider caches, and Proxmox API
tokens must stay ignored and local.

Generated inventories from Proxmox/OpenTofu outputs are local runtime artifacts,
not source. Keep them under `ansible/inventory/*.generated.yml` or another
ignored operator path.

## Optional Tool Admission

New homelab/enterprise tools must start as `ToolCandidate` records under
`templates/tool-candidates/*.yaml` unless they are purely internal code. Do not
add service descriptors for ComfyUI, n8n, Keycloak, Vault/Infisical, Harbor,
backup runners, or similar stacks until their candidate record names the target
deployments, contracts, storage/secrets profiles, ports, risks, and next
verification step.

Accepted optional services must remain opt-in unless there is an explicit
product decision to make them default. `proxmox-exporter` uses the `proxmox`
profile instead of the generic `observability` profile so standard metrics
deployments do not require Proxmox credentials.

Accepted optional service candidates must match real runtime state. The
candidate id must resolve to a service descriptor, exactly one component owner,
a digest-pinned image, declared profiles, and declared ports. Keep this rule in
`agmind.addons.checks` so `scripts/tool_candidate_check.py` and
`agmind tools validate` cannot drift. Keep tool-candidate schema validation and
`scripts/tool_candidate_check.py` in pre-commit and CI because changes in
services/components can invalidate accepted optional candidates.

Optional services that mount credential files must fail early or require an
explicit externally managed config flag. Do not let Docker Compose create
missing secret file paths as directories during `up`.

Credential validators must never echo secret values in errors, summaries, or
Ansible output. Report missing field names and structural issues only.

## Models

### I.9 `templates/models.yaml` is the model source of truth

LLM/embed/rerank/VLM selections belong in the model catalog.

Setup wizard defaults for every active role must resolve to curated
`wizard_catalog.entries`. Do not use `custom` as a default unless the role is
intentionally disabled by product decision.

### I.10 RAG retrieval backends are role-specific

Dify vector-store selection is `vector_db`. RAGFlow document engine selection is
`search_index`. Do not hardcode Qdrant into Dify service dependencies, and do
not inject Milvus/Qdrant/Weaviate into RAGFlow. Setup UI may enforce a single
Dify vector provider, but renderer/catalog compatibility may still allow
multiple vector stores for advanced manual deployments. If multiple Dify vector
providers are selected, compatibility must warn about the ambiguous active
`VECTOR_STORE`. Dify leaf services (`dify-web`, `dify-sandbox`,
`dify-plugin-daemon`) must not directly consume model/vector provider
capabilities. RAGFlow must render explicit MySQL/MinIO/Redis wiring and its
component contract must declare `mysql_db`, `object_storage`, `redis_cache`,
and `search_index`. Any profile lane that includes RAGFlow must also include
Redis.

Operator-facing topology text must come from
`agmind.services.deployment_topology`, not from ad hoc TUI/CLI string assembly.
That report owns RAG storage lines, missing dependency warnings, and
compatibility warnings for selected services. CLI surfaces such as
`agmind render topology` must use the same report object. JSON output must
remain structured enough for CI: counts, sources, severity, kind, services,
capability, message, and retrieval provider fields. `--fail-on-warning` must
return non-zero only after emitting the diagnostic report.
Standard profile lanes must stay topology-clean through
`scripts/topology_check.py`. Optional capability gaps such as
`dify_external_kb` must be informational, not warning-level CI failures.
Topology report JSON must keep warning and info records separate: informational
notes can appear in `infos`/`info_count`, while `warnings`/`warning_count` remain
the only non-error topology signals that `--fail-on-warning` may fail on.
Topology validation policy belongs in `agmind.services.topology_checks`; scripts
and future commands should delegate to it instead of reimplementing profile
lane checks.

### I.11 Model files are artifacts, not source

`/models/`, `*.gguf`, `*.safetensors`, and similar payloads stay ignored.

## CLI And TUI

### I.12 CLI is a leaf layer

Domain modules must not import CLI/TUI modules.

### I.13 CLI handlers stay thin

Handlers parse input and delegate. Business logic belongs in domain modules.

### I.14 TUI state must be testable

Wizard state, install state, and status/deploy views should have focused tests
for transitions and rendering assumptions.

## Install And Host Mutation

### I.15 Ansible owns privileged bootstrap

Python can orchestrate; Ansible mutates apt, groups, sysctl, Docker daemon,
firewall, and host service bootstrap.

### I.16 Secrets are files with restrictive modes

Production credentials are written with mode `0600` and masked in logs.

## CI And Tooling

### I.17 Self-hosted CI uses system Python and `uv`

Do not reintroduce `actions/setup-python` into the normal self-hosted CI path
unless the runner/toolcache issue is intentionally solved.

### I.18 Host pytest lane installs `.[dev]`, not backend extras

Native `llama-cpp-python` backend builds belong in Docker/backend lanes.
`test-cpu` covers core Python behavior and lazy fallback.

### I.19 Runtime image smoke is not pytest

Backend runtime images are production images. Strix smoke checks
`get_backend().device_info()` inside the image. The smoke job must wait for
the Docker backend matrix, otherwise it can test stale `agmind-*:ci` tags left
on the self-hosted runner.

### I.20 Audit must stay green

`scripts/audit_forbidden.py --fail` is a merge gate.

### I.21 Executable scripts must be executable in Git

If tests require a script to be executable, track mode `100755`, not just local
filesystem chmod.

### I.22 Dependency planes stay constrained

Direct Python deps, dev tooling, and backend extras must be represented in
`constraints/*.txt`. Backend Dockerfiles must install through their matching
constraint plane.

## Planning Discipline

### I.23 Aggregate governance is a convenience, not the only gate

`agmind governance validate` must keep reusing individual governance checks.
Do not hide component, deploy-target, tool-candidate, or constraints failures
behind a single vague pass/fail message; the aggregate output must preserve the
individual check names and details. In CI, keep the focused jobs and run
`governance-validate` as a summary after them, not as a replacement.
Deploy-target and Kubernetes proof workflow checks must keep structured issue
metadata available through `DeploymentCheckReport`; scripts may render compact
text, but machine consumers should not have to parse plain error strings.
When `agmind governance validate --json` is used, every check must include a
parsed `payload` object so automation can read counts and issue kinds without
scraping captured stdout.
The aggregate JSON must also keep a top-level `summary` with check counts,
payload coverage, key domain counters, Kubernetes warning totals, and
`total_warnings`, `total_infos`, `total_errors`, `failed_checks`,
`warning_checks`, `info_checks`, `health_status`, `status_counts`, and
`check_health`; CI and UI consumers should not have to walk nested payloads for
the common health line or per-gate health table. Warning totals should count
warning/blocker severity separately from informational notes, and status labels
must be derived from error > warning > info > ok precedence.
The text `governance validate` output must preserve readable per-gate details
and include the same health status plus warning/info/error totals in its final
line. Failed checks without structured payloads must still be counted as errors
and must still render that final health suffix; an unknown aggregate check name
is a failure, not an unclassified text-only condition. Structured
`payload.error_count > 0` must make the aggregate fail even if the child process
returned exit code `0`; top-level `ok`, summary counts, per-check JSON,
`check_health`, `GovernanceCheckResult.ok`, and text status must use the same
effective pass/fail rule. Raw child process status belongs in `returncode`.
Non-zero child return codes must contribute at least one aggregate error even
when a structured payload omits `error_count`; payload error counts may increase
the total but must not hide a failed process.
JSON-capable checks in structured aggregate mode must return a parsed object
payload. Missing, malformed, or non-object `--json` output is a governance
failure even when the child exits `0`, because CI/UI consumers rely on payloads
being real machine-readable reports. Such failures must be exposed as
per-check `payload_error` and summarized through `payload_error_count` and
`payload_error_checks`; machine consumers must not scrape stderr to classify
broken structured payloads.
Aggregate wrappers must pass explicit argv tuples to child check entrypoints;
do not let outer CLI flags leak through `sys.argv` into nested script `main()`
calls.

### I.24 `.planning/` is the durable GSD memory

Update `STATE`, `ROADMAP`, `BACKLOG`, session notes, and codebase maps when
work changes the project state.

### I.25 Claude artifacts are not project memory

`.claude/` and `CLAUDE.md` are ignored and removed from source control.

### I.26 ADR for durable architecture shifts

New extension surfaces, backend strategy changes, or deploy architecture
changes need an ADR.

### I.27 Recon before new external systems

Before adding a new backend/engine/vendor service, add a research note under
`.planning/research/` with dated evidence.
