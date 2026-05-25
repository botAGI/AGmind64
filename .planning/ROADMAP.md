# AGmind x86 — Roadmap

**Current milestone:** v0.6.0 candidate — post-M5 hardening and release
confidence.
**Current gate:** M7 local governance checkpoint verified; next external
evidence is self-hosted CI on the M7 batch plus real install/Proxmox smoke.
**Target stable:** v1.0.0 after real E2E + cluster smoke + docs.

## Milestone overview

| Milestone | Status | Phases |
|-----------|--------|--------|
| v0.1.0-dev (M1) — Migration alpha | shipped 2026-05-19 | A B C D E F G |
| v0.2.0 (M2) — Production hardening | shipped 2026-05-20 | H' L J.2 H N O P |
| v0.3.0 (M3) — UX + ops polish | shipped 2026-05-20 | P.fix Q R S.1 S.2 T |
| v0.4.x (M4) — Cluster + UX wave | shipped 2026-05-21 | M4.1, U.1, M4.2-M4.7 |
| v0.5.0 (M5) — Model split + TUI polish round 2 | shipped 2026-05-21 | M5.1-M5.4 |
| v0.6.0 (M6) — Hardening + E2E confidence | current | S0 A B C D E |
| v1.0.0 (GA) | TBD | production soak + docs + zero P0/P1 |

---

## GSD gates

Each phase should have:

- clear scope,
- executable verification,
- session note or ROADMAP/BACKLOG update,
- no unrelated refactors unless explicitly scoped.

Current verification baseline:

```bash
.venv/bin/python -m pytest -q
.venv/bin/python scripts/audit_forbidden.py
.venv/bin/python -m agmind doctor
```

Latest observed result, 2026-05-23:

- GitHub Actions run `26333245295` on `d21294f`: all CI jobs green, including
  Docker cpu/vulkan/rocm and Strix Halo runtime smoke vulkan/rocm. Strix smoke
  now explicitly waits for the Docker build matrix.
- Local dev-only parity: 882 passed, 4 deselected for
  `backend_any or backend_cpu`.
- Doctor: 7 ok / 2 warn / 0 fail.

Latest local M7 verification, 2026-05-24:

- `git diff --check`, `uv pip check`, forbidden audit, aggregate governance,
  offline version report generation, focused 133-test pytest set, and full
  `pre-commit --all-files` all pass.
- External evidence still needed: self-hosted GitHub Actions on the M7 batch,
  real Proxmox exporter API-token smoke, OpenTofu validation against a Proxmox
  endpoint, and M6.C real install E2E.
- M7.D.1 started after that checkpoint: `agmind render kubernetes` now provides
  a research-grade plain-manifest MVP for k3s inspection, with warnings/strict
  mode for Docker-only descriptor fields. Real `kubectl --dry-run=server`
  remains external evidence.
- M7.D.2 added `scripts/kubernetes_render_check.py`, a self-hosted
  `kubernetes-render-validate` job, and aggregate governance coverage for the
  research Kubernetes renderer. Governance now has five local checks.
- M7.D.3 made Kubernetes portability warnings actionable: the current k3s
  render reports 5 blocker warnings and 22 warning-level items with stable
  codes and remediation hints.
- M7.D.4 added promotion policy: blocker warnings are acceptable only for
  `research` Kubernetes targets. `experimental` and `supported` targets fail
  default local render validation while blockers remain.
- M7.D.5 made those findings operator-actionable: JSON render reports now
  include service-level warning records with code/severity/message/remediation,
  and text reports include compact blocker-code breakdowns for triage before
  real `kubectl --dry-run=server` evidence. Local final verification passed:
  153 M7-focused tests, aggregate governance, `git diff --check`, and full
  pre-commit.
- M7.D.6 added the proof harness for that external evidence:
  `scripts/kubernetes_dry_run.py` renders the k3s target and runs
  `kubectl apply --dry-run=server` when kubectl/cluster access exists. In local
  dev without kubectl it reports `SKIPPED`; `--require-cluster` turns skipped
  evidence into a failing gate for real proof runs. Local verification passed:
  159 M7-focused tests, deploy target/governance checks, `git diff --check`,
  and full pre-commit.
- M7.D.7 added artifact capture for those proof runs. `--artifact-dir` now
  writes the rendered target manifest, per-target dry-run JSON, and
  `summary.json`; skipped local runs still preserve the manifest so reviewers
  can inspect the exact input that would be sent to a real cluster. Local
  verification passed: 161 M7-focused tests, aggregate governance,
  `git diff --check`, and full pre-commit.
