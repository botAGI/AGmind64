---
gsd_state_version: 1.2
milestone: v0.6.0
milestone_name: "AGmind x86 — post-M5 hardening + release confidence"
status: m7-governance-payload-error-summary-local
last_updated: "2026-05-25"
last_activity: "2026-05-25 — added governance payload error summary"
progress:
  m1_phases: 7
  m1_completed: 7
  m2_phases: 9
  m2_completed: 9
  m3_phases: 6
  m3_completed: 6
  m4_wave: shipped
  m5_phases: 4
  m5_completed: 4
  m5_percent: 100
---

# State: AGmind x86

## Project Reference

See `.planning/PROJECT.md`.

**Core value:** private LLM/RAG platform for AMD Strix Halo and generic x86_64.
The target operator experience is `agmind install` from clean Ubuntu, then
day-2 CLI/TUI operations, capability-aware service graph, cluster discovery,
observability, and self-hosted CI evidence.

**GSD memory:** `.planning/` is the durable project memory. Claude live config
is not project memory and has been removed.

## Current Position

- **Branch:** `develop`
- **Latest full green CI:** GitHub Actions run `26333245295` on `d21294f`
- **Green gates:** pre-commit, audit, schema validate, compose validate,
  test-cpu, Docker build matrix `cpu/vulkan/rocm`, Strix Halo runtime smoke
  `vulkan/rocm`
- **Python package:** 98 Python files under `agmind/`
- **Tests:** 65 Python files under `tests/`
- **Service descriptors:** 34 files under `templates/services/`
- **Component contracts:** 9 files under `templates/components/`
- **Deploy targets:** 3 files under `templates/deploy-targets/`
- **Tool candidates:** 11 files under `templates/tool-candidates/`
- **Infra targets:** `infra/proxmox/vm-compose` OpenTofu skeleton
- **Ansible roles:** 11 roles
- **Docker backend images:** base, cpu, vulkan, rocm
- **Doctor:** last recorded host state was 7 ok / 2 warn / 0 fail
- **Known host warnings:** kernel/GTT tuning still needs operator attention

## Latest Checkpoint

M6.S0/M6.A cleanup is complete locally:

| Gate | Status | Notes |
|------|--------|-------|
| S0.1 Cloud artifact classification | done | CI/tooling artifacts were committed in focused repair commits through `33a2050` |
| S0.2 Claude artifact cleanup | done | `.claude/` and `CLAUDE.md` removed and ignored |
| S0.3 Codebase map refresh | done | `.planning/codebase/*` rewritten to current post-CI snapshot |
| S0.4 Agent tooling note | done | `.planning/codebase/AGENT_TOOLING.md` added |
| S0.5 Verification | done | pre-commit, forbidden audit, service schema validation, and git status inspected |

Additional CI dependency fix after cleanup:

- Commit `d21294f` gates Strix Halo smoke on the Docker build matrix, so the
  smoke jobs cannot test stale daemon-local `agmind-*:ci` images.
- GitHub Actions run `26333245295` is the latest full green self-hosted run.

Component/version audit after R19:

- `.planning/research/homelab-enterprise/R20-component-boundaries-version-contracts.md`
  records dependency conflicts and the proposed `ComponentContract` direction.
- `uv pip check --python .venv/bin/python` reports the current Python
  environment is compatible, but broad lower bounds leave pytest/textual/ansible
  major drift under-governed.
- Initial scanner coverage was limited to service images and Dockerfiles; M7.A
  expanded governance to component policies, Python deps, Ansible Galaxy,
  Dockerfile pip installs, constraints, and YAML-backed model catalog ownership.
- Capability graph needs a split between real providers, stack membership, and
  deploy-level singleton conflicts such as host ports `80/443`.

M7.A implementation checkpoint:

- `docs/adr/0013-component-contracts-and-safe-updates.md` records the
  service-vs-component boundary and safe update workflow.
- `agmind.components` now provides `ComponentContract` models, a default loader
  for `templates/components/`, and JSON Schema export to
  `templates/schemas/component.json`.
- Baseline contracts cover AGmind core, llama.cpp Strix inference, Dify,
  RAGFlow, stateful services, edge/security, observability, app interfaces, and
  model catalog ownership.
- Repository tests verify every `templates/services/*.yaml` descriptor has
  exactly one component owner and every component service reference exists.
- Deploy-level host-port conflicts now live in `agmind.components.checks`, so
  reverse proxy collisions can be hard errors without turning service
  capability redundancy into a blocker.
- `scripts/version_check.py` now reports `newer_than_probe`, component update
  policies, pyproject dependency specs, Ansible Galaxy collection specs, and
  Dockerfile pip specs while preserving the existing JSON list of container
  pin reports.
- `agmind upgrade --component <id>` now builds component plans, applies
  grouped descriptor bumps, persists grouped rollback state, supports
  `--plan`, and keeps raw single-service upgrade fallback.
- `scripts/component_check.py` validates component references, exactly-one
  service ownership, and deploy-level host-port conflicts. It now runs through
  pre-commit and the self-hosted `component-validate` CI job.
- `constraints/{core,dev,cpu,vulkan,rocm-gfx1151}.txt` define dependency
  compatibility envelopes. `scripts/constraints_check.py` validates pyproject,
  extras, and backend Dockerfile coverage, and the backend Dockerfiles now use
  matching `-c /opt/agmind/constraints/<plane>.txt` installs.
- `templates/models.yaml::wizard_catalog` now owns setup wizard curated model
  ids/defaults. `agmind.models` loads curated entries from YAML, while
  `agmind.install.models` stays as a compatibility facade for existing CLI/TUI
  imports.
- The curated setup catalog now includes the upstream-compatible
  `bge-reranker-v2-m3-q8` reranker and uses it as the default rerank model.
  `SetupState` resolves the reranker repo/file from YAML instead of starting
  with an empty/custom rerank choice.
- Active project docs now treat `.planning/` and `.planning/codebase/` as the
  source of truth. Legacy x86 migration artifacts were removed from the active
  repo on 2026-05-25; `agmind.migrations` remains the current state-schema
  migration subsystem.
- Optional tool admission now includes k3s addon candidates for `longhorn`
  and `external-secrets-operator`. Tool candidates can carry
  `recommended_version` and `version_source`, giving Kubernetes add-ons a
  researched version baseline before they become rendered runtime artifacts.
- RAG retrieval policy is now explicit in setup: Dify consumes `vector_db`
  (Qdrant/Milvus/Weaviate), while RAGFlow consumes `search_index`
  (Elasticsearch today). Choosing Milvus for Dify no longer drags Qdrant into
  setup, and the TUI summary calls out that Milvus applies to Dify only while
  RAGFlow keeps its DOC_ENGINE provider. Dify provider capabilities are now
  direct-consumer only: `dify-api` and `dify-worker` consume model/vector
  backends, while web/sandbox/plugin-daemon stay leaf stack services. RAGFlow
  now renders explicit MySQL/MinIO/Redis host/port/secret references and its
  component contract declares `redis_cache` alongside `mysql_db`,
  `object_storage`, and `search_index`. Redis now carries both `rag` and
  `ragflow` compose profiles so `--profile core,ragflow` renders a complete
  dependency closure.
- Capability env injection is now shared by compose and Kubernetes renderers.
  Provider resolution prefers a provider with an explicit consumer binding
  before falling back to deterministic capability ownership, preventing future
  providers from shadowing a configured backend for Dify/RAGFlow. Dify vector
  provider priority is shared with the topology summary
  (`milvus > weaviate > qdrant`), and the summary now marks multi-provider
  Dify selections as ambiguous instead of hiding the extra backend.
- Deployment topology reporting is now a shared domain helper. Setup preview
  and the multi-step confirm screen both use
  `agmind.services.deployment_topology` for RAG storage lines, missing runtime
  dependency warnings, and compatibility warnings instead of assembling those
  signals independently. Operators can also inspect the same report with
  `agmind render topology --service ...` or `--profile ...`. Its JSON output
  now exposes structured warning records plus `warning_count`,
  `dependency_warning_count`, `compatibility_warning_count`, and retrieval
  provider fields for CI/operator automation. `--fail-on-warning` turns the
  same report into a CI gate by returning exit code `2` when topology warnings
  are present while still printing the text or JSON diagnostics.
- `scripts/topology_check.py` now validates standard profile lanes
  (`core`, `core,rag`, `core,observability`, `core,ragflow`, and
  `core,rag,ragflow`) through the same topology report. It is wired into
  aggregate governance, pre-commit, and self-hosted CI as the seventh M7
  governance check. Optional `dify_external_kb` gaps are informational so the
  normal Dify-only `core,rag` lane stays clean.
- Topology validation policy now lives in `agmind.services.topology_checks`;
  `scripts/topology_check.py` is only a thin entrypoint. The module exposes
  structured per-profile reports and JSON formatting for future CI/operator
  consumers.
- Topology signals are now split by severity. `DeploymentTopologyReport`
  carries `warnings` and non-blocking `infos` separately, and its JSON payload
  includes `info_count`, `compatibility_info_count`, `has_infos`, and the raw
  `infos` records. `scripts/topology_check.py --json` and
  `agmind render topology --json --fail-on-warning` therefore expose optional
  notes such as a missing `dify_external_kb` integration without failing the
  lane.
