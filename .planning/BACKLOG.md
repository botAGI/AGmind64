# AGmind x86 — Backlog (post-M5, 2026-05-24)

> **M5 SHIPPED 2026-05-21:** model split + per-service settings + TUI polish round 2
> + cluster TUI integration. 886 tests · 0 audit findings.
> Commits: `1e63fb0` (M5.1+M5.2) + `c86b3e0` (M5.3+M5.4).
>
> **Current gate 2026-05-24:** M7.A component/version contracts, M7.B deploy
> target contracts, M7.C optional runtime admission, and M7.G aggregate
> governance are implemented and locally verified after full green self-hosted
> CI and codebase/planning cleanup.
> Latest full green CI run: `26333245295` on `d21294f`.


Структурированный backlog для next sessions. Сгруппировано по
**milestone × priority**. См. `ROADMAP.md` для phase context.

Legend:
- 🔴 **Critical** — production blocker
- 🟡 **High** — production-readiness
- 🟢 **Medium** — UX polish
- 🔵 **Low** — nice-to-have

## Status snapshot (2026-05-24)

- **M1 v0.1.0-dev (Migration alpha):** ✅ SHIPPED
- **M2 v0.2.0 (Production hardening):** ✅ SHIPPED
- **M3 v0.3.0 (UX + ops polish):** ✅ SHIPPED
- **M4 wave (Cluster + UX bundle):** ✅ SHIPPED
- **M5 v0.5.0 (Model split + TUI polish round 2):** ✅ SHIPPED
- **M6 v0.6.0 candidate (Hardening + E2E):** current
- **M7.A component/version contracts:** local implementation complete
- **M7.B deploy targets:** local implementation complete for contract,
  Proxmox VM skeleton, inventory bridge, CLI, and CI/pre-commit gates
- **M7.C optional tools:** first accepted runtime path complete locally
- **M7.G aggregate governance:** local implementation verified