- M7.D.8 started blocker remediation. Traefik keeps its Docker Compose
  descriptor unchanged, but the Kubernetes renderer now omits the Docker socket
  mount and rewrites Docker provider args to `--providers.kubernetesingress=true`.
  Current k3s render reports 26 warnings: 4 blockers and 22 warnings. Local
  verification passed: 162 M7-focused tests, aggregate governance,
  `git diff --check`, and full pre-commit.
- M7.D.9 removed the final Docker-socket blocker from Kubernetes output by
  omitting the current Docker-socket-based Portainer descriptor with an explicit
  `kubernetes-omitted` warning. Current k3s render reports 36 objects and 26
  warnings: 3 blockers, all `docker-device`, plus 23 warnings. Local
  verification passed: 163 M7-focused tests, aggregate governance,
  `git diff --check`, and focused ruff/mypy checks.
- M7.D.10 maps the known `/dev/dri` inference device to the Kubernetes extended
  resource `amd.com/gpu: "1"` and keeps unknown Docker devices as blockers.
  Current k3s render reports 36 objects and 26 warnings with 0 blockers. Local
  verification passed: 165 M7-focused tests, aggregate governance,
  `git diff --check`, and full pre-commit.
- M7.D.11 added real-cluster GPU proof plumbing: `scripts/kubernetes_dry_run.py
  --require-amd-gpu` records `kubectl get nodes -o json` allocatable
  `amd.com/gpu` evidence and fails a proof run when the resource is absent.
  The k3s target verification command now includes `--require-cluster
  --require-amd-gpu`. Local verification passed: 169 M7-focused tests,
  aggregate governance, focused ruff/mypy, and local skipped no-kubectl smoke.
- M7.D.12 reduced Kubernetes render warning debt by resolving explicit
  non-empty descriptor defaults such as `${VAR:-default}` inside Kubernetes env
  and command args without reading host env. Secrets, no-default placeholders,
  and empty defaults remain unresolved. Current k3s render reports 36 objects,
  15 warnings, and 0 blockers. Local focused verification passed: 25
  renderer/check tests plus focused ruff/mypy.
- M7.D.13 maps secret-like unresolved Kubernetes env values to
  `valueFrom.secretKeyRef` entries without changing Compose descriptors.
  Grafana, Postgres, Redis, and Postgres exporter no longer render raw secret
  placeholders. Current k3s render reports 36 objects, 11 warnings, and 0
  blockers. Local verification passed: 172 M7-focused tests, aggregate
  governance, `git diff --check`, and full pre-commit.
- M7.D.14 maps safe Docker security fields to Kubernetes `securityContext`:
  `seccomp=unconfined`, Linux capabilities, and numeric supplemental groups.
  Named `video`/`render` groups remain warning debt until a target-level GID
  policy exists. Current k3s render reports 36 objects, 10 warnings, and 0
  blockers. Local verification passed: 174 M7-focused tests, aggregate
  governance, `git diff --check`, and full pre-commit.
- M7.D.15 removes duplicate Docker GPU group warnings when `/dev/dri` has
  already been mapped to the Kubernetes AMD GPU device-plugin resource. The
  `amd-gpu-device-plugin` warning remains the cluster prerequisite. Current
  k3s render reports 36 objects, 7 warnings, and 0 blockers. Local
  verification passed: 175 M7-focused tests, aggregate governance,
  `git diff --check`, and full pre-commit.
- M7.D.16 resolves explicit empty descriptor env defaults such as
  `${AGMIND_ROPE_SCALING:-}` and `${AGMIND_RERANK_FILE:-}` to Kubernetes
  `value: ""` entries only in env rendering. Command interpolation still
  requires non-empty values, so `/models/${AGMIND_RERANK_FILE}` remains a
  warning until an operator supplies a model file or omits the service.
  Current k3s render reports 36 objects, 5 warnings, and 0 blockers. Local
  verification passed: 176 M7-focused tests, aggregate governance,
  `git diff --check`, and full pre-commit.