- Deploy target and Kubernetes proof workflow validation now have a shared
  severity-aware report model in `agmind.deploy.target_checks`.
  `validate_deploy_target_report()` and
  `validate_kubernetes_proof_workflow_report()` expose `error_count`,
  `warning_count`, `info_count`, stable issue `kind`, `target_id`, and JSON
  output, while the legacy validators still return plain error strings for
  existing call sites. `scripts/deploy_target_check.py --json` and
  `scripts/kubernetes_proof_workflow_check.py --json` are ready for CI/operator
  consumers that need machine-readable gate output.
- Structured validation now reaches the operator CLI layer. `agmind targets
  validate --json` emits the deployment target report directly, and
  `agmind governance validate --json` runs JSON-capable checks in structured
  mode so deploy-target, topology, Kubernetes render, and Kubernetes proof
  workflow results include parsed `payload` objects alongside stdout/stderr.
- Aggregate governance JSON is now fully structured across all seven checks.
  `scripts/component_check.py --json`, `scripts/tool_candidate_check.py --json`,
  and `scripts/constraints_check.py --json` expose count/error payloads too, so
  every `agmind governance validate --json` check has a non-null parsed
  `payload`.
- Aggregate governance JSON now includes a compact top-level `summary` with
  check/pass/fail counts, payload coverage, component/service/deploy/tool/
  constraint/topology/Kubernetes counters, Kubernetes warning severity totals,
  and `total_errors`. CI and operator UIs can read the common health summary
  without scraping stdout or walking every nested payload.
- The same governance summary now exposes `total_warnings` and `total_infos`.
  Warning totals intentionally count warning/blocker severity separately from
  informational notes, so CI dashboards can show non-fatal debt without
  confusing topology info records with Kubernetes render warnings.
- Text governance output now uses the structured payload path too, while
  preserving each gate's readable stdout. The final operator line includes the
  same health totals as JSON, for example `warnings=4, infos=1, errors=0`.
- Aggregate governance now invokes child check entrypoints with explicit empty
  argv for text runs and `("--json",)` for payload runs. This prevents an
  outer `scripts/governance_check.py --json` flag from leaking into child
  `main()` calls and turning captured readable stdout back into JSON.
- Aggregate governance JSON now names the affected gates in addition to
  reporting totals. The top-level summary includes `failed_checks`,
  `warning_checks`, and `info_checks`, currently resolving to no failed gates,
  `kubernetes-render` for warnings, and `topology` for informational notes.
- Aggregate governance JSON now also includes `check_health`: one compact row
  per gate with `name`, `ok`, `warnings`, `infos`, and `errors`. This gives
  dashboards a stable per-gate health table without traversing detailed nested
  payloads.
- Governance health summaries now include status labels. Top-level
  `health_status` and each `check_health[*].status` use `failed`, `warning`,
  `info`, or `ok` derived from error/warning/info counts; current local
  baseline is `warning` because Kubernetes render has four non-blocking
  warnings.
- Governance JSON summary now includes `status_counts` with the gate
  distribution by derived status. Current local baseline is
  `failed=0`, `warning=1`, `info=1`, and `ok=5`.
- Text governance output now includes the derived health status in the final
  line, for example `status=warning, warnings=4, infos=1, errors=0`, so CI logs
  and operator terminals expose the same headline state as JSON.
- Failure-path governance output now keeps that final health suffix even when a
  failed check has no structured payload, such as an unknown aggregate gate.
  Those failures still contribute `total_errors`, `failed_checks`,
  `status_counts.failed`, and a `check_health` row with `status=failed`.
- Aggregate governance now uses one effective pass/fail status for top-level
  `ok`, summary counts, per-check JSON, per-check health rows, and text output.
  A structured payload with `error_count > 0` fails the aggregate even if a
  child check incorrectly returns exit code `0`.
- `GovernanceCheckResult.ok` now uses that same effective status instead of
  only raw `returncode == 0`. Consumers that need process status must read
  `returncode`; consumers that need gate health can trust `ok` consistently
  across report objects, JSON, `check_health`, and text rendering.
- Structured governance payloads can no longer erase a process failure by
  omitting `error_count`. Any non-zero child return code contributes at least
  one aggregate error; a larger payload `error_count` still wins when present.
- JSON-capable governance checks now fail structured aggregate mode when their
  `--json` output is missing or invalid, even if the text run and JSON run both
  exit `0`. The aggregate stderr records `invalid structured JSON payload for
  <check>`, preserving the invariant that structured governance has real parsed
  payloads for machine consumers.
- Aggregate governance now exposes payload contract failures directly in JSON:
  each check carries `payload_error`, and the top-level summary includes
  `payload_error_count` plus `payload_error_checks`. Dashboards can classify
  malformed/missing structured output without scraping check stderr.
- Cluster detection now has a deploy-target inspection layer, not only mDNS
  peer discovery. `agmind cluster inspect` reports Docker/Compose, Kubernetes
  and k3s, Proxmox host/guest hints, LAN peers, warnings, and a recommended
  target id (`k3s`, `proxmox-vm-compose`, `ubuntu-compose`, or `unknown`).
  The report now enriches known recommendations from
  `templates/deploy-targets/*.yaml`, so JSON/text output includes the target
  name, status, runtime renderer, profiles, provisioner, storage profile, and
  secrets profile. External CLI probes return structured timeout/OS errors
  instead of aborting inspection.
- CI runner visibility now has a product-side operator command. `agmind ci
  status` uses the local `gh` CLI to inspect recent GitHub Actions runs and
  self-hosted runner state for `botAGI/AGmind64`, supports JSON/text output,
  detects the repo from `AGMIND_GITHUB_REPO` or `git remote.origin.url`, and
  reports missing auth/tooling as warnings instead of crashing.

M7.B deploy target checkpoint:

- `docs/adr/0014-deploy-targets-and-provisioning-boundary.md` records the
  deploy target/provisioning boundary.
- `agmind.deploy.targets` now provides `DeploymentTarget` models and a default
  loader for `templates/deploy-targets/`.
- Baseline deploy targets cover `ubuntu-compose` as supported,
  `proxmox-vm-compose` as experimental, and `k3s` as research.
- `scripts/export_schemas.py` now exports
  `templates/schemas/deploy-target.json`.
- Tests verify strict target validation, duplicate id rejection, repository
  baseline targets, and schema export.
- `infra/proxmox/vm-compose` now contains the first OpenTofu Proxmox root
  module skeleton. It pins `bpg/proxmox` to the current `~> 0.93.0` line,
  creates per-node cloud-init snippets and VMs, ignores local state/secrets,
  and exposes `agmind_hosts` plus `ansible_inventory` outputs for the next
  inventory bridge.
- `agmind.deploy.proxmox_inventory` and `scripts/proxmox_inventory.py` now
  convert `tofu output -json` into an Ansible inventory with `agmind_nodes`,
  `agmind_master`, and `agmind_workers`. Generated inventories are ignored via
  `ansible/inventory/*.generated.yml`.
- `agmind.deploy.target_checks` and `agmind targets list/status/validate` now
  expose deployment target contracts to operators and CI. The target validator
  enforces local repository references for supported/experimental lanes, which
  caught and corrected stale `ansible/playbooks/site.yml` references to the
  actual `ansible/install.yml` playbook.
- `scripts/deploy_target_check.py` is now the CI/pre-commit wrapper for the
  same deploy target gate. The self-hosted workflow validates
  `templates/deploy-targets/*.yaml` against `templates/schemas/deploy-target.json`
  and runs a dedicated `deploy-target-validate` job before CPU tests and compose
  validation.
- `agmind.addons` now provides `ToolCandidate` models and a loader for
  `templates/tool-candidates/`. Baseline candidates cover ComfyUI, n8n,
  Keycloak, SOPS/age, Vault, Infisical, Harbor, restic/Kopia, and Proxmox
  exporter. `scripts/tool_candidate_check.py` validates deployment target
  references and is wired into pre-commit.
- `proxmox-exporter` is the first candidate promoted into an opt-in runtime
  descriptor. It is owned by `observability-stack`, uses only the `proxmox`
  compose profile, pins `prompve/prometheus-pve-exporter:3.9.0` by digest, and
  ships token/scrape examples without tracked secrets.
- The services Ansible role now guards the `proxmox` profile before compose
  render/up: operators must either provide
  `agmind_proxmox_exporter_{user,token_name,token_value}` or declare an
  externally managed `/etc/agmind/proxmox-exporter/pve.yml`. The generated
  token config is rendered with `no_log: true` and restrictive permissions.
- `agmind.deploy.proxmox_exporter` and `scripts/proxmox_exporter_check.py` now
  validate `pve.yml` token auth without echoing secrets. Ansible invokes the
  validator before compose render/up, and the same command can optionally probe
  a running exporter endpoint with `/pve?module=default&target=...`.
- `scripts/tool_candidate_check.py` now enforces accepted service-profile
  admission against runtime state: service descriptor existence, exactly-one
  component owner, digest pin, descriptor profiles, and declared ports.
- The admission rule now lives in `agmind.addons.checks`, so CI scripts and
  operator commands share one source of truth. `agmind tools list/status/validate`
  exposes the optional homelab/enterprise catalog, accepted status, profiles,
  ports, risks, verification commands, and admission errors without reading YAML
  by hand.