CI baseline: **full self-hosted GitHub Actions green**, including Docker
cpu/vulkan/rocm and Strix Halo smoke vulkan/rocm. Local dev-only parity:
882 passed, 4 deselected for `backend_any or backend_cpu`.
Local M7 verification, 2026-05-24: `git diff --check`, `uv pip check`,
forbidden audit, aggregate governance, offline version report generation,
focused 133-test pytest set, and full `pre-commit --all-files` all pass.
Local M7.A contract gate: `templates/components/*.yaml` validates against
`templates/schemas/component.json`; all 34 service descriptors have exactly one
component owner. Version check v2 now renders component policies and
non-container dependency pins. `agmind upgrade` can now plan/apply/rollback
grouped component updates while preserving raw service fallback. Component
contract checks are wired into local pre-commit and self-hosted CI. Python and
backend dependency planes now have constraint files and validation.
`templates/models.yaml` is now the canonical wizard model catalog source.
`DeploymentTarget` contracts now define the first universal deploy ladder:
`ubuntu-compose`, `proxmox-vm-compose`, and `k3s`. The first OpenTofu Proxmox
module skeleton now exists for `proxmox-vm-compose`, and OpenTofu output can
now be converted into AGmind Ansible inventory. `agmind targets
list/status/validate` exposes this deploy ladder and checks local references
for supported/experimental lanes. The deploy target gate is now wired into
pre-commit and the self-hosted CI through `scripts/deploy_target_check.py` plus
schema validation for `templates/deploy-targets/*.yaml`. Optional homelab/enterprise
tools now have an admission catalog before runtime inclusion. Proxmox exporter
is the first accepted opt-in runtime descriptor and still needs real Proxmox
API-token smoke evidence. Its Ansible guard now prevents a missing
`pve.yml` bind-mount source when the `proxmox` profile is enabled, and the
local validator checks token-auth shape before compose up. Accepted optional
candidates are now checked against real service descriptors, component
ownership, digest pins, profiles, and ports. `agmind tools list/status/validate`
now exposes the optional tool catalog and admission result for operators and CI.
Tool candidate schema/admission validation is also wired into pre-commit and
the self-hosted CI through the `tool-candidate-validate` job. A new aggregate
`agmind governance validate` / `scripts/governance_check.py` command runs the
M7 component, deploy target, optional tool, and dependency constraint gates in
one operator-facing report. The aggregate report is wired into pre-commit for
wrapper changes and into CI as `governance-validate` after the focused M7 jobs.
M7.D.1-M7.D.23 added a research-grade `agmind render kubernetes` MVP plus a
local Kubernetes render governance gate. The gate renders the `k3s` target
from current service descriptors and reports Docker-only portability warnings
with stable codes, severity, and remediation before any real k3s promotion.
M7.D.4 now blocks accidental Kubernetes promotion: blocker warnings are allowed
only while the target status remains `research`. M7.D.5 exposes the full
service-level warning/remediation list in JSON and compact blocker-code
breakdowns in text output for operator triage. M7.D.6 adds a server-side
dry-run proof harness that cleanly distinguishes passed, skipped, and failed
cluster evidence. M7.D.7 records the dry-run evidence bundle shape for real
k3s proof runs. M7.D.8 removes Traefik's Kubernetes Docker-socket blocker by
switching its rendered args to the Kubernetes provider. M7.D.9 omits
Docker-socket-based Portainer from Kubernetes output with an explicit warning,
and M7.D.10 maps the known `/dev/dri` inference device to the Kubernetes
extended resource `amd.com/gpu`, leaving the local k3s baseline with no
blocker warnings. M7.D.11 adds the real-cluster AMD GPU proof preflight to the
server-side dry-run harness. M7.D.12 resolves explicit non-empty descriptor
defaults in Kubernetes env/command rendering, reducing the k3s baseline to 15
warnings and 0 blockers while secrets and empty defaults remain visible.
M7.D.13 maps secret-like unresolved env placeholders to Kubernetes
`secretKeyRef` entries, reducing the k3s baseline to 11 warnings and 0
blockers while external Secret materialization remains a follow-up.
M7.D.14 maps supported Docker security fields to Kubernetes `securityContext`,
reducing the k3s baseline to 10 warnings and 0 blockers while named
`video`/`render` groups remain target-policy debt.
M7.D.15 treats those Docker GPU groups as covered when `/dev/dri` is rendered
through the AMD GPU device-plugin resource, reducing the k3s baseline to 7
warnings and 0 blockers.
M7.D.16 resolves explicit empty descriptor env defaults to Kubernetes empty
env values only in env rendering, reducing the k3s baseline to 5 warnings and
0 blockers while unsafe command placeholders stay visible.
M7.D.17 adds target-declared expected warning codes, so strict render
validation now allows known AMD GPU device-plugin prerequisites and Compose-only
Portainer omission while still failing on the single unexpected
`command-interpolation=1` warning.
M7.D.18 omits unconfigured `llama-rerank` from k3s with an explicit
`kubernetes-omitted` warning, reducing the local baseline to 34 objects,
4 warnings, and 0 blockers while strict render-check now passes locally.
M7.D.19 makes dry-run evidence bundles self-describing: aggregate
`summary.json` records cluster/GPU requirements, kubectl binary, kube context,
namespace, artifact directory, and summary path before the real k3s proof run.
M7.D.20 adds explicit target selection to the proof harness: repeatable
`--target <id>` flags, `target_ids` in `summary.json`, early rejection of
unknown/non-Kubernetes targets, and a k3s verification command pinned to
`--target k3s`. M7.D.21 adds actionable warning details to dry-run evidence:
target reports and `summary.json` now include warning `service`, `code`,
`severity`, `message`, `remediation`, and target-policy `expected` fields.
M7.D.22 adds artifact integrity evidence: target reports include manifest byte
size and SHA256 digest, `summary.json` records `checksum_path`, and
`checksums.txt` covers persisted bundle files. M7.D.23 adds a first-party
artifact verifier: `scripts/kubernetes_dry_run.py --verify-artifact-dir <dir>`
checks copied or uploaded bundles by reading `summary.json`, `checksums.txt`,
listed files, SHA256 digests, and per-target manifest byte/digest metadata in
text or JSON mode. Latest M7.D.23 local verification passed on 2026-05-24:
focused Kubernetes dry-run tests passed 23 tests, governance and strict
Kubernetes render checks passed, the expanded M7 pytest set passed 190 tests,
verifier smoke accepted a generated bundle and rejected a corrupted manifest,
and `git diff --check` plus full pre-commit passed.
M7.D.24.A turns that verifier into a deployment-target contract for real k3s
proof runs: the `k3s` verification command now writes
`local-kubernetes-proof/k3s`, runs the matching `--verify-artifact-dir`
command, and declares `k3s.yaml`, `k3s.dry-run.json`, `summary.json`, and
`checksums.txt` as expected proof artifacts. M7.D.24.D-prep extends that bundle
with `proof-command.txt`, records it in `summary.json`, includes it in
`checksums.txt`, and declares/uploads it through the k3s target contract and
manual workflow. Deploy-target validation rejects
Kubernetes `--require-cluster` proof commands that omit the target, artifact
directory, verifier, or bundle artifact declarations. Latest M7.D.24.A local
focused verification passed 15 deploy-target tests after RED failures for the
missing contract and validator rules.
Final M7.D.24.A local verification passed on 2026-05-24: focused ruff format
check, ruff check, mypy, deploy-target check, aggregate governance check,
expanded 194-test M7 pytest set, `git diff --check`, and full pre-commit.
M7.D.24.B-prep adds `.github/workflows/kubernetes-proof.yml`, a manual
`workflow_dispatch` path for a kubeconfig-equipped self-hosted runner labeled
`k3s`. It installs with the existing `uv` pattern, runs strict Kubernetes render
validation, executes the contract-defined k3s proof into
`local-kubernetes-proof/k3s`, verifies the bundle, and uploads `k3s.yaml`,
`k3s.dry-run.json`, `proof-command.txt`, `summary.json`, and `checksums.txt`.
Latest focused local verification passed after RED failure for the missing workflow:
`tests/test_kubernetes_dry_run.py` passed 24 tests.
Final M7.D.24.B-prep local verification passed on 2026-05-24: focused ruff
format check, ruff check, aggregate governance check, expanded 195-test M7
pytest set, `git diff --check`, and full pre-commit.
M7.D.24.C-prep adds `scripts/kubernetes_proof_workflow_check.py`, a local
drift guard that keeps `.github/workflows/kubernetes-proof.yml` aligned with
the `k3s` deployment target proof contract. It validates manual-only triggers,
the k3s self-hosted runner label, runner-local Python/uv usage, strict render
validation, proof target/artifact-dir flags, matching bundle verifier, and all
declared proof artifacts. It also requires the verifier step to run with
`if: always()` so failed proof attempts still produce verifier diagnostics
before artifact upload. The guard is wired into pre-commit, self-hosted CI, and
aggregate governance as the sixth check. Latest focused verification
passed after RED failures for the missing script and governance/CI wiring:
focused tests passed 7 tests and the script reported
`kubernetes proof workflow OK: 1 targets`.
Final M7.D.24.C-prep local verification passed on 2026-05-24: focused
workflow/governance tests passed 9 tests, aggregate governance passed with 6
checks, expanded 196-test M7 pytest set passed, `git diff --check` passed, and
full pre-commit passed.
M7.D.24.D-prep makes proof bundles self-describing: `proof-command.txt` stores
the exact dry-run proof command, `summary.json` records the command/path, the
bundle verifier catches checksum drift for the command artifact, and the
manual workflow uploads it. Latest focused local verification passed after RED
failures for the missing artifact and contract declaration: new
artifact/verifier tests passed 4 tests and deploy-target/workflow checks
passed 17 tests. Final local verification passed: full Kubernetes dry-run tests
passed 27 tests, focused governance/contract tests passed 50 tests, workflow
drift check and aggregate governance passed, a CLI proof bundle smoke verified
`proof-command.txt`, the expanded M7 pytest set passed 170 tests,
`git diff --check` passed, and full pre-commit passed.
M7.D.24.E-prep makes the manual proof workflow more useful on failure: the
`Verify k3s proof bundle` step now runs with `if: always()`, and
`scripts/kubernetes_proof_workflow_check.py` rejects workflows where the
verifier command is not always-run guarded. Latest focused local verification
passed after RED failures for the missing workflow guard and validator rule:
workflow/validator tests passed 3 tests. Final local verification passed:
focused workflow/validator/governance tests passed 4 tests, workflow drift
check and aggregate governance passed, and the expanded M7 pytest set passed
171 tests.
M7.D.24.F-prep adds always-run proof bundle diagnostics to the manual workflow:
before upload, it lists produced files under `local-kubernetes-proof/k3s` and
prints `checksums.txt` when present. The workflow drift guard now rejects
workflows without that diagnostic step. Latest focused local verification
passed after RED failures for the missing summary step and validator rule:
workflow/validator tests passed 3 tests. Final local verification passed:
focused workflow/validator/governance tests passed 4 tests, workflow drift
check and aggregate governance passed, and the expanded M7 pytest set passed
172 tests.
M7.D.24.G-prep adds a machine-readable verifier report to manual proof
artifacts: the always-run verifier writes `verification.json`, preserves the
verifier exit status, uploads the report, and the workflow drift guard enforces
that contract. Latest focused local verification passed after RED failures for
the text-only verifier workflow and missing validator rule: workflow/validator
tests passed 4 tests. Final local verification passed: focused
workflow/validator/governance tests passed 5 tests, workflow drift check and
aggregate governance passed, and the expanded M7 pytest set passed 173 tests.
M7.D.24.H-prep tightens the verifier report drift guard: the validator now
requires `verification.json` in the `actions/upload-artifact@v4` step
specifically, closing the false-positive where `tee .../verification.json`
existed but the upload path was removed. Latest focused local verification
passed after RED exposed the gap: verifier-upload tests passed 3 tests. Final
local verification passed: focused verifier-upload/governance tests passed 4
tests, workflow drift check and aggregate governance passed, and the expanded
M7 pytest set passed 174 tests.
M7.D.24.I-prep extends upload-scoped validation to every target-declared proof
artifact. The workflow drift guard now rejects missing upload paths even when
the same artifact path still appears in diagnostics, closing the same
false-positive class for files like `checksums.txt`. Latest focused local
verification passed after RED exposed the gap: upload-scoped artifact tests
passed 3 tests. Final local verification passed: focused
upload-scoped/governance tests passed 4 tests, workflow drift check and
aggregate governance passed, and the expanded M7 pytest set passed 175 tests.
M7.D.24.J-prep adds checksum-verified proof run metadata. The dry-run harness
now writes `run-metadata.json` from an allowlist of GitHub Actions and runner
environment fields, records the same payload/path in `summary.json`, includes
the metadata file in `checksums.txt`, and the verifier rejects bundles where
the summary metadata and metadata artifact diverge. The k3s target contract
declares the artifact and the manual proof workflow uploads it with the rest of
the bundle. Latest focused local verification passed after RED failures for
the missing metadata artifact and workflow upload: Kubernetes dry-run tests
passed 28 tests and focused workflow/contract tests passed 5 tests. Final
local verification passed: workflow/deploy-target/governance scripts passed,
the CLI artifact smoke verified `run-metadata.json`, the expanded M7 pytest set
passed 176 tests, `git diff --check` passed, and full pre-commit passed.
M7.D.24.K-prep tightens verifier integrity semantics for proof bundles. The
artifact verifier now derives required members from `summary.json` and rejects
bundles where `summary.json`, `proof-command.txt`, `run-metadata.json`, target
manifests, or target dry-run reports exist but are not listed in
`checksums.txt`. Latest focused local verification passed after RED exposed the
gap: removing only the `run-metadata.json` checksum line now fails, focused
required-checksum tests passed 3 tests, and the full Kubernetes dry-run test
module passed 29 tests. Final local verification passed: workflow,
deploy-target, and governance scripts passed; focused proof/governance tests
passed 30 tests; the expanded M7 pytest set passed 177 tests; the CLI artifact
smoke verified the bundle; `git diff --check` passed; and full pre-commit
passed.
M7.D.24.L-prep tightens verifier required artifact semantics. The artifact
verifier now derives the required proof member set once from `summary.json` and
rejects bundles where required files such as `proof-command.txt` are missing
entirely, even if their checksum entries are missing too. Latest focused local
verification passed after RED exposed the gap: deleting both `proof-command.txt`
and its checksum line now fails, focused required-artifact tests passed 3 tests,
and the full Kubernetes dry-run test module passed 30 tests. Final local
verification passed: workflow, deploy-target, and governance scripts passed;
focused proof/governance tests passed 31 tests; the expanded M7 pytest set
passed 178 tests; the CLI artifact smoke verified the bundle;
`git diff --check` passed; and full pre-commit passed.
M7.D.24.M-prep blocks checksum path escapes in proof bundle verification. The
artifact verifier now rejects `checksums.txt` member paths that are absolute or
contain `..`, before reading or hashing any referenced file. Latest focused
local verification passed after RED exposed the gap: a checksum entry for
`../outside-proof-file.txt` no longer verifies, focused path-containment tests
passed 3 tests, and the full Kubernetes dry-run test module passed 31 tests.
Final local verification passed: workflow, deploy-target, and governance
scripts passed; focused proof/governance tests passed 32 tests; the expanded M7
pytest set passed 179 tests; the CLI artifact smoke verified the bundle;
`git diff --check` passed; and full pre-commit passed.
M7.D.24.N-prep blocks proof-command drift in proof bundle verification. The
artifact verifier now rejects bundles where checksum-covered `proof-command.txt`
does not exactly match `summary.json::proof_command`. Latest focused local
verification passed after RED exposed the gap: changing `proof-command.txt` and
regenerating its checksum line no longer verifies, focused proof-command
consistency tests passed 3 tests, and the full Kubernetes dry-run test module
passed 32 tests. Final local verification passed: workflow, deploy-target, and
governance scripts passed; focused proof/governance tests passed 33 tests; the
expanded M7 pytest set passed 180 tests; the CLI artifact smoke verified the
bundle; `git diff --check` passed; and full pre-commit passed.
M7.D.24.O-prep blocks target report drift in proof bundle verification. The
artifact verifier now rejects bundles where checksum-covered
`<target>.dry-run.json` no longer matches the corresponding target object in
`summary.json`. Latest focused local verification passed after RED exposed the
gap: changing `k3s-research.dry-run.json` and regenerating its checksum line no
longer verifies, focused target report consistency tests passed 3 tests, and
the full Kubernetes dry-run test module passed 33 tests. Final local
verification passed: workflow, deploy-target, and governance scripts passed;
focused proof/governance tests passed 34 tests; the expanded M7 pytest set
passed 181 tests; the CLI artifact smoke verified the bundle;
`git diff --check` passed; and full pre-commit passed.
M7.D.24.P-prep blocks summary ok drift in proof bundle verification. The
artifact verifier now rejects bundles where `summary.json::ok` no longer
matches the target statuses and `require_cluster` policy recorded in the same
summary. Latest focused local verification passed after RED exposed the gap:
changing `summary.json::ok` and regenerating its checksum line no longer
verifies, focused summary consistency tests passed 3 tests, and the full
Kubernetes dry-run test module passed 34 tests. Final local verification
passed: workflow, deploy-target, and governance scripts passed; focused
proof/governance tests passed 35 tests; the expanded M7 pytest set passed 182
tests; the CLI artifact smoke verified the bundle; `git diff --check` passed;
and full pre-commit passed.
M7.D.24.Q-prep blocks target_ids drift in proof bundle verification. The
artifact verifier now rejects bundles where `summary.json::target_ids` no
longer matches the ordered target records in the same summary. Latest focused
local verification passed after RED exposed the gap: changing
`summary.json::target_ids` and regenerating its checksum line no longer
verifies, focused target_ids consistency tests passed 3 tests, and the full
Kubernetes dry-run test module passed 35 tests. Final local verification
passed: workflow, deploy-target, and governance scripts passed; focused
proof/governance tests passed 36 tests; the expanded M7 pytest set passed 183
tests; the CLI artifact smoke verified the bundle; `git diff --check` passed;
and full pre-commit passed.
M7.D.24.R-prep blocks proof_command target drift in proof bundle verification.
The artifact verifier now rejects bundles where the `--target` flags inside
`summary.json::proof_command` no longer match `summary.json::target_ids`.
Latest focused local verification passed after RED exposed the gap: changing
both `summary.json::proof_command` and checksum-covered `proof-command.txt` to
`--target other` no longer verifies, focused proof_command target consistency
tests passed 3 tests, and the full Kubernetes dry-run test module passed 36
tests. Final local verification passed: workflow, deploy-target, and governance
scripts passed; focused proof/governance tests passed 37 tests; the expanded M7
pytest set passed 184 tests; the CLI artifact smoke verified the bundle;
`git diff --check` passed; and full pre-commit passed.
M7.D.24.S-prep blocks proof_command require_cluster drift in proof bundle
verification. The artifact verifier now rejects bundles where
`summary.json::require_cluster` is true but `summary.json::proof_command` and
checksum-covered `proof-command.txt` omit `--require-cluster`. Latest focused
local verification passed after RED exposed the gap: removing
`--require-cluster` no longer verifies, focused require_cluster consistency
tests passed 3 tests, and the full Kubernetes dry-run test module passed 37
tests. Final local verification passed: workflow, deploy-target, and governance
scripts passed; focused proof/governance tests passed 38 tests; the expanded M7
pytest set passed 185 tests; the CLI artifact smoke verified the bundle;
`git diff --check` passed; and full pre-commit passed.
M7.A.8-prep adds setup-time component selection closure. A new
`agmind.services.selection.resolve_service_selection()` resolver keeps
low-level renderer service filtering exact, but expands setup wizard selections
into deployable closures. Stack components expand only when their component
contract provides a `*_stack` capability, so choosing `dify-api` now pulls the
Dify sibling services, recursive `depends_on` services, and mandatory providers
for Dify's component requirements (`llama-llm`, `llama-embed`, `qdrant`,
`postgres`, `redis`) without auto-pulling optional RagFlow external KB. The
multistep services screen visibly checks that expanded Dify closure when the
operator toggles `dify-api`. Latest focused local verification passed after RED
tests exposed the missing resolver/helper/checkbox behavior: service-selection,
setup helper, and multistep checkbox tests passed 3 tests; the focused
setup/TUI/service-selection suite passed 50 tests. Final local verification
passed: focused service/TUI/compat/deploy pytest passed 78 tests; ruff format
check, ruff check, and mypy passed for touched source modules; deploy-target
and aggregate governance scripts passed; the expanded M7 pytest set passed 186
tests; `git diff --check` passed; and full pre-commit passed.