- M7.D.17 adds target-declared Kubernetes warning policy:
  `DeploymentVerification.expected_warning_codes` now lets `k3s` mark
  `amd-gpu-device-plugin` and `kubernetes-omitted` as expected research debt.
  Strict render validation now rejects only unexpected warnings, so the current
  strict gate fails on exactly `command-interpolation=1` while default
  governance remains 36 objects, 5 warnings, and 0 blockers. Local focused
  verification passed: 178 M7-focused tests, aggregate governance,
  `git diff --check`, and full pre-commit.
- M7.D.18 mirrors installer rerank semantics in Kubernetes: when
  `AGMIND_RERANK_FILE` is explicitly empty, `llama-rerank` is omitted from the
  k3s render with a `kubernetes-omitted` warning instead of rendering an
  invalid `/models/${AGMIND_RERANK_FILE}` command. Current strict and normal
  k3s render checks both report 34 objects, 4 warnings, and 0 blockers. Local
  verification passed: 179 M7-focused tests, aggregate governance,
  `git diff --check`, and full pre-commit.
- M7.D.19 makes Kubernetes dry-run evidence bundles self-describing before the
  real k3s proof. Aggregate `summary.json` now records `require_cluster`,
  `require_amd_gpu`, `kubectl`, `kube_context`, `namespace`, `artifact_dir`,
  and `summary_path`; local skipped evidence remains reviewable when `kubectl`
  and kubeconfig are unavailable. Local verification passed: 180 M7-focused
  tests, aggregate governance, strict render-check, and a skipped
  `--require-cluster --require-amd-gpu --artifact-dir` smoke that failed only
  because `kubectl` is unavailable.
- M7.D.20 adds explicit Kubernetes dry-run target selection. The proof harness
  accepts repeatable `--target <id>` flags, records effective `target_ids` in
  `summary.json`, rejects unknown or non-Kubernetes targets early, and updates
  the `k3s` verification command to
  `scripts/kubernetes_dry_run.py --target k3s --require-cluster
  --require-amd-gpu`. Local verification passed: focused dry-run plus
  deploy-target tests, deploy target validation, and target-selection CLI
  smokes.
- M7.D.21 adds actionable warning details to Kubernetes dry-run evidence. The
  target report and aggregate `summary.json` now include warning records with
  `service`, `code`, `severity`, `message`, `remediation`, and `expected`,
  derived from target-declared expected warning codes. Skipped local `k3s`
  proof bundles now show the current four expected warning records inline.
- M7.D.22 adds checksum evidence to Kubernetes dry-run bundles. Target reports
  now record rendered manifest byte size and SHA256 digest; aggregate
  `summary.json` records `checksum_path`; and `checksums.txt` includes SHA256
  lines for persisted manifests, per-target reports, and `summary.json`.
- M7.D.23 adds a first-party verifier for copied or uploaded Kubernetes
  dry-run evidence bundles. `scripts/kubernetes_dry_run.py
  --verify-artifact-dir <dir>` checks `summary.json`, `checksums.txt`, listed
  artifact files, SHA256 digests, and per-target manifest byte/digest metadata
  in both text and JSON modes.
- M7.D.24.A strengthens the real k3s proof contract before external cluster
  execution. The `k3s` verification command now writes
  `local-kubernetes-proof/k3s`, declares the expected bundle artifacts, and
  includes a matching `--verify-artifact-dir` command. Deploy-target validation
  rejects Kubernetes `--require-cluster` proof commands that omit the target,
  artifact directory, verifier, or bundle artifact declarations.
- M7.D.24.B-prep adds a manual self-hosted CI path for producing that evidence.
  `.github/workflows/kubernetes-proof.yml` runs only via `workflow_dispatch` on
  `[self-hosted, linux, x64, k3s]`, performs strict render validation, runs the
  contract-defined k3s proof into `local-kubernetes-proof/k3s`, verifies the
  bundle, and uploads the declared proof artifacts.
- M7.D.24.C-prep adds a drift guard for that proof workflow.
  `scripts/kubernetes_proof_workflow_check.py` validates that the manual
  workflow stays aligned with the `k3s` deployment target proof contract, and
  the guard is wired into pre-commit, self-hosted CI, and aggregate governance
  as the sixth M7 check.
- M7.D.24.D-prep makes Kubernetes proof bundles self-describing:
  `proof-command.txt` stores the exact proof command, `summary.json` records
  the command/path, `checksums.txt` verifies it, and the manual workflow uploads
  it with the rest of the evidence.
- M7.D.24.E-prep makes failed proof attempts diagnosable: the manual workflow
  verifies the proof bundle with `if: always()`, and the drift guard rejects
  workflows where the verifier is not always-run guarded.