- The self-hosted workflow now validates `templates/tool-candidates/*.yaml`
  against `templates/schemas/tool-candidate.json` and runs a dedicated
  `tool-candidate-validate` job before CPU tests and compose validation. The
  local pre-commit hook also watches candidate schema, services, components,
  deploy targets, and admission code so accepted-candidate drift is caught early.
- `agmind.governance`, `scripts/governance_check.py`, and
  `agmind governance validate` now provide a single aggregate M7 gate over
  component contracts, deployment targets, optional tool candidates, and
  dependency constraints. It does not replace individual CI jobs; it gives
  operators and local development one concise command for the full governance
  layer.
- The aggregate governance gate is now wired into pre-commit for aggregate
  wrapper changes and into the self-hosted CI as `governance-validate`, which
  depends on component, deploy-target, tool-candidate, and constraints jobs and
  emits the combined report after those focused gates run.

M7.D.1 Kubernetes renderer MVP checkpoint:

- `agmind.services.kubernetes_renderer` now renders a research-grade plain
  Kubernetes manifest stream from existing `ServiceDescriptor` objects without
  changing the Docker Compose renderer.
- `agmind render kubernetes --profile <profiles> --namespace <namespace>`
  exposes this path to operators. `--strict` fails if selected descriptors
  contain Docker-only fields that need a real Kubernetes mapping.
- The renderer emits Namespace, Deployment, and ClusterIP Service objects,
  carries digest-pinned images, env, args, ports, hostPath mounts, resource
  limits/requests, and simple HTTP health probes where safely mappable.
- Docker-only fields such as `devices`, `group_add`, `security_opt`, `cap_add`,
  Docker socket mounts, and unresolved `${...}` env interpolation become
  explicit warnings. This is the current bridge between Compose descriptors and
  a future real k3s/RKE2/Talos renderer.
- `templates/deploy-targets/k3s.yaml` now points at `agmind render kubernetes`
  instead of the old `future-kubernetes-renderer` placeholder, while remaining
  a `research` target until real cluster dry-run/apply evidence exists.
- Local verification for M7.D.1 passed on 2026-05-24: focused
  renderer/CLI/target tests (10), target/governance slice (33), expanded
  M7-focused pytest (142), focused mypy, deploy target check, aggregate
  governance check, `git diff --check`, and full pre-commit.

M7.D.2 Kubernetes render governance checkpoint:

- `agmind.services.kubernetes_checks` now validates Kubernetes deployment
  targets by running the local `agmind render kubernetes` path against each
  target's declared profiles.
- `scripts/kubernetes_render_check.py` exposes the check for operators,
  pre-commit, CI, and aggregate governance. Default mode treats portability
  warnings as visible non-fatal research debt; `--strict` fails while current
  Docker-only fields remain unmapped.
- `agmind governance validate` now includes Kubernetes render validation in the
  aggregate governance path. The current M7 aggregate has grown to seven
  checks: component contracts, deployment targets, optional tool candidates,
  dependency constraints, topology, Kubernetes render validation, and
  Kubernetes proof workflow drift.
- Pre-commit now watches Kubernetes renderer/check/target/service drift, and
  self-hosted CI has a dedicated `kubernetes-render-validate` job before the
  aggregate `governance-validate` summary.
- Local check output currently renders the `k3s` target into 38 Kubernetes
  objects: 23 Deployments and 14 Services, with 27 portability warnings.
- Local verification for M7.D.2 passed on 2026-05-24: RED failures were
  observed, the governance slice passed 15 tests, expanded M7-focused pytest
  passed 150 tests, focused ruff/format/mypy passed, and
  `scripts/kubernetes_render_check.py` passed. Final governance, `git diff
  --check`, and full `pre-commit --all-files` also passed after documentation
  updates.

M7.D.3 Kubernetes portability policy checkpoint:

- `KubernetesRenderWarning` now carries stable `code`, `severity`, `message`,
  and `remediation` fields. YAML warning comments preserve human readability
  and include `[code/severity]` markers.
- Current warning codes cover `docker-device`, `docker-group-add`,
  `docker-security-opt`, `linux-capability`, `env-interpolation`, and
  `docker-socket`.
- Kubernetes render reports now include `warning_summary` for each target and
  at aggregate JSON level.
- Current `k3s` research render still produces 27 warnings, now classified as
  5 blockers and 22 warnings. Default render governance remains non-fatal for
  research targets; `--strict` still fails on any warning.
- Local verification for M7.D.3 passed on 2026-05-24: RED failures were
  observed, focused Kubernetes renderer/check tests passed 17 tests, focused
  ruff/format/mypy passed, and `scripts/kubernetes_render_check.py --json`
  reported the structured severity summary. Final governance, expanded
  M7-focused pytest (151), `git diff --check`, and full pre-commit also passed.

M7.D.4 Kubernetes promotion policy checkpoint:

- Kubernetes render validation now treats blocker warnings as compatible only
  with `research` targets. Equivalent `experimental` or `supported`
  Kubernetes targets fail default validation while blockers remain.
- `--strict` remains stronger than target status: it fails on any warning for
  any Kubernetes target.
- Current `k3s` stays `research`, so the local render gate still passes while
  exposing 5 blockers and 22 warning-level items.
- Local verification for M7.D.4 passed on 2026-05-24: RED failure was observed
  for an `experimental` target with blockers, focused Kubernetes render check
  tests passed 10 tests, focused ruff/format/mypy passed, default
  `scripts/kubernetes_render_check.py` passed, and `--strict` failed as
  expected while warnings remain. Final governance, expanded M7-focused pytest
  (153), `git diff --check`, and full pre-commit also passed.

M7.D.5 Kubernetes remediation report checkpoint:

- Kubernetes render validation now preserves renderer warning metadata instead
  of reparsing YAML comments. Each target JSON report includes warning records
  with `service`, `code`, `severity`, `message`, and `remediation`.
- Human-readable render reports now include a compact blocker-code breakdown,
  for example `docker-device=...`, so operators can see the first remediation
  buckets before running real cluster validation.
- This remains reporting/governance only: target status, manifest output, and
  promotion policy are unchanged.
- Local focused verification for M7.D.5 passed on 2026-05-24:
  `tests/test_kubernetes_render_check.py` passed 10 tests after RED failures
  were observed for missing JSON `warnings` and text `blockers:` output.
- Local final verification for M7.D.5 passed on 2026-05-24: focused ruff,
  format check, and mypy passed; `scripts/kubernetes_render_check.py` reports
  38 objects, 27 warnings, and blocker buckets `docker-device=3,
  docker-socket=2`; expanded M7-focused pytest passed 153 tests;
  `scripts/governance_check.py` passed 5 checks; `git diff --check` and full
  `pre-commit --all-files` passed.

M7.D.6 Kubernetes server dry-run harness checkpoint:

- `agmind.services.kubernetes_dry_run` now renders Kubernetes deployment
  targets and runs `kubectl apply --dry-run=server -f <manifest>` through an
  injectable runner. Reports distinguish `passed`, `failed`, and `skipped`.
- `scripts/kubernetes_dry_run.py` exposes text and JSON evidence. Default mode
  treats missing kubectl/cluster access as `SKIPPED` so local development and
  non-cluster CI remain honest; `--require-cluster` makes skipped evidence a
  failing gate for real k3s proof runs.
- `templates/deploy-targets/k3s.yaml` now records the proof harness in its
  verification commands after the raw manifest render command.
- Current local host evidence is intentionally still not real cluster proof:
  default script output is `SKIPPED` because `kubectl` is not available in this
  environment, and `--require-cluster --kubectl /definitely/missing/kubectl`
  exits 1 as expected.
- Local final verification for M7.D.6 passed on 2026-05-24: focused dry-run
  and deploy target tests passed 16 tests; focused ruff, format check, and
  mypy passed; `scripts/kubernetes_dry_run.py --json` reported `skipped`
  evidence without kubectl; deploy target and aggregate governance checks
  passed; expanded M7-focused pytest passed 159 tests; `git diff --check` and
  full `pre-commit --all-files` passed.

M7.D.7 Kubernetes dry-run artifact bundle checkpoint:

- `scripts/kubernetes_dry_run.py --artifact-dir <dir>` now writes the rendered
  manifest as `<target>.yaml`, the per-target report as
  `<target>.dry-run.json`, and aggregate evidence as `summary.json`.
- JSON output now includes `manifest_path` and `report_path` so CI logs can
  point reviewers to the persisted proof files.
- Artifact mode renders and writes the manifest before checking kubectl
  availability, so local skipped runs still preserve the exact YAML that a real
  `kubectl apply --dry-run=server` proof run would submit.
- Local final verification for M7.D.7 passed on 2026-05-24: focused dry-run
  and deploy target tests passed 18 tests; sequential artifact smoke wrote
  `k3s.yaml`, `k3s.dry-run.json`, and `summary.json`; focused ruff, format
  check, and mypy passed; aggregate governance passed 5 checks; expanded
  M7-focused pytest passed 161 tests; `git diff --check` and full
  `pre-commit --all-files` passed.

M7.D.8 Traefik Kubernetes provider remediation checkpoint:

- The Kubernetes renderer now treats Traefik's Docker socket mount as
  replaceable in Kubernetes renders. It omits `/var/run/docker.sock` from the
  Traefik Deployment and rewrites Docker provider CLI args to
  `--providers.kubernetesingress=true`.