---

## Completed checkpoint — M6.S0/M6.A cleanup

| # | Task | Priority | Notes |
|---|------|----------|-------|
| S0.1 | Remove Claude live artifacts | 🔴 | `.claude/` + `CLAUDE.md` removed and ignored |
| S0.2 | Refresh `.planning/codebase/*` | 🔴 | Current snapshot + architecture/deps/extensions/invariants/pitfalls |
| S0.3 | Document agent plugins/tooling needs | 🟡 | `AGENT_TOOLING.md` |
| S0.4 | Verify cleanup | 🔴 | pre-commit/audit/schema/git status inspected |

## Live queue — M6 hardening candidates

| # | Task | Priority | Notes |
|---|------|----------|-------|
| M6.C | Real `agmind install` E2E on Strix Halo | 🔴 | Record dry-run/full path evidence |
| M6.B.5 | Keep Dependabot/release-drafter off critical runner queue | 🟡 | Current self-hosted runner is single-lane |
| M6.D | Cluster deploy smoke with second LAN node | 🟡 | mDNS exists; replication needs evidence |
| M6.E.1 | Grafana dashboards JSON provision | 🟢 | Deferred since M2 |
| M6.E.2 | Authelia 2FA wizard flow | 🟢 | Service exists; UX/config missing |
| M6.E.3 | `agmind chat` against running deploy | 🟢 | Small demo-value feature |
| M6.E.4 | Plugin marketplace | 🔵 | Larger deferred scope |