- M7.D.24.F-prep adds always-run proof bundle diagnostics before upload: the
  workflow lists produced proof files, prints `checksums.txt`, and the drift
  guard enforces that diagnostic step.
- M7.D.24.G-prep uploads a machine-readable verifier report:
  `verification.json` captures the always-run bundle verifier output while the
  workflow preserves the verifier exit status.
- M7.D.24.H-prep tightens that report contract: the drift guard verifies
  `verification.json` inside the upload-artifact step specifically.
- M7.D.24.I-prep generalizes that upload-scoped check to every
  target-declared proof artifact, so diagnostic references do not mask missing
  upload paths.
- M7.D.24.J-prep adds checksum-verified proof run provenance:
  `run-metadata.json` records allowlisted GitHub Actions and runner metadata,
  `summary.json` records the same payload/path, `checksums.txt` covers it, the
  artifact verifier rejects metadata drift, and the manual proof workflow
  uploads it through the k3s target contract.
- M7.D.24.K-prep tightens bundle integrity: the artifact verifier derives
  required proof members from `summary.json` and rejects bundles where required
  files exist but are missing from `checksums.txt`.
- M7.D.24.L-prep completes that required-member guard by rejecting
  summary-declared proof files that are missing entirely, even when their
  checksum entries are missing too.
- M7.D.24.M-prep blocks checksum path escapes: the artifact verifier rejects
  absolute paths and `..` traversal in `checksums.txt` before reading files.
- M7.D.24.N-prep blocks proof-command drift: the artifact verifier rejects
  bundles where `proof-command.txt` no longer matches
  `summary.json::proof_command`.
- M7.D.24.O-prep blocks target report drift: the artifact verifier rejects
  bundles where `<target>.dry-run.json` no longer matches the matching
  `summary.json` target evidence.
- M7.D.24.P-prep blocks summary ok drift: the artifact verifier rejects
  bundles where `summary.json::ok` no longer matches target statuses and
  `require_cluster` policy.
- M7.D.24.Q-prep blocks target_ids drift: the artifact verifier rejects
  bundles where `summary.json::target_ids` no longer matches the summary target
  records.
- M7.D.24.R-prep blocks proof_command target drift: the artifact verifier
  rejects bundles where `summary.json::proof_command --target` no longer
  matches `summary.json::target_ids`.
- M7.D.24.S-prep blocks proof_command require_cluster drift: the artifact
  verifier rejects bundles where `summary.json::require_cluster` is true but
  `summary.json::proof_command` omits `--require-cluster`.

---

## M6.S0 — Cloud-artifact Reconciliation (done)

**Outcome:** cloud/CI artifact layer was split into focused repair commits and
proved on the self-hosted runner through full CI and Strix Halo smoke.

| # | Task | DoD |
|---|------|-----|
| S0.1 | Inventory dirty files | done |
| S0.2 | Verify current behavior | done |
| S0.3 | Split mechanical vs semantic changes | done |
| S0.4 | Commit or defer groups | done |
| S0.5 | Refresh GSD docs | done in 2026-05-23 codebase cleanup |

**DoD:** worktree either clean or intentionally documented, and next phase can
start from a stable GSD checkpoint.

## M6.A — Planning + Codebase Refresh (done)

**Problem:** `.planning/codebase/*`, `PROJECT.md`, and some backlog sections
still describe M1-M3/M5 planning history rather than the current post-M5
codebase.

| # | Task | DoD |
|---|------|-----|
| A.1 | Refresh `.planning/codebase/INDEX.md` | done |
| A.2 | Refresh codebase architecture/deps/extensions/invariants/pitfalls | done |
| A.3 | Document agent tooling/plugins | done in `AGENT_TOOLING.md` |
| A.4 | Prune Claude live artifacts | done: `.claude/` and `CLAUDE.md` removed/ignored |
| A.5 | Refresh `PROJECT.md` and deeper backlog history | optional follow-up |

## M6.B — Tooling Gate Cleanup (standard gates done, runner-noise follow-up)

**Outcome:** standard CI gates are green on the self-hosted runner. Remaining
tooling issue is queue hygiene: Dependabot/release-drafter should not occupy
the only Strix runner ahead of required develop CI.