- This is intentionally scoped to Traefik. Portainer still reports a
  `docker-socket` blocker because it has no safe Kubernetes-native replacement
  in the current service catalog.
- Current local k3s render output now reports 26 warnings total:
  22 warnings and 4 blockers. Blocker buckets are `docker-device=3` and
  `docker-socket=1`.
- Local final verification for M7.D.8 passed on 2026-05-24: focused Kubernetes
  renderer/check tests passed 20 tests; focused ruff, format check, and mypy
  passed; `scripts/kubernetes_render_check.py` reported 26 warnings and 4
  blockers; aggregate governance passed 5 checks; expanded M7-focused pytest
  passed 162 tests; `git diff --check` and full `pre-commit --all-files`
  passed.

M7.D.9 Portainer Kubernetes omission checkpoint:

- The Kubernetes renderer now omits the current Docker-socket-based Portainer
  descriptor from Kubernetes objects instead of rendering a misleading
  Deployment/Service. It emits `kubernetes-omitted` as a warning so the missing
  Kubernetes workload stays visible in reports.
- Compose behavior is unchanged: the `templates/services/portainer.yaml`
  descriptor still exists for the Docker Compose observability lane.
- Current local k3s render output now reports 36 objects: 22 Deployments and
  13 Services plus Namespace. Warning summary is 23 warnings and 3 blockers.
  All remaining blockers are `docker-device` for llama LLM/embed/rerank.
- Local verification for M7.D.9 passed on 2026-05-24: focused Kubernetes
  renderer/check tests passed 21 tests; focused ruff format check, ruff check,
  and mypy passed; `scripts/kubernetes_render_check.py` reported 36 objects,
  26 warnings, and 3 blockers with blocker bucket `docker-device=3`; aggregate
  governance passed 5 checks; expanded M7-focused pytest passed 163 tests;
  `git diff --check` and full `pre-commit --all-files` passed after the
  planning update.

M7.D.10 AMD GPU Kubernetes device mapping checkpoint:

- The Kubernetes renderer now maps the known AGmind inference device
  `devices: ["/dev/dri"]` to container resource requests/limits
  `amd.com/gpu: "1"` instead of treating it as an unmapped Docker-only device.
- Each mapped llama service emits `amd-gpu-device-plugin` as a warning, not a
  blocker. The warning makes the cluster prerequisite explicit: install the AMD
  GPU device plugin or GPU Operator and verify allocatable `amd.com/gpu` before
  real promotion.
- Unknown Docker devices remain blocker warnings. The promotion policy test now
  uses a temporary `/dev/custom0` descriptor so the blocker rule remains covered
  even though the repository baseline has no blockers.
- Current local k3s render output now reports 36 objects: 22 Deployments and
  13 Services plus Namespace. Warning summary is 26 warnings and 0 blockers.
- Local verification for M7.D.10 passed on 2026-05-24: focused Kubernetes
  renderer/check tests passed 23 tests; focused ruff format check, ruff check,
  and mypy passed; `scripts/kubernetes_render_check.py` reported 36 objects,
  26 warnings, and 0 blockers; aggregate governance passed 5 checks; expanded
  M7-focused pytest passed 165 tests; `git diff --check` and full
  `pre-commit --all-files` passed.

M7.D.11 Kubernetes AMD GPU proof checkpoint:

- `scripts/kubernetes_dry_run.py` now supports `--require-amd-gpu`. When set,
  the proof harness runs `kubectl get nodes -o json` before server-side apply,
  sums `status.allocatable["amd.com/gpu"]`, and records that evidence in text,
  JSON, per-target artifacts, and `summary.json`.
- A real cluster with zero allocatable `amd.com/gpu` now fails the target proof.
  Missing kubectl or cluster access remains `skipped` unless `--require-cluster`
  is also used, preserving safe local development behavior.
- `templates/deploy-targets/k3s.yaml` now advertises the real proof command:
  `scripts/kubernetes_dry_run.py --require-cluster --require-amd-gpu`.
- Local focused verification for M7.D.11 passed on 2026-05-24: dry-run and
  deploy-target tests passed 22 tests; local no-kubectl smoke with
  `--require-amd-gpu --kubectl /definitely/missing/kubectl` emitted skipped
  `gpu_preflight` JSON; focused ruff format check, ruff check, and mypy passed;
  `scripts/kubernetes_render_check.py` reported 36 objects, 26 warnings, and 0
  blockers; aggregate governance passed 5 checks; expanded M7-focused pytest
  passed 169 tests; `git diff --check` and full `pre-commit --all-files`
  passed.

M7.D.12 Kubernetes default interpolation checkpoint:

- The Kubernetes renderer now resolves explicit non-empty descriptor defaults
  such as `${VAR:-default}` without reading host environment values. Nested
  descriptor defaults such as `${CTX:-${BASE_CTX:-8192}}` resolve inside the
  rendered Kubernetes manifest.
- Rendered command arguments reuse those resolved descriptor env values, so
  model paths and numeric settings with safe defaults become concrete
  Kubernetes args.
- Secrets, no-default placeholders, and empty defaults remain unresolved and
  visible as warning-level debt. Unresolved command placeholders now emit
  `command-interpolation` warnings with remediation metadata.
- Current local k3s render output now reports 36 objects: 22 Deployments and
  13 Services plus Namespace. Warning summary is 15 warnings and 0 blockers.
- Local verification for M7.D.12 passed on 2026-05-24: RED failures were
  observed for unresolved default interpolation and the old 26-warning
  baseline; after implementation, focused Kubernetes renderer/check tests
  passed 25 tests; focused ruff format check, ruff check, and mypy passed;
  `scripts/kubernetes_render_check.py` reported 36 objects, 15 warnings, and
  0 blockers; aggregate governance passed 5 checks; expanded M7-focused pytest
  passed 171 tests; `git diff --check` and full `pre-commit --all-files`
  passed.

M7.D.13 Kubernetes Secret env refs checkpoint:

- The Kubernetes renderer now maps unresolved secret-like descriptor env values
  to deterministic `valueFrom.secretKeyRef` entries instead of raw `${...}`
  strings. Secret refs use `agmind-<service>-env` as the Secret name.
- Pure secret placeholders such as `${POSTGRES_PASSWORD}` use the placeholder
  token as the Secret key. Embedded secret strings such as a Postgres exporter
  DSN use the env var name as the Secret key because Kubernetes cannot
  concatenate literal env fragments with a Secret key in one env value.
- Docker Compose behavior and service descriptors are unchanged. The renderer
  does not create Secret objects yet; operators still need External Secrets,
  SOPS, or manually managed Secrets before runtime promotion.
- Current local k3s render output now reports 36 objects: 22 Deployments and
  13 Services plus Namespace. Warning summary is 11 warnings and 0 blockers.
- Local verification for M7.D.13 passed on 2026-05-24: RED failures were
  observed for raw `${POSTGRES_PASSWORD}` env rendering and the old 15-warning
  baseline; after implementation, focused Kubernetes renderer/check tests
  passed 26 tests; focused ruff format check, ruff check, and mypy passed;
  `scripts/kubernetes_render_check.py` reported 36 objects, 11 warnings, and
  0 blockers; aggregate governance passed 5 checks; expanded M7-focused pytest
  passed 172 tests; `git diff --check` and full `pre-commit --all-files`
  passed.

M7.D.14 Kubernetes securityContext checkpoint:

- The Kubernetes renderer now maps supported Docker security fields to explicit
  Kubernetes security contexts without changing Compose descriptors.
- `security_opt: ["seccomp=unconfined"]` renders as container
  `securityContext.seccompProfile.type: Unconfined`.
- `cap_add` renders as container `securityContext.capabilities.add`.
- Numeric `group_add` values render as pod `securityContext.supplementalGroups`.
  Named groups such as `video` and `render` remain warning debt because
  Kubernetes expects numeric group IDs and target-specific GID policy is not
  available in service descriptors.
- Current local k3s render output now reports 36 objects: 22 Deployments and
  13 Services plus Namespace. Warning summary is 10 warnings and 0 blockers.
- Local focused verification for M7.D.14 passed on 2026-05-24: RED failures
  were observed for missing pod/container `securityContext` output and the old
  11-warning baseline; after implementation, focused Kubernetes renderer/check
  tests passed 28 tests; focused ruff format check, ruff check, and mypy
  passed; `scripts/kubernetes_render_check.py` reported 36 objects, 10
  warnings, and 0 blockers; aggregate governance passed 5 checks; expanded
  M7-focused pytest passed 174 tests; `git diff --check` and full
  `pre-commit --all-files` passed.

M7.D.15 Kubernetes AMD GPU group policy checkpoint:

- The Kubernetes renderer now treats Docker `group_add: ["video", "render"]`
  as covered when the same descriptor maps `/dev/dri` to the Kubernetes
  extended resource `amd.com/gpu`.
- The `amd-gpu-device-plugin` warning remains the single cluster prerequisite
  for those inference workloads: install/verify the device plugin or GPU
  Operator and confirm allocatable `amd.com/gpu`.
- Named GPU groups without `/dev/dri`, unrelated named groups, and unknown
  devices still emit portability warnings. Numeric `group_add` values still
  render as pod `securityContext.supplementalGroups`.