## M7 candidate — component contracts + universal homelab/enterprise deploy

Reference research:
`.planning/research/homelab-enterprise/R20-component-boundaries-version-contracts.md`
and `.planning/research/homelab-enterprise/R19-universal-deploy-tooling.md`.

| # | Task | Priority | Notes |
|---|------|----------|-------|
| M7.A.0 | ADR: component/version contracts | ✅ | `docs/adr/0013-component-contracts-and-safe-updates.md` |
| M7.A.1 | `ComponentContract` schema + baseline inventory | ✅ | 9 contracts, all 34 services owned exactly once |
| M7.A.2 | Version check v2 scanner/report coverage | ✅ | Component policies, pyproject, Ansible Galaxy, Dockerfile pip installs, issue-66-style instructions |
| M7.A.3 | Compatibility checker split | ✅ | Service capability warnings remain soft; deploy host-port conflicts are hard errors |
| M7.A.4 | Component-aware upgrade workflow | ✅ | `--plan`, grouped apply state, grouped rollback, raw service fallback |
| M7.A.5 | Component contract CI gate | ✅ | `scripts/component_check.py`, pre-commit hook, self-hosted `component-validate` job |
| M7.A.6 | Python/backend constraints | ✅ | `constraints/{core,dev,cpu,vulkan,rocm-gfx1151}.txt`, checker, Dockerfile `-c`, CI gate |
| M7.A.7 | Model catalog unification | ✅ | `templates/models.yaml::wizard_catalog` backs setup wizard defaults and curated ids |
| M7.A.8 | Setup service selection closure | ✅ | Wizard `dify-api` selection expands Dify stack, recursive deps, and mandatory capability providers without pulling optional RagFlow |
| M7.A.9 | Reranker catalog + active doc source cleanup | ✅ | `bge-reranker-v2-m3-q8` restored as curated rerank default; migration spec demoted to legacy archive |
| M7.B.1 | ADR: deploy targets and provisioning boundary | ✅ | `docs/adr/0014-deploy-targets-and-provisioning-boundary.md` |
| M7.B.2 | `DeploymentTarget` schema | ✅ | Runtime/provisioner/configurator/storage/secrets profiles plus 3 baseline targets |
| M7.B.3 | OpenTofu Proxmox module skeleton | ✅ | `infra/proxmox/vm-compose` root module with cloud-init VM skeleton |
| M7.B.4 | Generate Ansible inventory from OpenTofu outputs | ✅ | `scripts/proxmox_inventory.py` writes local generated inventory |
| M7.B.5 | Deployment target operator CLI | ✅ | `agmind targets list/status/validate`, local reference validation |
| M7.B.6 | Deployment target CI/pre-commit gate | ✅ | `scripts/deploy_target_check.py`, pre-commit hook, self-hosted `deploy-target-validate` job |
| M7.B.7 | Deployment/proof structured validation reports | ✅ | Deploy-target and Kubernetes proof workflow gates now expose severity-aware JSON reports while legacy validators keep returning error message lists |
| M7.C.0 | Optional tool candidate catalog | ✅ | 9 candidate contracts, schema export, local validation hook |
| M7.C.1 | First optional runtime descriptor | ✅ | `proxmox-exporter` accepted as opt-in `proxmox` profile service |
| M7.C.2 | Proxmox exporter Ansible config guard | ✅ | Requires token vars or externally managed `pve.yml` before compose up |
| M7.C.3 | Proxmox exporter config validator | ✅ | `scripts/proxmox_exporter_check.py` validates `pve.yml` and optional exporter probe |
| M7.C.4 | Accepted candidate runtime admission gate | ✅ | Accepted service candidates must match descriptor, owner, digest, profiles, ports |
| M7.C.5 | Optional tool operator CLI | ✅ | `agmind tools list/status/validate` shares `agmind.addons.checks` admission logic |
| M7.C.6 | Optional tool CI/pre-commit visibility | ✅ | Tool-candidate schema validation plus self-hosted `tool-candidate-validate` job |
| M7.C.7 | k3s storage/secrets addon candidates | ✅ | Longhorn 1.11.2 and External Secrets Operator 2.4.1 recorded as deploy-target addons with version sources |
| M7.C.8 | RAG retrieval policy hardening | ✅ | Dify vector_db and RAGFlow search_index are explicit; Milvus selection no longer pulls Qdrant, ambiguous Dify vector providers warn, Dify leaf services no longer consume provider capabilities, and RAGFlow renders explicit MySQL/MinIO/Redis env/profile closure |
| M7.C.9 | Consumer-aware capability env injection | ✅ | Compose and Kubernetes renderers share capability env merging; provider selection prefers explicit consumer bindings and Dify vector priority before deterministic fallback |
| M7.C.10 | Cluster environment inspect | ✅ | `agmind cluster inspect` detects Docker/Compose, k3s/Kubernetes, Proxmox host/guest hints, mDNS peers, recommends a deploy target, enriches it from the deploy-target catalog, and reports probe timeouts safely |
| M7.C.11 | GitHub Actions runner monitor | ✅ | `agmind ci status` reports recent workflow runs and self-hosted runner online/busy state through `gh`, with JSON/text output and repo auto-detection |
| M7.C.12 | Shared deployment topology report | ✅ | Setup preview, confirm summary, and `agmind render topology` share RAG storage, dependency, compatibility warnings, structured JSON warning counts, and `--fail-on-warning` CI gating through `agmind.services.deployment_topology` |
| M7.C.13 | Topology governance gate | ✅ | `scripts/topology_check.py` validates standard profile lanes and is wired into governance, pre-commit, and self-hosted CI |
| M7.C.14 | Topology info/warning split | ✅ | Optional topology notes such as missing `dify_external_kb` are surfaced as `info` in report JSON/profile summaries while `--fail-on-warning` and topology governance fail only on warning/error lanes |
| M7.G.1 | Aggregate governance gate | ✅ | `agmind governance validate` and `scripts/governance_check.py` run M7 gates together |
| M7.G.2 | Aggregate governance CI summary | ✅ | Pre-commit hook plus self-hosted `governance-validate` summary job |
| M7.G.3 | Governance structured JSON payloads | ✅ | `agmind targets validate --json` and `agmind governance validate --json` expose parsed deploy/topology/Kubernetes/proof report payloads instead of forcing automation to parse text |
| M7.G.4 | Full governance JSON coverage | ✅ | Component, tool-candidate, and constraint gates now support `--json`, so all 7 governance checks expose parsed `payload` objects in aggregate JSON |
| M7.G.5 | Governance aggregate JSON summary | ✅ | Aggregate JSON now includes top-level counts for checks, payload coverage, components, targets, topology info/warnings, Kubernetes warnings, and total errors |
| M7.G.6 | Governance warning/info health totals | ✅ | Aggregate JSON summary now includes `total_warnings` and `total_infos`, separating non-blocking warnings from informational notes across all gates |
| M7.G.7 | Governance text health summary | ✅ | Text `agmind governance validate` now keeps readable gate output while appending warning/info/error totals to the final operator summary |
| M7.G.8 | Governance wrapper argv isolation | ✅ | Aggregate governance invokes child check entrypoints with explicit argv tuples so outer `--json` flags cannot leak into captured text stdout |
| M7.G.9 | Governance named health checks | ✅ | Aggregate JSON summary now lists `failed_checks`, `warning_checks`, and `info_checks` so CI/UI can highlight affected gates without traversing nested payloads |
| M7.G.10 | Governance per-check health rows | ✅ | Aggregate JSON summary now includes `check_health` rows with name, ok, warning, info, and error counts for dashboard-style consumers |
| M7.G.11 | Governance health status labels | ✅ | Aggregate JSON summary now derives `health_status` plus per-row `status` values (`ok`, `info`, `warning`, `failed`) for dashboard color/state mapping |
| M7.G.12 | Governance status distribution | ✅ | Aggregate JSON summary now includes `status_counts` for failed/warning/info/ok gate totals so dashboards do not need to recount health rows |
| M7.G.13 | Governance text status summary | ✅ | Text `agmind governance validate` final line now includes `status=<health_status>` alongside warning/info/error totals |
| M7.G.14 | Governance failure health summary | ✅ | Failed aggregate checks without parsed payloads, including unknown gate names, still render `status=failed` plus warning/info/error totals in the final text line |
| M7.G.15 | Governance effective failure status | ✅ | Structured payload `error_count > 0` now fails aggregate `ok`, summary counts, per-check JSON/health rows, and text status even if a child gate exits `0` |
| M7.G.16 | Governance result ok consistency | ✅ | `GovernanceCheckResult.ok` now uses the same effective pass/fail rule as aggregate report, JSON, `check_health`, and text output; raw process state remains in `returncode` |
| M7.G.17 | Governance process-error floor | ✅ | Non-zero child return codes contribute at least one aggregate error even when a structured payload omits `error_count`, preventing failed gates from reporting `health_status=ok` |
| M7.G.18 | Governance structured JSON hard error | ✅ | JSON-capable checks now fail structured aggregate mode when `--json` output is missing or invalid instead of silently passing with `payload=None` |
| M7.G.19 | Governance payload error summary | ✅ | Aggregate JSON now exposes per-check `payload_error` plus top-level `payload_error_count` and `payload_error_checks` for CI/UI classification |
| M7.V | Local final verification | ✅ | 133 focused tests plus full pre-commit all-files pass on 2026-05-24 |
| M7.D.1 | Kubernetes renderer MVP | ✅ | `agmind render kubernetes`, warnings/strict mode, k3s target no longer points at a future placeholder |
| M7.D.2 | Kubernetes render governance | ✅ | `scripts/kubernetes_render_check.py`, pre-commit hook, self-hosted `kubernetes-render-validate`, governance is now 5 checks |
| M7.D.3 | Kubernetes portability policy | ✅ | warning codes/severity/remediation; baseline k3s render started at 5 blockers + 22 warnings |
| M7.D.4 | Kubernetes promotion policy | ✅ | `experimental`/`supported` Kubernetes targets fail while blocker warnings remain |
| M7.D.5 | Kubernetes remediation report | ✅ | JSON warning checklist preserves service/code/severity/message/remediation; text output shows blocker-code breakdown |
| M7.D.6 | Kubernetes server dry-run harness | ✅ | `scripts/kubernetes_dry_run.py`, JSON/text evidence, safe skip without local cluster, `--require-cluster` for proof runs |
| M7.D.7 | Kubernetes dry-run artifact bundle | ✅ | `--artifact-dir` writes `<target>.yaml`, `<target>.dry-run.json`, and `summary.json` for reviewable proof runs |
| M7.D.8 | Traefik Kubernetes provider remediation | ✅ | Traefik Docker socket is omitted in Kubernetes render; k3s blocker baseline is now 4 blockers + 22 warnings |
| M7.D.9 | Portainer Kubernetes omission | ✅ | Compose-only Portainer is omitted from k3s render as `kubernetes-omitted`; blocker baseline is now 3 device blockers + 23 warnings |
| M7.D.10 | AMD GPU Kubernetes device mapping | ✅ | Known `/dev/dri` inference devices render as `amd.com/gpu`; k3s blocker baseline is now 0 blockers + 26 warnings |
| M7.D.11 | Kubernetes AMD GPU proof preflight | ✅ | `scripts/kubernetes_dry_run.py --require-amd-gpu` records allocatable `amd.com/gpu` evidence before server dry-run |
| M7.D.12 | Kubernetes default interpolation | ✅ | Defaulted descriptor env/command placeholders resolve in k8s render; k3s warning baseline is now 15 warnings + 0 blockers |
| M7.D.13 | Kubernetes Secret env refs | ✅ | Secret-like env placeholders render as operator-managed `secretKeyRef`; k3s warning baseline is now 11 warnings + 0 blockers |
| M7.D.14 | Kubernetes securityContext remediation | ✅ | `seccomp=unconfined`, `cap_add`, and numeric `group_add` render as securityContext; k3s baseline is now 10 warnings + 0 blockers |
| M7.D.15 | Kubernetes AMD GPU group policy | ✅ | `video/render` Docker groups are covered by AMD GPU device-plugin resource mapping; k3s baseline is now 7 warnings + 0 blockers |
| M7.D.16 | Kubernetes empty env defaults | ✅ | Empty descriptor env defaults render as empty strings while unsafe command placeholders remain warnings; k3s baseline is now 5 warnings + 0 blockers |
| M7.D.17 | Kubernetes warning policy | ✅ | Target-declared expected warning codes let strict render checks allow AMD GPU prerequisite and Portainer omission debt while failing on `command-interpolation=1` |
| M7.D.18 | Kubernetes rerank omission | ✅ | Empty rerank model file omits `llama-rerank` from k3s with explicit warning; strict render-check now passes at 34 objects + 4 warnings + 0 blockers |
| M7.D.19 | Kubernetes dry-run metadata | ✅ | `summary.json` records invocation metadata so skipped and real proof bundles are self-describing |
| M7.D.20 | Kubernetes dry-run target selection | ✅ | `--target k3s` scopes proof runs and records effective `target_ids` in `summary.json` |
| M7.D.21 | Kubernetes dry-run warning details | ✅ | Dry-run artifacts include actionable warning records with expected-policy markers |
| M7.D.22 | Kubernetes dry-run artifact checksums | ✅ | Target reports include manifest digest metadata and `checksums.txt` covers persisted evidence files |
| M7.D.23 | Kubernetes dry-run artifact verifier | ✅ | `--verify-artifact-dir` validates copied/uploaded dry-run bundles against checksums and manifest metadata |
| M7.D.24.A | Kubernetes proof artifact contract | ✅ | k3s proof command writes/verifies `local-kubernetes-proof/k3s`; deploy-target gate rejects incomplete proof bundle contracts |
| M7.D.24.B-prep | Kubernetes proof CI artifacts | ✅ | Manual `kubernetes-proof` workflow runs k3s proof on a k3s-labeled self-hosted runner and uploads bundle artifacts |
| M7.D.24.C-prep | Kubernetes proof workflow drift guard | ✅ | `scripts/kubernetes_proof_workflow_check.py` keeps manual proof workflow aligned with the k3s target contract and aggregate governance |
| M7.D.24.D-prep | Kubernetes proof command artifact | ✅ | Proof bundles include checksum-verified `proof-command.txt` and the manual workflow uploads it with the evidence bundle |
| M7.D.24.E-prep | Kubernetes proof always-verify workflow | ✅ | Manual proof verifier runs with `if: always()` and the workflow drift guard enforces it |
| M7.D.24.F-prep | Kubernetes proof bundle diagnostics | ✅ | Manual proof workflow always lists produced bundle files and checksums before upload |
| M7.D.24.G-prep | Kubernetes proof verifier report | ✅ | Manual proof workflow uploads `verification.json` from the always-run verifier and preserves verifier exit status |
| M7.D.24.H-prep | Kubernetes proof verifier upload guard | ✅ | Workflow drift guard verifies `verification.json` is present in the upload-artifact step, not only in the verifier command |
| M7.D.24.I-prep | Kubernetes proof upload-scoped artifact guard | ✅ | Workflow drift guard verifies every target-declared proof artifact in the upload-artifact step |
| M7.D.24.J-prep | Kubernetes proof run metadata | ✅ | Proof bundles include checksum-verified `run-metadata.json` with allowlisted GitHub/runner provenance |
| M7.D.24.K-prep | Kubernetes proof required checksum coverage | ✅ | Verifier rejects required proof files that are missing from `checksums.txt` |
| M7.D.24.L-prep | Kubernetes proof required artifact presence | ✅ | Verifier rejects summary-declared proof files that are missing entirely |
| M7.D.24.M-prep | Kubernetes proof checksum path containment | ✅ | Verifier rejects absolute or parent-traversal paths in `checksums.txt` |
| M7.D.24.N-prep | Kubernetes proof command consistency | ✅ | Verifier rejects `proof-command.txt` drift from `summary.json::proof_command` |
| M7.D.24.O-prep | Kubernetes proof target report consistency | ✅ | Verifier rejects `<target>.dry-run.json` drift from `summary.json` target evidence |
| M7.D.24.P-prep | Kubernetes proof summary consistency | ✅ | Verifier rejects `summary.json::ok` drift from target statuses |
| M7.D.24.Q-prep | Kubernetes proof target ids consistency | ✅ | Verifier rejects `summary.json::target_ids` drift from target records |
| M7.D.24.R-prep | Kubernetes proof command target consistency | ✅ | Verifier rejects `summary.json::proof_command --target` drift from `target_ids` |
| M7.D.24.S-prep | Kubernetes proof command require_cluster consistency | ✅ | Verifier rejects missing `--require-cluster` in `proof_command` when summary requires cluster |
| M7.D.24.B | Kubernetes real cluster evidence | 🔵 | Execute manual proof workflow against k3s, capture uploaded server-side dry-run output, then RKE2/Talos enterprise later |