| # | Task | DoD |
|---|------|-----|
| B.1 | Decide lint policy | done |
| B.2 | Resolve or scope ruff/mypy/pre-commit drift | done |
| B.3 | Validate ansible-lint bump | done |
| B.4 | Document exact green commands | done in codebase docs/state |
| B.5 | Keep Dependabot/release-drafter off critical runner queue | follow-up |

## M6.C — Real Install E2E

**Problem:** install pipeline exists, but GA needs recorded evidence from a
real Strix Halo install path.

| # | Task | DoD |
|---|------|-----|
| C.1 | Dry-run install path | preflight + wizard state + rendered env/compose evidence |
| C.2 | Full single-node install | services start, healthchecks pass, rollback path known |
| C.3 | Model pull/reuse check | LLM/embed/rerank files resolve and are reused |
| C.4 | Record logs | session note + docs/TROUBLESHOOTING updates if needed |

## M6.D — Cluster Deploy Smoke

**Problem:** mDNS discovery and inventory generation exist; deploy replication
needs real or realistic smoke validation.

| # | Task | DoD |
|---|------|-----|
| D.1 | `agmind cluster detect/status` on LAN | peers found or failure mode documented |
| D.2 | Wizard replicate toggle writes inventory | generated inventory validates |
| D.3 | Ansible check-mode against inventory | no syntax/layout failures |
| D.4 | Status dashboard cluster story | current limitations documented |

## M6.E — GA Backlog Pruning

Choose only what matters before v1.0:

| Candidate | Status |
|-----------|--------|
| Grafana dashboards JSON provision | deferred from M2/M3 |
| Authelia 2FA wizard flow | deferred |
| Plugin marketplace | deferred |
| `agmind chat` against deployed `/v1/chat/completions` | small, high demo value |
| DeepDoc fork vs Dify/RAGFlow native path | recon exists in R18 |
| OpenTelemetry traces | wishlist |

## M7 candidate — Component contracts + universal deploy targets

**Status:** local implementation verified; component/deploy target contracts,
the first opt-in runtime candidate, and aggregate governance gates exist
locally. Self-hosted CI and real Proxmox smoke still need external evidence on
the resulting ref.
**References:**
- `.planning/research/homelab-enterprise/R20-component-boundaries-version-contracts.md`
- `.planning/research/homelab-enterprise/R19-universal-deploy-tooling.md`
- `docs/adr/0013-component-contracts-and-safe-updates.md`
- `docs/adr/0014-deploy-targets-and-provisioning-boundary.md`

Initial direction if selected:

- define component/version contracts before adding more tools as first-class
  features. `ComponentContract` v1 now has a schema, loader, JSON Schema export,
  and 9 baseline contracts under `templates/components/`;
- split service descriptors from stack-level version ownership: Dify,
  RAGFlow, edge proxy, observability, model catalog, and backend build planes
  each need their own core + recommended versions;
- expand upstream version checks to Python deps, Ansible Galaxy, Dockerfile
  pip installs, model catalog entries, and registry adapters. The report now
  includes component policies plus pyproject, Ansible Galaxy, Dockerfile pip,
  and constraint specs. Model catalog ownership is now YAML-backed via
  `templates/models.yaml::wizard_catalog`;
- split compatibility checks into capability warnings and deploy-level
  singleton conflicts such as host ports `80/443`. This split now exists in
  `agmind.components.checks`;
- make updates component-aware: `agmind upgrade --component <id>` now supports
  dry-run plans, grouped descriptor updates, grouped rollback state, and raw
  single-service fallback;
- gate the contract layer in automation: `scripts/component_check.py` now runs
  in pre-commit and self-hosted CI before CPU tests and compose validation;
- split Python/backend dependency governance into `constraints/core.txt`,
  `constraints/dev.txt`, `constraints/cpu.txt`, `constraints/vulkan.txt`, and
  `constraints/rocm-gfx1151.txt`; backend Dockerfiles now install through the
  matching constraint plane;
- make `templates/models.yaml` the canonical source for setup wizard curated
  model ids/defaults via `wizard_catalog`; `agmind.install.models` is now a
  compatibility facade over YAML-backed loaders. The curated defaults include
  LLM, embedding, and `bge-reranker-v2-m3-q8` rerank entries;
- define deploy targets separately from service descriptors:
  `agmind.deploy.targets` now loads `templates/deploy-targets/*.yaml` and
  exports `templates/schemas/deploy-target.json`;