- Current local k3s render output now reports 36 objects: 22 Deployments and
  13 Services plus Namespace. Warning summary is 7 warnings and 0 blockers.
- Local focused verification for M7.D.15 passed on 2026-05-24: RED failures
  were observed for duplicate `docker-group-add` warnings and the old
  10-warning baseline; after implementation, focused Kubernetes renderer/check
  tests passed 29 tests and `scripts/kubernetes_render_check.py` reported 36
  objects, 7 warnings, and 0 blockers; aggregate governance passed 5 checks;
  expanded M7-focused pytest passed 175 tests; `git diff --check` and full
  `pre-commit --all-files` passed.

M7.D.16 Kubernetes empty env defaults checkpoint:

- The Kubernetes renderer now resolves explicit empty env defaults such as
  `${AGMIND_ROPE_SCALING:-}` and `${AGMIND_RERANK_FILE:-}` to `value: ""`.
- Empty defaults are enabled only for environment variable rendering. Command
  interpolation still requires a non-empty value, so
  `/models/${AGMIND_RERANK_FILE}` remains a `command-interpolation` warning.
- Secret refs, non-empty descriptor defaults, AMD GPU resource mapping, and
  securityContext mappings are unchanged.
- Current local k3s render output now reports 36 objects: 22 Deployments and
  13 Services plus Namespace. Warning summary is 5 warnings and 0 blockers.
- Local verification for M7.D.16 passed on 2026-05-24: RED failures were
  observed for raw empty-default env placeholders and the old 7-warning
  baseline; after implementation, focused ruff format check, ruff check, and
  mypy passed; focused Kubernetes renderer/check tests passed 30 tests;
  `scripts/kubernetes_render_check.py` reported 36 objects, 5 warnings, and
  0 blockers; aggregate governance passed 5 checks; expanded M7-focused pytest
  passed 176 tests; `git diff --check` and full `pre-commit --all-files`
  passed.

M7.D.17 Kubernetes warning policy checkpoint:

- Deployment target verification contracts now support
  `expected_warning_codes` for target-declared Kubernetes render debts.
- The `k3s` research target declares `amd-gpu-device-plugin` as expected
  cluster-prerequisite debt and `kubernetes-omitted` as expected Compose-only
  omission debt.
- Kubernetes render strict mode now rejects only unexpected warnings. The
  current strict check therefore fails on the single remaining unexpected
  warning: `command-interpolation=1` for the rerank model path.
- Default non-strict governance remains unchanged: current local k3s render
  output reports 36 objects, 22 Deployments, 13 Services, 5 warnings, and
  0 blockers.
- Local focused verification for M7.D.17 passed on 2026-05-24: RED failures
  were observed for missing `expected_warning_codes` support and the old
  all-warning strict behavior; after implementation, focused deploy-target and
  Kubernetes render-check tests passed 22 tests; focused ruff format check,
  ruff check, and mypy passed; normal Kubernetes render check passed; strict
  Kubernetes render check failed only on `command-interpolation=1`; aggregate
  governance passed 5 checks; expanded M7-focused pytest passed 178 tests;
  `git diff --check` and full `pre-commit --all-files` passed.

M7.D.18 Kubernetes rerank omission checkpoint:

- The Kubernetes renderer now omits `llama-rerank` when
  `AGMIND_RERANK_FILE` resolves to an explicit empty value. This mirrors the
  installer and wizard policy: empty rerank file means reranking is disabled
  rather than started with an invalid model path.
- The omission emits a `kubernetes-omitted` warning with rerank-specific
  remediation. Generic unresolved command interpolation is still preserved for
  other services and remains test-covered.
- Current local k3s render output now reports 34 objects: 21 Deployments and
  12 Services plus Namespace. Warning summary is 4 warnings and 0 blockers.
- `scripts/kubernetes_render_check.py --strict` now passes locally because the
  remaining warnings are target-declared expected warning codes:
  `amd-gpu-device-plugin` for LLM/embed GPU resources and `kubernetes-omitted`
  for unconfigured rerank plus Portainer.
- Local focused verification for M7.D.18 passed on 2026-05-24: RED failures
  were observed for rendered unconfigured rerank, the old 36-object/5-warning
  baseline, and strict `command-interpolation=1`; after implementation,
  focused Kubernetes renderer/check tests passed 32 tests; focused ruff format
  check, ruff check, and mypy passed; normal and strict Kubernetes render
  checks both reported 34 objects, 4 warnings, and 0 blockers; aggregate
  governance passed 5 checks; expanded M7-focused pytest passed 179 tests;
  `git diff --check` and full `pre-commit --all-files` passed.

M7.D.19 Kubernetes dry-run metadata checkpoint:

- Real k3s proof is still external in this environment: `kubectl` and
  `~/.kube` are unavailable, so `--require-cluster` dry-run evidence records a
  skipped local run instead of a server-side proof.
- Aggregate `summary.json` now records `require_cluster`, `require_amd_gpu`,
  `kubectl`, `kube_context`, `namespace`, `artifact_dir`, and `summary_path`.
  The evidence bundle is therefore self-describing when an operator reruns it
  on a real k3s host.
- Target-level JSON remains backward-compatible; manifest, per-target report,
  and aggregate summary artifacts are still written for skipped local runs.
- Local skipped smoke with `--require-cluster --require-amd-gpu --artifact-dir
  /tmp/agmind-k8s-dry-run-m7d19` writes metadata and fails only because
  `kubectl` is unavailable.
- Local focused verification for M7.D.19 passed on 2026-05-24: RED failures
  were observed for missing dry-run aggregate metadata; after implementation,
  focused Kubernetes dry-run tests passed 13 tests; focused ruff format check,
  ruff check, and mypy passed; the local skipped smoke wrote metadata;
  aggregate governance passed 5 checks; strict Kubernetes render-check passed;
  expanded M7-focused pytest passed 180 tests. Final diff/pre-commit evidence
  is recorded in this session after planning updates.

M7.D.20 Kubernetes dry-run target selection checkpoint:

- `scripts/kubernetes_dry_run.py` now accepts repeatable `--target <id>` flags
  so real proof runs can be scoped to `k3s` before future RKE2/Talos targets
  exist in the same repository.
- `run_kubernetes_server_dry_run(..., target_ids=(...))` validates unknown
  target ids before rendering and rejects non-Kubernetes targets instead of
  silently producing an empty proof.
- Aggregate `summary.json` now records effective `target_ids`. With
  `--target k3s --artifact-dir <dir>`, only `k3s.yaml`,
  `k3s.dry-run.json`, and `summary.json` are written for that run.
- The `k3s` deployment target verification contract now calls
  `scripts/kubernetes_dry_run.py --target k3s --require-cluster
  --require-amd-gpu`, so the external proof command is explicit and stable as
  more Kubernetes targets are added.
- Local focused verification for M7.D.20 passed on 2026-05-24: RED failures
  were observed for the missing `target_ids` API, missing CLI `--target`, and
  the old k3s verification command; after implementation, focused dry-run plus
  deploy-target contract tests passed 18 tests, `scripts/deploy_target_check.py`
  passed, and CLI smokes for `--target k3s` and unknown target behavior matched
  expectations. Final aggregate verification is recorded in this session after
  planning updates.

M7.D.21 Kubernetes dry-run warning details checkpoint:

- Dry-run target reports now include actionable render warning records, not
  only `warning_summary`.
- Each warning record includes `service`, `code`, `severity`, `message`,
  `remediation`, and `expected`. `expected` is derived from the deployment
  target's `verification.expected_warning_codes`.
- Local skipped k3s proof bundles therefore show the current four warning
  records inline: two `amd-gpu-device-plugin` prerequisites and two
  `kubernetes-omitted` records for unconfigured rerank plus Portainer. All are
  currently expected for the research `k3s` target.
- The target-level report and aggregate `summary.json` carry the same warning
  records, so reviewers no longer need a separate render-check JSON artifact
  just to see remediation text.
- Local focused verification for M7.D.21 passed on 2026-05-24: RED failures
  were observed for missing dry-run `warnings` fields; after implementation,
  focused Kubernetes dry-run tests passed 18 tests, focused ruff format check,
  ruff check, and mypy passed, and a skipped `--target k3s --artifact-dir`
  smoke wrote four warning records with `expected=true`. Final aggregate
  verification is recorded in this session after planning updates.

M7.D.22 Kubernetes dry-run artifact checksums checkpoint:

- Dry-run target reports now include `manifest_bytes` and `manifest_sha256`
  whenever a rendered manifest artifact is written.
- Aggregate `summary.json` now records `checksum_path` when `--artifact-dir`
  is used.
- The harness writes `checksums.txt` after `summary.json`, with SHA256 lines
  for persisted evidence files: rendered manifests, per-target dry-run JSON
  reports, and `summary.json`. The checksum file intentionally does not include
  a checksum line for itself.
- Local skipped k3s proof bundles are now self-describing, target-scoped,
  warning-rich, and integrity-checkable before the external real-cluster proof.
- Local focused verification for M7.D.22 passed on 2026-05-24: RED failure was
  observed for missing checksum artifacts; after implementation, focused
  Kubernetes dry-run tests passed 19 tests, focused ruff format check, ruff
  check, and mypy passed, and a skipped `--target k3s --artifact-dir` smoke
  wrote `k3s.yaml`, `k3s.dry-run.json`, `summary.json`, and `checksums.txt`.
  Final aggregate verification is recorded in this session after planning
  updates.