---

## Historical backlog below

The sections below are kept for traceability. Many M2/M3/M5 items are already
shipped in git history; use the live queue above for current work.

## M2 — Remaining (rolled into M3)

These were originally scoped в M2 но rolled to M3:

| # | Task | Effort | Priority | Notes |
|---|------|:------:|----------|-------|
| M2.K.1 | Grafana dashboards auto-provision (datasources + 3 dashboards: system / llama / services) | 4h | 🟡 | configs готовы (Phase H'.D), JSON dashboards не написаны |
| M2.K.2 | Prometheus alert rules tuning (CPU/RAM/GPU/disk thresholds) | 2h | 🟡 | skeleton есть, thresholds default |
| M2.U.1 | Ansible cluster role smoke test (1 host playbook --check) | 2h | 🟡 | playbook есть, не run'нен |

---

## M3 v0.3.0 — UX + ops polish (next sprint)

### M3.P.fix — version_check tag filtering (~1h, 🔴 high)

Weekly Phase P report сейчас шумный — много false positives. Fix перед
next Monday cron run.

| # | Task | Effort |
|---|------|:------:|
| P.fix.1 | Variant tag filter regex (`-windowsservercore`, `-arm64`, `-ubuntu`, `-alpine`, `-distroless`) | 15 min |
| P.fix.2 | RC/dev/nightly drop (`-rc`, `-dev-`, `+security-`, дата-based) | 15 min |
| P.fix.3 | SHA-only tags filter (40-char hex без semver prefix) | 10 min |
| P.fix.4 | Quay.io probe (`quay.io/<org>/<image>` через v2 API) | 10 min |
| P.fix.5 | GCR probe (`gcr.io/<project>/<image>`) | 10 min |
| P.fix.6 | Re-test live + verify signal-to-noise ratio | 10 min |

**DoD:** weekly report shows только real bumps; "❌ error" < 5.

### M3.Q — `agmind models {list,pull,rm,info}` (~2h, 🟡 medium)

Standalone CLI для управления GGUF files (без `agmind install`).

| # | Task | Effort |
|---|------|:------:|
| Q.1 | `agmind models list` — local *.gguf + size + last-used | 20 min |
| Q.2 | `agmind models pull <id>` — выбор из CURATED_MODELS | 30 min |
| Q.3 | `agmind models pull --repo X --file Y` — custom HF | 20 min |
| Q.4 | `agmind models rm <id>` — delete + warn если в use | 20 min |
| Q.5 | `agmind models info <id>` — size + quant + params + ctx | 15 min |
| Q.6 | Tests (mock HF + filesystem) | 30 min |

**Reuse:** Phase N.H detect/reuse + Phase N.G CURATED_MODELS catalog.

### M3.R — `agmind upgrade --component X` (~2h, 🟡 medium)

Bump single image pin + redeploy с rollback safety.

| # | Task | Effort |
|---|------|:------:|
| R.1 | `agmind upgrade --check` — synonym для version_check | 10 min |
| R.2 | `agmind upgrade --component X --version Y` — edit YAML + auto-resolve digest | 40 min |
| R.3 | `agmind upgrade --apply` — re-deploy after bump (reuse Phase L.B runner) | 30 min |
| R.4 | `agmind upgrade --rollback` — revert + redeploy snapshot | 20 min |
| R.5 | Respect version_holds.yaml (refuse без --force) | 10 min |
| R.6 | Tests | 20 min |

### M3.S.1 — TUI feedback polish (~2h, 🟢 UX)

| # | Task | Effort |
|---|------|:------:|
| S.1.1 | Replace `#status-msg` Static с `self.notify(...)` Toast | 30 min |
| S.1.2 | Inline domain Input validator (red border до fix) | 30 min |
| S.1.3 | Inline CF token validator (length check live) | 20 min |
| S.1.4 | Modal ConfirmScreen для Apply (destructive guard) | 30 min |
| S.1.5 | ProgressBar(show_eta=True) в Install screen | 10 min |

### M3.S.2 — Multi-step wizard split (~4h, 🟢 UX)

| # | Task | Effort |
|---|------|:------:|
| S.2.1 | `DomainScreen` extract (domain + CF token + inline validation) | 60 min |
| S.2.2 | `ModelScreen` extract (curated/custom + ctx/kv/threads/parallel) | 60 min |
| S.2.3 | `ServicesScreen` extract (per-tier checkboxes) | 30 min |
| S.2.4 | `ConfirmScreen` (summary + Apply / Back) | 30 min |
| S.2.5 | Navigation flow + Tab keybinding + back-button-restores-state | 30 min |
| S.2.6 | Persist partial state ~/.local/share/agmind/setup-state.json | 20 min |
| S.2.7 | Update tests для new screen flow | 30 min |

### M3.T — i18n hookup (~1.5h, 🔵 low)

| # | Task | Effort |
|---|------|:------:|
| T.1 | Auto-detect через LANG env var | 10 min |
| T.2 | `--lang en/ru` CLI flag | 10 min |
| T.3 | Wrap all user-facing strings через i18n.get() | 60 min |
| T.4 | Update en.json + ru.json (cover все wizard strings) | 30 min |
| T.5 | Test LANG=ru_RU agmind setup → русский UI | 10 min |

**M3 total estimate:** ~12.5h. Можно split на 2-3 sessions.

---

## M5 v0.5.0 — Model selectors split + TUI polish round 2 ✅ SHIPPED 2026-05-21

User feedback 2026-05-21 (контекст compaction approaching):
"очень тупая логика — ты в одно окно выбора модели уебал и embedding;
а где rerank? и настройки тоже отдельные. + внешний вид всё ещё очко."

### M5.1 — Split model selector на 3 secции (LLM + Embed + Rerank)

Сейчас wizard's `ModelScreen` имеет ОДИН `model-select` для всех типов
моделей — но CURATED_MODELS уже содержит `kind="llm"|"embed"|"rerank"`.
User видит embed-модели вместе с LLM в одном dropdown.

| # | Task | Effort |
|---|------|:------:|
| M5.1.1 | Filter `models_for_wizard()` по kind — return 3 separate lists | 20 min |
| M5.1.2 | SetupState: + `embed_model_id`, `embed_repo`, `embed_file`; + `rerank_model_id`, `rerank_repo`, `rerank_file` | 15 min |
| M5.1.3 | ModelScreen split на три blocked sections: LLM / Embed / Rerank, каждая с свой curated + "Custom HF" + ctx (только LLM) | 1.5h |
| M5.1.4 | InstallConfig + steps.ModelDownloadStep: pull all три модели (sequential) | 30 min |
| M5.1.5 | llama-embed.yaml / llama-rerank.yaml templates параметризовать через AGMIND_EMBED_FILE / AGMIND_RERANK_FILE | 30 min |
| M5.1.6 | Tests: per-section catalog filtering + 3-model download | 30 min |

### M5.2 — Per-service inference settings

Сейчас AGMIND_CTX_SIZE / KV_CACHE / THREADS / PARALLEL применяются ко
**всем** llama-* services. Реально:
- LLM сервер — ctx 16K-256K, KV q8_0, parallel 1+
- Embed сервер — ctx обычно 8K (max), KV f16 (короткие inputs), parallel высокий
- Rerank сервер — ctx 512-2048, KV f16

| # | Task | Effort |
|---|------|:------:|
| M5.2.1 | SetupState добавить `embed_ctx_size`, `embed_kv_cache`, `embed_parallel`, `rerank_ctx_size` | 15 min |
| M5.2.2 | EnvWriteStep пишет AGMIND_LLM_CTX_SIZE / AGMIND_EMBED_CTX_SIZE / AGMIND_RERANK_CTX_SIZE (renamed) | 30 min |
| M5.2.3 | templates/services/llama-{embed,rerank}.yaml: command stanza с separate env vars | 30 min |

### M5.3 — TUI polish round 2 ("внешний вид всё ещё очко")

Concrete refinements (after M4.7.1-4 already shipped):

| # | Task | Approach |
|---|------|----------|
| M5.3.1 | Textual `Rule` widget для visual separators между form sections | Replace empty Static с Rule(line_style="heavy", color="$pip-faint") |
| M5.3.2 | Detected hardware: full-width Panel вверху wizard (не сейчас "одна строка dim в углу") | Use `Panel` + ASCII art-table layout |
| M5.3.3 | Field hint inline label-side (Tooltip widget) | f"Domain    [dim](TLS, subdomain recommended)[/dim]" |
| M5.3.4 | Empty-state визуально явный — Services screen если 0 selected | Show "[ NO SERVICES SELECTED — PRESS SPACE TO CHECK ]" banner |
| M5.3.5 | Color-coded SetupState diff в ConfirmScreen — changed fields в amber | Compare initial vs final state |
| M5.3.6 | Animated progress bar в InstallProgressScreen — current step pulse | Textual reactive interval @ 200ms |
| M5.3.7 | Help overlay (F1 keybinding) — модальный screen с full keymap | New HelpScreen pushed on F1 |
| M5.3.8 | TabbedContent или Pages для config groups (alternative к multi-step) | Investigate `from textual.widgets import TabbedContent` |

### M5.4 — agmind cluster TUI integration

User уже подключил второй LAN node. Сейчас `agmind cluster detect` есть.
Wizard ServicesScreen НЕ показывает «cluster peers — deploy to all?».

| # | Task |
|---|------|
| M5.4.1 | DomainScreen — после CF token block добавить «Cluster peers detected (N)» auto-discover banner |
| M5.4.2 | Checkbox «Deploy on this node only / Replicate to peers» |
| M5.4.3 | Ansible inventory generation если "replicate" + N peers |

**M5 total estimate:** ~7h split на 3 sub-milestones (model split / settings / TUI polish).

## M4 v0.4.0 — Cluster + plugins (deferred)

### M4.U — Phase M cluster (multi-node)

| # | Task | Effort | Notes |
|---|------|:------:|-------|
| U.1 | Ansible cluster inventory parser (2+ hosts) | 4h | role skeleton есть |
| U.2 | Inter-node WireGuard (AmneziaWG для РФ per user feedback) | 4h | TBD |
| U.3 | mDNS endpoints advertise per node | 2h | legacy *.local pattern |
| U.4 | Cluster-aware deploy в Phase L.B runner | 4h | parallel apply per node |
| U.5 | `agmind status --tui` показывает все nodes | 2h | dashboard cluster mode |

### M4.V — Plugin marketplace

| # | Task | Effort | Notes |
|---|------|:------:|-------|
| V.1 | `agmind plugin list` (от agmind.dev/plugins TBD endpoint) | 2h | endpoint TBD |
| V.2 | `agmind plugin install <id>` (download + verify + register) | 4h | |
| V.3 | Plugin metadata schema (similar to ServiceDescriptor) | 2h | |
| V.4 | Sample plugins (e.g. authelia-2fa, gpu-monitor) | 4h | |

### M4.W — Authelia 2FA + Authentication

| # | Task | Effort | Notes |
|---|------|:------:|-------|
| W.1 | TUI wizard Authelia toggle (currently service есть но wizard не запрашивает) | 30 min | |
| W.2 | Auto-provision Authelia config (users.yml + access rules) | 2h | template есть |
| W.3 | TOTP secret generation + QR code в SummaryScreen | 1h | |

---

## Known defects (DEF-*)

Resolved (M2 session 2026-05-20):
- ✅ DEF-AUDIT-FIXTURE-TESTS (resolved `3dda542`)
- ✅ DEF-AUDIT-GITIGNORE (resolved `8a6c621`)
- ✅ DEF-VULKAN-MULTI-GPU-PARSE (resolved `3dda542`)
- ✅ DEF-ROCM-VERSION-GFX1151 (resolved earlier session)
- ✅ DEF-DOCKERFILE-DIGESTS (resolved earlier session)

Open:
- 🟡 DEF-PYTEST9-CAPLOG — test_logger_emits_to_configured_stream — caplog
  empty в pytest 9.0.3 (root logger propagation change). Workaround: 1
  test skipped. Fix: переписать через propagate=True или pin pytest<9.

---

## Long-term wishlist (M5 / GA)

- **PERF** — XDNA 2 NPU support (когда Linux driver появится)
- **PERF** — Async LLM serving (vLLM ROCm когда gfx1151 supported)
- **OBS** — OpenTelemetry traces (`agmind/observability/` placeholder есть)
- **DOCS** — Full user manual + tutorial videos
- **SEC** — mTLS между services + ansible-vault для secrets
- **SEC** — RBAC в `agmind` CLI (multi-user host)
- **CLUSTER** — Auto-failover между nodes если primary падает
- **MODELS** — Auto-detect best model для hardware (memory budget aware)

---

## Session notes / reminders

- **Tip commit:** `1e4923e` on develop branch
- **GitHub remote:** botAGI/AGmind64
- **Daily commits convention:** conventional (`feat:` / `fix:` / `docs:`)
- **Branch policy:** auto-push to develop, main требует confirmation
- **Verify before commit:** `pytest -q && python3 scripts/audit_forbidden.py`
- **PR convention:** small conventional PRs aligned to `.planning/BACKLOG.md`
  slices; migration spec is historical only.