- keep `ubuntu-compose` as the supported v1.0 path;
- `proxmox-vm-compose` now has the first OpenTofu root module skeleton under
  `infra/proxmox/vm-compose`: it provisions cloud-init VM shells and exposes
  inventory-oriented outputs while keeping Ansible and Compose boundaries
  intact;
- OpenTofu output can now be bridged into Ansible inventory via
  `scripts/proxmox_inventory.py`, preserving the current install playbook
  groups: `agmind_nodes`, `agmind_master`, and `agmind_workers`;
- validate the Proxmox lane next on a host with OpenTofu installed and a real
  Proxmox endpoint;
- keep optional tools behind an admission catalog before runtime inclusion:
  `agmind.addons` now loads `templates/tool-candidates/*.yaml` and validates
  candidate target references through `scripts/tool_candidate_check.py`;
- `proxmox-exporter` is the first promoted optional runtime descriptor. It is
  pinned to `prompve/prometheus-pve-exporter:3.9.0` by digest, lives only in
  the `proxmox` profile, is owned by `observability-stack`, and includes local
  token/scrape examples. Real Proxmox API-token smoke remains the next gate;
- keep `k3s` as a research target even though the first Kubernetes renderer
  MVP and local render governance exist. The renderer now resolves safe
  descriptor defaults in env/command fields, resolves explicit empty env
  defaults to empty Kubernetes env values, maps secret-like env values to
  operator-managed Secret refs, maps supported Docker security fields to
  Kubernetes securityContext, treats Docker `video`/`render` groups as covered
  by the AMD GPU device-plugin resource, and the local warning baseline is down
  to 4 non-blocking warnings. The target declares expected strict-mode warning
  codes for AMD GPU device-plugin prerequisites and Kubernetes omissions
  (Portainer plus unconfigured rerank), so local strict render validation now
  passes, and dry-run evidence bundles record invocation metadata plus selected
  target ids plus actionable warning records and checksum evidence that can be
  verified locally after copy/upload. The real proof command must now write
  `local-kubernetes-proof/k3s`, including checksum-verified
  `proof-command.txt`, and run the bundle verifier before review, even when
  the live proof step itself fails. The manual workflow also uploads
  `verification.json` with the verifier result. A
  manual `kubernetes-proof` workflow now exists for a k3s-labeled self-hosted
  runner to produce and upload the evidence, and a workflow drift guard keeps
  that workflow aligned with the target contract. Promote it only after real
  k3s server-side dry-run/apply evidence, AMD GPU device-plugin proof, and
  External Secrets/SOPS materialization;
  choose RKE2/Talos later for enterprise/hardened paths;
- keep ComfyUI, n8n, Keycloak, Vault/Infisical, Harbor, backup runners, and
  other tool candidates out of core orchestration. They must stay candidates
  until their image/version/license/port, storage, secrets, and ownership
  checks pass.

---

## Historical shipped phases

### M1 — Migration alpha

7 phases A-G complete. Outcome: Python rewrite skeleton, compute abstraction,
Ansible roles, descriptors, docs, and audit gate.

### M2 — Production hardening

Shipped:

- H' ServiceDescriptor + split descriptors + renderer + observability + plugins
- L day-2 ops: deploy/gc/migrate/logs/shell/backup/restore
- J.2 status TUI
- H real Strix Halo bench: Qwen3.6-35B-A3B Q4_K_M, tg128 ~73 t/s
- N end-to-end installer
- O service capability graph
- P upstream version check workflow

### M3 — UX + ops polish

Shipped:

- P.fix version check false-positive filtering
- Q `agmind models {list,pull,rm,info}`
- R `agmind upgrade`
- S.1 toast + inline validation
- S.2 multi-step wizard
- T wizard i18n

### M4 — Cluster + UX wave

Shipped:

- Multi-step wizard default
- mDNS cluster detect/advertise/status
- `agmind setup` alias
- i18n validators
- rich doctor output
- status TUI hotkeys
- model pull speed/ETA
- Fallout terminal theme and wizard polish M4.7.x

### M5 — Model split + TUI polish round 2

Shipped:

- LLM / Embed / Rerank model selector split
- per-service inference settings
- TUI polish round 2
- cluster peer banner and replicate toggle in wizard

## Phase dependency graph

```text
M1 migration
  -> M2 production hardening
  -> M3 UX + ops polish
  -> M4 cluster + UX wave
  -> M5 model split + TUI polish
  -> M6 hardening + E2E confidence
  -> GA
```