M7.D.23 Kubernetes dry-run artifact verifier checkpoint:

- `scripts/kubernetes_dry_run.py --verify-artifact-dir <dir>` now verifies an
  existing evidence bundle instead of running a new dry-run.
- The verifier reads `summary.json` and `checksums.txt`, checks persisted file
  existence and SHA256 digests, and cross-checks target
  `manifest_bytes`/`manifest_sha256` metadata against the manifest artifact.
- Verification resolves artifact basenames relative to the supplied directory,
  so copied or CI-uploaded bundles can be checked without relying on stale
  absolute paths inside JSON.
- Text mode prints `kubernetes dry-run artifact bundle OK: <n> files` or a
  compact FAILED report; `--json --verify-artifact-dir` returns machine-readable
  per-file verification status and errors.
- Local focused verification for M7.D.23 passed on 2026-05-24: RED failures
  were observed for missing verifier API and CLI flag; after implementation,
  focused Kubernetes dry-run tests passed 23 tests, focused ruff format check,
  ruff check, and mypy passed, verifier smoke accepted a generated bundle, and
  corruption smoke rejected a modified manifest with checksum and manifest
  metadata errors.
- Final M7.D.23 verification passed on 2026-05-24: governance check, strict
  Kubernetes render check, expanded 190-test M7 pytest set, `git diff --check`,
  and full `pre-commit --all-files --show-diff-on-failure`.

M7.D.24.A Kubernetes proof artifact contract checkpoint:

- The `k3s` deployment target now declares a real proof bundle path:
  `local-kubernetes-proof/k3s`. This path is covered by the existing
  `local-*` gitignore rule, so real cluster proof output stays local unless
  intentionally copied or uploaded.
- The `k3s` `scripts/kubernetes_dry_run.py --target k3s --require-cluster
  --require-amd-gpu` command now includes `--artifact-dir
  local-kubernetes-proof/k3s`, followed by a matching
  `scripts/kubernetes_dry_run.py --verify-artifact-dir
  local-kubernetes-proof/k3s` command.
- `verification.artifacts` now declares the expected bundle files:
  `k3s.yaml`, `k3s.dry-run.json`, `proof-command.txt`, `summary.json`, and
  `checksums.txt` under the proof artifact directory.
- Deploy-target validation now rejects Kubernetes proof commands that use
  `--require-cluster` without `--target <id>`, without `--artifact-dir`,
  without the matching bundle verifier command, or without the expected
  artifact declarations.
- Local focused verification for M7.D.24.A passed on 2026-05-24:
  `tests/test_deploy_targets.py` passed 15 tests after RED failures were
  observed for the missing contract and validator rules.
- Final M7.D.24.A verification passed on 2026-05-24: focused ruff format
  check, ruff check, mypy, deploy-target check, aggregate governance check,
  expanded 194-test M7 pytest set, `git diff --check`, and full
  `pre-commit --all-files --show-diff-on-failure`.

M7.D.24.B-prep Kubernetes proof CI artifact workflow checkpoint:

- Added `.github/workflows/kubernetes-proof.yml`, a manual-only
  `workflow_dispatch` workflow for a kubeconfig-equipped self-hosted runner
  labeled `[self-hosted, linux, x64, k3s]`.
- The workflow follows the normal self-hosted install pattern with
  `$HOME/.local/bin/uv`, and intentionally does not use `actions/setup-python`.
- Before live proof, it runs `.venv/bin/python scripts/kubernetes_render_check.py
  --strict`.
- The proof step writes `local-kubernetes-proof/k3s` with the same contract
  recorded in `templates/deploy-targets/k3s.yaml`: `--target k3s`,
  `--require-cluster`, `--require-amd-gpu`, and `--artifact-dir
  local-kubernetes-proof/k3s`.
- The workflow then runs `.venv/bin/python scripts/kubernetes_dry_run.py
  --verify-artifact-dir local-kubernetes-proof/k3s` and uploads
  `k3s.yaml`, `k3s.dry-run.json`, `proof-command.txt`, `summary.json`, and
  `checksums.txt` as the `kubernetes-proof-k3s` artifact.
- Local focused verification for M7.D.24.B-prep passed on 2026-05-24: RED
  failure was observed for the missing workflow, then the focused workflow test
  passed and `tests/test_kubernetes_dry_run.py` passed 24 tests.
- Final M7.D.24.B-prep verification passed on 2026-05-24: focused ruff format
  check, ruff check, aggregate governance check, expanded 195-test M7 pytest
  set, `git diff --check`, and full
  `pre-commit --all-files --show-diff-on-failure`.

M7.D.24.C-prep Kubernetes proof workflow drift guard checkpoint:

- Added `scripts/kubernetes_proof_workflow_check.py`, which validates that the
  manual `.github/workflows/kubernetes-proof.yml` workflow stays aligned with
  the `k3s` deployment target proof contract.
- The validator checks that the workflow is `workflow_dispatch` only, runs on
  `[self-hosted, linux, x64, k3s]`, avoids `actions/setup-python`, runs strict
  Kubernetes render validation, includes the target-declared proof target and
  artifact directory, runs the matching `--verify-artifact-dir`, keeps that
  verifier step guarded with `if: always()`, and uploads every
  `verification.artifacts` path.
- Aggregate governance now includes `kubernetes-proof-workflow`, so the local
  governance report has six checks. The guard is also wired into pre-commit and
  the self-hosted CI as `kubernetes-proof-workflow-validate`.
- Local focused verification for M7.D.24.C-prep passed on 2026-05-24: RED
  failures were observed for the missing script and missing governance/CI
  wiring; after implementation, focused tests passed 7 tests, the workflow
  script reported `kubernetes proof workflow OK: 1 targets`, focused ruff
  checks passed, and mypy passed for the touched governance modules.
- Final M7.D.24.C-prep verification passed on 2026-05-24: focused
  workflow/governance tests passed 9 tests, `scripts/kubernetes_proof_workflow_check.py`
  passed, aggregate governance passed with 6 checks, the expanded M7 pytest set
  passed 196 tests, `git diff --check` passed, and full
  `pre-commit --all-files --show-diff-on-failure` passed.

M7.D.24.D-prep Kubernetes proof command artifact checkpoint:

- `scripts/kubernetes_dry_run.py --artifact-dir <dir>` now writes
  `proof-command.txt` with the shell command that reproduces the proof bundle.
- `summary.json` records both `proof_command` and `proof_command_path`, while
  `checksums.txt` includes `proof-command.txt`, so copied or uploaded bundles
  fail verification if the operator-facing command artifact is missing or
  corrupted.
- The `k3s` deploy-target contract and manual `kubernetes-proof` workflow now
  declare/upload `local-kubernetes-proof/k3s/proof-command.txt` alongside the
  manifest, per-target report, summary, and checksums.
- Local focused verification for M7.D.24.D-prep passed on 2026-05-24: RED
  failures were observed for the missing command artifact and missing target
  declaration; after implementation, the new artifact/verifier tests passed 4
  tests and deploy-target/workflow checks passed 17 tests.
- Final M7.D.24.D-prep verification passed on 2026-05-24: full
  `tests/test_kubernetes_dry_run.py` passed 27 tests, focused
  governance/contract tests passed 50 tests, focused ruff format/check passed,
  mypy passed for the touched Kubernetes proof modules,
  `scripts/kubernetes_proof_workflow_check.py` passed, aggregate governance
  passed with 6 checks, a CLI smoke generated and verified a proof bundle with
  checksum-covered `proof-command.txt`, the expanded M7 pytest set passed 170
  tests, `git diff --check` passed, and full
  `pre-commit --all-files --show-diff-on-failure` passed.

M7.D.24.E-prep Kubernetes proof always-verify workflow checkpoint:

- `.github/workflows/kubernetes-proof.yml` now runs the `Verify k3s proof
  bundle` step with `if: always()`, so a failed live server-side dry-run still
  attempts local bundle integrity verification before upload.
- `validate_kubernetes_proof_workflow()` now rejects workflows where the
  verifier command exists but is not in a step guarded with `if: always()`.
- Local focused verification for M7.D.24.E-prep passed on 2026-05-24: RED
  failures were observed for the missing workflow guard and missing validator
  rule; after implementation, the focused workflow/validator tests passed 3
  tests.
- Final M7.D.24.E-prep verification passed on 2026-05-24: focused
  workflow/validator/governance tests passed 4 tests, focused ruff
  format/check passed, mypy passed for `agmind/deploy/target_checks.py`,
  `scripts/kubernetes_proof_workflow_check.py` passed, aggregate governance
  passed with 6 checks, and the expanded M7 pytest set passed 171 tests.

M7.D.24.F-prep Kubernetes proof bundle diagnostics checkpoint:

- `.github/workflows/kubernetes-proof.yml` now has an always-run `Summarize k3s
  proof bundle` step between verification and upload. It lists files in
  `local-kubernetes-proof/k3s` and prints `checksums.txt` when present, while
  treating a missing bundle directory as an informational diagnostic.
- `validate_kubernetes_proof_workflow()` now rejects workflows where the
  bundle diagnostic step is missing, does not run with `if: always()`, or does
  not include both the file listing and checksum output commands.
- Local focused verification for M7.D.24.F-prep passed on 2026-05-24: RED
  failures were observed for the missing summary workflow step and missing
  validator rule; after implementation, the focused workflow/validator tests
  passed 3 tests.
- Final M7.D.24.F-prep verification passed on 2026-05-24: focused
  workflow/validator/governance tests passed 4 tests, focused ruff
  format/check passed after formatting `tests/test_governance_cmd.py`, mypy
  passed for `agmind/deploy/target_checks.py`,
  `scripts/kubernetes_proof_workflow_check.py` passed, aggregate governance
  passed with 6 checks, and the expanded M7 pytest set passed 172 tests.

M7.D.24.G-prep Kubernetes proof verification report checkpoint:

- The manual `Verify k3s proof bundle` step now runs
  `scripts/kubernetes_dry_run.py --json --verify-artifact-dir
  local-kubernetes-proof/k3s`, tees the JSON report to
  `local-kubernetes-proof/k3s/verification.json`, preserves the verifier exit
  status with `${PIPESTATUS[0]}`, and uploads `verification.json` with the
  proof bundle.
- `validate_kubernetes_proof_workflow()` now rejects workflows where the
  verifier step does not write `verification.json`, does not preserve the
  verifier exit status, or does not upload the verifier report artifact.
- Local focused verification for M7.D.24.G-prep passed on 2026-05-24: RED
  failures were observed for the text-only verifier workflow and missing
  validator rule; after implementation, focused workflow/validator tests passed
  4 tests.
- Final M7.D.24.G-prep verification passed on 2026-05-24: focused
  workflow/validator/governance tests passed 5 tests, focused ruff
  format/check passed after formatting `agmind/deploy/target_checks.py`, mypy
  passed for `agmind/deploy/target_checks.py`,
  `scripts/kubernetes_proof_workflow_check.py` passed, aggregate governance
  passed with 6 checks, and the expanded M7 pytest set passed 173 tests.

M7.D.24.H-prep Kubernetes proof verification upload guard checkpoint:

- `validate_kubernetes_proof_workflow()` now checks that
  `local-kubernetes-proof/k3s/verification.json` appears in the
  `actions/upload-artifact@v4` step specifically, not merely somewhere in the
  workflow.
- This closes the false-positive where the verifier `tee .../verification.json`
  command existed but the uploaded artifact path was accidentally removed.
- Local focused verification for M7.D.24.H-prep passed on 2026-05-24: RED
  failure showed the old validator returned no errors when only the upload path
  was removed; after implementation, focused verifier-upload tests passed 3
  tests.
- Final M7.D.24.H-prep verification passed on 2026-05-24: focused
  verifier-upload/governance tests passed 4 tests, focused ruff format/check
  passed, mypy passed for `agmind/deploy/target_checks.py`,
  `scripts/kubernetes_proof_workflow_check.py` passed, aggregate governance
  passed with 6 checks, and the expanded M7 pytest set passed 174 tests.

M7.D.24.I-prep Kubernetes proof upload-scoped artifact guard checkpoint:

- `validate_kubernetes_proof_workflow()` now checks every
  `target.verification.artifacts` proof artifact inside the
  `actions/upload-artifact@v4` step specifically, not just anywhere in the
  workflow text.
- This closes the same false-positive class for declared artifacts such as
  `checksums.txt`, which may also appear in the diagnostic step.
- Local focused verification for M7.D.24.I-prep passed on 2026-05-24: RED
  failure showed the old validator returned no errors when only the
  `checksums.txt` upload path was removed; after implementation, focused
  upload-scoped artifact tests passed 3 tests.
- Final M7.D.24.I-prep verification passed on 2026-05-24: focused
  upload-scoped/governance tests passed 4 tests, focused ruff format/check
  passed after formatting `tests/test_governance_cmd.py`, mypy passed for
  `agmind/deploy/target_checks.py`,
  `scripts/kubernetes_proof_workflow_check.py` passed, aggregate governance
  passed with 6 checks, and the expanded M7 pytest set passed 175 tests.

M7.D.24.J-prep Kubernetes proof run metadata checkpoint:

- The dry-run harness now writes `run-metadata.json` into proof bundles using
  an allowlist of GitHub Actions and runner environment fields.
- `summary.json` records the same metadata payload and path, `checksums.txt`
  covers the metadata artifact, and the artifact verifier rejects metadata
  drift between `summary.json` and `run-metadata.json`.
- The `k3s` target contract declares `run-metadata.json`, and the manual
  `kubernetes-proof` workflow uploads it with the rest of the proof bundle.
- Final M7.D.24.J-prep verification passed on 2026-05-24: Kubernetes dry-run
  tests passed 28 tests, focused workflow/contract tests passed 5 tests,
  workflow/deploy-target/governance scripts passed, CLI artifact smoke
  verified `run-metadata.json`, expanded M7 pytest passed 176 tests,
  `git diff --check` passed, and full pre-commit passed.

M7.D.24.K-prep Kubernetes proof required checksum coverage checkpoint:

- `verify_kubernetes_dry_run_artifacts()` now derives required proof members
  from `summary.json`: `summary.json`, `proof_command_path`,
  `run_metadata_path`, and every target `manifest_path` and `report_path`.
- The verifier rejects bundles where one of those files exists but is not
  listed in `checksums.txt`, closing the gap where a file could match
  `summary.json` but still not be checksum-covered.
- Local focused verification for M7.D.24.K-prep passed on 2026-05-24: RED
  failure showed the old verifier accepted a bundle with the
  `run-metadata.json` checksum line removed; after implementation, focused
  required-checksum tests passed 3 tests and the full Kubernetes dry-run test
  module passed 29 tests.

M7.D.24.L-prep Kubernetes proof required artifact presence checkpoint:

- `verify_kubernetes_dry_run_artifacts()` now derives the required proof
  artifact set once from `summary.json` and validates both presence and
  checksum coverage for that set.
- The required set includes `summary.json`, `proof_command_path`,
  `run_metadata_path`, and every target `manifest_path` and `report_path`.
- This closes the gap where an artifact such as `proof-command.txt` could be
  deleted together with its checksum line while still being referenced by
  `summary.json`.
- Local focused verification for M7.D.24.L-prep passed on 2026-05-24: RED
  failure showed the old verifier accepted a bundle with both
  `proof-command.txt` and its checksum line removed; after implementation,
  focused required-artifact tests passed 3 tests and the full Kubernetes
  dry-run test module passed 30 tests.

M7.D.24.M-prep Kubernetes proof checksum path containment checkpoint:

- `_verify_checksum_file()` now rejects checksum member paths that are
  absolute or contain `..` before reading or hashing the referenced file.
- This prevents copied or uploaded proof bundles from causing the verifier to
  read files outside the artifact directory via a crafted `checksums.txt`.
- Local focused verification for M7.D.24.M-prep passed on 2026-05-24: RED
  failure showed the old verifier accepted a checksum entry for
  `../outside-proof-file.txt` when the outside file existed and the digest
  matched; after implementation, focused path-containment tests passed 3 tests
  and the full Kubernetes dry-run test module passed 31 tests.
- Final local verification also passed: workflow, deploy-target, and governance
  scripts; focused proof/governance pytest with 32 tests; expanded M7 pytest
  with 179 tests; CLI artifact smoke; `git diff --check`; and full pre-commit.

M7.D.24.N-prep Kubernetes proof command consistency checkpoint:

- `verify_kubernetes_dry_run_artifacts()` now checks that checksum-covered
  `proof-command.txt` matches `summary.json::proof_command` exactly.
- This closes the gap where `proof-command.txt` could be changed and its
  checksum line regenerated while `summary.json` still claimed the original
  proof command.
- Local focused verification for M7.D.24.N-prep passed on 2026-05-24: RED
  failure showed the old verifier accepted proof-command drift; after
  implementation, focused proof-command consistency tests passed 3 tests and
  the full Kubernetes dry-run test module passed 32 tests.
- Final local verification also passed: workflow, deploy-target, and governance
  scripts; focused proof/governance pytest with 33 tests; expanded M7 pytest
  with 180 tests; CLI artifact smoke; `git diff --check`; and full pre-commit.

M7.D.24.O-prep Kubernetes proof target report consistency checkpoint:

- `verify_kubernetes_dry_run_artifacts()` now checks each checksum-covered
  `<target>.dry-run.json` report against the corresponding target object in
  `summary.json`.
- This closes the gap where a target report could be changed and its checksum
  line regenerated while `summary.json` still claimed the original target
  evidence.
- Local focused verification for M7.D.24.O-prep passed on 2026-05-24: RED
  failure showed the old verifier accepted target report drift; after
  implementation, focused target report consistency tests passed 3 tests and
  the full Kubernetes dry-run test module passed 33 tests.
- Final local verification also passed: workflow, deploy-target, and governance
  scripts; focused proof/governance pytest with 34 tests; expanded M7 pytest
  with 181 tests; CLI artifact smoke; `git diff --check`; and full pre-commit.

M7.D.24.P-prep Kubernetes proof summary consistency checkpoint:

- `verify_kubernetes_dry_run_artifacts()` now checks that `summary.json::ok`
  matches the target statuses and `require_cluster` policy recorded in the same
  summary payload.
- This closes the gap where `summary.json::ok` could be changed and the summary
  checksum line regenerated while the target evidence still implied the
  original aggregate result.
- Local focused verification for M7.D.24.P-prep passed on 2026-05-24: RED
  failure showed the old verifier accepted summary ok drift; after
  implementation, focused summary consistency tests passed 3 tests and the full
  Kubernetes dry-run test module passed 34 tests.
- Final local verification also passed: workflow, deploy-target, and governance
  scripts; focused proof/governance pytest with 35 tests; expanded M7 pytest
  with 182 tests; CLI artifact smoke; `git diff --check`; and full pre-commit.

M7.D.24.Q-prep Kubernetes proof target ids consistency checkpoint:

- `verify_kubernetes_dry_run_artifacts()` now checks that
  `summary.json::target_ids` matches the ordered `target_id` fields in the
  summary target records.
- This closes the gap where `summary.json::target_ids` could be changed and the
  summary checksum line regenerated while the target records still described a
  different proof scope.
- Local focused verification for M7.D.24.Q-prep passed on 2026-05-24: RED
  failure showed the old verifier accepted target_ids drift; after
  implementation, focused target_ids consistency tests passed 3 tests and the
  full Kubernetes dry-run test module passed 35 tests.
- Final local verification also passed: workflow, deploy-target, and governance
  scripts; focused proof/governance pytest with 36 tests; expanded M7 pytest
  with 183 tests; CLI artifact smoke; `git diff --check`; and full pre-commit.

M7.D.24.R-prep Kubernetes proof command target consistency checkpoint:

- `verify_kubernetes_dry_run_artifacts()` now checks that the `--target` flags
  inside `summary.json::proof_command` match `summary.json::target_ids`.
- This closes the gap where both `summary.json::proof_command` and
  checksum-covered `proof-command.txt` could point at a different target while
  `target_ids` and target records still described the original proof scope.
- Local focused verification for M7.D.24.R-prep passed on 2026-05-24: RED
  failure showed the old verifier accepted proof_command target drift; after
  implementation, focused proof_command target consistency tests passed 3 tests
  and the full Kubernetes dry-run test module passed 36 tests.
- Final local verification also passed: workflow, deploy-target, and governance
  scripts; focused proof/governance pytest with 37 tests; expanded M7 pytest
  with 184 tests; CLI artifact smoke; `git diff --check`; and full pre-commit.

M7.D.24.S-prep Kubernetes proof command require_cluster consistency checkpoint:

- `verify_kubernetes_dry_run_artifacts()` now checks that
  `summary.json::proof_command` includes `--require-cluster` exactly when
  `summary.json::require_cluster` is true.
- This closes the gap where checksum-covered `proof-command.txt` could lose the
  live-cluster proof flag while the summary still claimed the bundle required
  live-cluster evidence.
- Local focused verification for M7.D.24.S-prep passed on 2026-05-24: RED
  failure showed the old verifier accepted missing `--require-cluster` drift;
  after implementation, focused require_cluster consistency tests passed 3
  tests and the full Kubernetes dry-run test module passed 37 tests.
- Final local verification also passed: workflow, deploy-target, and governance
  scripts; focused proof/governance pytest with 38 tests; expanded M7 pytest
  with 185 tests; CLI artifact smoke; `git diff --check`; and full pre-commit.

M7.A.8-prep Setup service selection component closure checkpoint:

- `agmind.services.selection.resolve_service_selection()` now expands setup-time
  explicit service choices into a deployable closure without changing
  low-level exact `select_services()` renderer semantics.
- Stack components are intentionally narrow: a component expands as a stack only
  when its component contract provides a `*_stack` capability. This lets
  `dify-api` pull the Dify runtime siblings while preventing a single
  stateful-service checkbox such as `qdrant` from pulling MySQL, Milvus,
  Weaviate, and every storage alternative.
- The resolver recursively adds `depends_on` services and satisfies mandatory
  component capabilities from deterministic default providers. Selecting
  `dify-api` now pulls `dify-web`, `dify-worker`, `dify-plugin-daemon`,
  `dify-sandbox`, `postgres`, `redis`, `qdrant`, `llama-llm`, and
  `llama-embed`, while optional `dify_external_kb` does not auto-pull RagFlow.
- The setup wizard stores expanded selections in both legacy and multistep
  flows, and multistep `ServicesScreen` visibly checks the expanded Dify
  closure when the operator toggles `dify-api`.
- Local focused verification passed on 2026-05-24: RED tests reproduced the
  missing resolver/helper/checkbox behavior; after implementation,
  service-selection, setup helper, and multistep checkbox tests passed 3 tests,
  and the focused setup/TUI/service-selection suite passed 50 tests.
- Final local verification also passed: focused service/TUI/compat/deploy
  pytest with 78 tests; ruff format check, ruff check, and mypy for touched
  source modules; deploy-target and aggregate governance scripts; expanded M7
  pytest with 186 tests; `git diff --check`; and full pre-commit.

M7 local verification checkpoint, 2026-05-24:

- `git diff --check` passed.
- `uv pip check --python .venv/bin/python` passed: all 81 installed packages
  are compatible.
- `scripts/audit_forbidden.py --fail` passed: 291 files checked, 0 findings.
- `scripts/governance_check.py` passed: component contracts, deployment
  targets, optional tool candidates, and dependency constraints all OK.
- `scripts/version_check.py --offline --json /tmp/agmind-version-v2.json`
  completed successfully and wrote the offline version report. Registry probes
  remain offline/noisy by design in this mode.
- Focused M7 pytest set passed: 133 tests.
- `pre-commit run --all-files --show-diff-on-failure` passed on the second run
  after ruff applied one automatic formatting/import fix on the first run.

## Shipped Milestones

| Milestone | Status | Notes |
|-----------|--------|-------|
| M1 v0.1.0-dev — Migration alpha | shipped 2026-05-19 | A-G migration |
| M2 v0.2.0 — Production hardening | shipped 2026-05-20 | H', L, J.2, H, N, O, P |
| M3 v0.3.0 — UX + ops polish | shipped 2026-05-20 | final M3 commit `57fd3ab` |
| M4 wave — Cluster + UX bundle | shipped 2026-05-21 | cluster discovery + UX bundle |
| M5 v0.5.0 — Model split + TUI polish round 2 | shipped 2026-05-21 | LLM/embed/rerank split, TUI polish, cluster TUI |
| M6.S0/M6.B CI cleanup | shipped 2026-05-22 | self-hosted CI green through Strix smoke |

## Next Focus

Recommended order after this local checkpoint:

1. **Current M7 batch external proof:** commit/review this batch, then run the
   self-hosted GitHub Actions workflow on the resulting ref.
2. **M7.C Proxmox runtime proof:** use `agmind tools status proxmox-exporter`,
   run real Proxmox API-token smoke for the accepted exporter, and validate the
   OpenTofu lane on a host with `tofu` and a real Proxmox endpoint.
3. **M6.C Real install E2E:** run dry/full install path on the Strix Halo box
   and record evidence.
4. **M6.B follow-up:** stop Dependabot/release-drafter from stealing the only
   self-hosted runner.
5. **M7.D Kubernetes real cluster evidence:** rerun the self-describing dry-run
   evidence bundle on a host with `kubectl`, kubeconfig, and AMD GPU device
   plugin visibility:
   `scripts/kubernetes_dry_run.py --target k3s --require-cluster
   --require-amd-gpu --artifact-dir <dir>` against a real k3s kubeconfig.

## Known Live Gaps

- Doctor host tuning: kernel/GTT pool.
- Dependabot/release-drafter can occupy the self-hosted runner.
- Grafana dashboard JSON provision remains deferred.
- Authelia 2FA wizard flow remains deferred.
- Plugin marketplace remains deferred.
- Full cluster deploy with peer replication still needs real smoke evidence.
- OpenTofu CLI is not installed in the current dev environment, so real
  `tofu init/fmt/validate/plan` still needs host validation.
- Proxmox exporter runtime/config/admission validation is local and opt-in, but
  still needs real Proxmox API-token smoke evidence and a decision on managed
  remote scrape provisioning.
- Kubernetes renderer is an MVP: local render governance exists, expected
  warning policy is target-declared, the k3s warning baseline is down to
  4 non-blocking warnings with strict render-check passing locally, and dry-run
  evidence bundles are now self-describing, target-selectable, include
  actionable warning details, carry checksum evidence, include allowlisted
  GitHub/runner provenance in `run-metadata.json`, enforce checksum entries
  and physical presence for required proof members, reject checksum path
  escapes, verify `proof-command.txt` against `summary.json::proof_command`,
  verify per-target dry-run reports against `summary.json`, and can be verified
  locally after upload/copy. The verifier also checks `summary.json::ok` against
  summary target statuses and `require_cluster` policy, and `target_ids` against
  the summary target records plus `proof_command --target` and
  `--require-cluster` flags. There is still no
  Helm chart, Ingress, External Secrets, Longhorn storage classes, AMD GPU
  device-plugin install proof, or real `kubectl --dry-run=server` evidence yet.

## Reference Documents

- `.planning/PROJECT.md`
- `.planning/REQUIREMENTS.md`
- `.planning/ROADMAP.md`
- `.planning/BACKLOG.md`
- `.planning/codebase/`
- `.planning/sessions/`
- `.planning/research/homelab-enterprise/`
- `docs/adr/`
- `docs/BENCHMARKS.md`

**Last updated:** 2026-05-25.
