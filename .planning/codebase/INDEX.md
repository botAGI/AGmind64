# AGmind Codebase Index

Last updated: 2026-05-25, after retiring legacy x86 migration artifacts.

This directory is the compact codebase map for the active GSD loop. Read this
file first, then open the topic-specific files when changing a subsystem.

## Current Snapshot

| Area | Current shape |
|------|---------------|
| Python package | `agmind/`, 105 Python files |
| Tests | `tests/`, 70 Python files |
| Service descriptors | `templates/services/`, 34 YAML descriptors |
| Component contracts | `templates/components/`, 9 YAML contracts |
| Deploy targets | `templates/deploy-targets/`, 3 YAML contracts |
| Tool candidates | `templates/tool-candidates/`, 11 YAML contracts |
| Infra modules | `infra/proxmox/vm-compose`, OpenTofu skeleton |
| Ansible roles | `ansible/roles/`, 11 roles |
| Docker backend images | `docker/Dockerfile.base`, `cpu`, `vulkan`, `rocm` |
| CI workflows | `.github/workflows/ci.yml`, `kubernetes-proof.yml`, `release-drafter.yml`, `version-check.yml` |
| ADRs | `docs/adr/0000` through `0014`, 15 markdown files |
| Planning memory | `.planning/STATE.md`, `ROADMAP.md`, `BACKLOG.md`, `sessions/`, `codebase/`, current `research/` |

## Directory Map

| Path | Purpose | Notes |
|------|---------|-------|
| `agmind/cli/` | Typer CLI and Textual TUI | Commands are thin wrappers over domain modules. Includes `agmind ci`, `agmind governance`, `agmind targets`, and `agmind tools`. |
| `agmind/ci/` | GitHub Actions runner monitor | Read-only `gh` wrapper for recent workflow runs and self-hosted runner online/busy state. |
| `agmind/addons/` | Optional tool candidates | Admission layer before service descriptors/components for homelab and enterprise tools; shared by script and CLI. |
| `agmind/compute/` | Backend abstraction and runtime backend selection | Built-ins: CPU, Vulkan, ROCm, NPU stub. Backends are discovered via `agmind.backends` entry points. |
| `agmind/components/` | Component contracts and deploy-level checks | Version ownership, update policy, service ownership, singleton port conflicts. |
| `agmind/services/` | Service descriptor loading, setup-time selection closure, compose/Kubernetes rendering, Kubernetes dry-run evidence, capability graph | `templates/services/*.yaml` is the source of truth. |
| `agmind/schemas/` | Pydantic service schema | JSON schema exported to `templates/schemas/service.json`. |
| `agmind/install/` | End-to-end install orchestrator | Calls doctor/bootstrap/model/env/deploy steps. |
| `agmind/deploy/` | Render/apply/snapshot/rollback/gc/targets | Day-2 deployment workflows, deployment target contracts, severity-aware target validation, and Kubernetes proof workflow drift checks. |
| `agmind/governance.py` | Aggregate governance gate | Runs component, deploy target, tool candidate, constraints, topology, Kubernetes render, and Kubernetes proof workflow checks together; JSON includes per-check payloads and a compact aggregate summary with warning/info/error totals, affected check names, health status labels, per-gate health rows, status distribution counts, and payload-error counts/check names, while text mode appends status and the same totals to the final operator line; no-payload failures, payload-level errors, non-zero process failures with incomplete payloads, and invalid JSON-capable payloads all fail the effective aggregate/result status while raw process status stays in `returncode`. |
| `agmind/ops/` | Backup/restore/logs/exec helpers | Wraps Docker Compose operations. |
| `agmind/cluster/` | Peer detection, environment inspect, inventory, routing | Recommends deploy target from Docker/k3s/Proxmox probes and mDNS peers, then enriches known targets from the deploy-target catalog. |
| `agmind/diagnostics/` | `agmind doctor` | Host/kernel/GPU/Docker/service checks. |
| `templates/services/` | Capability-aware service catalog | Core/RAG/RAGFlow/UI/observability/security plus opt-in `proxmox` profile. |
| `templates/components/` | Component/version contracts | First-class tool ownership and update policy. |
| `templates/deploy-targets/` | Deployment target contracts | `ubuntu-compose`, `proxmox-vm-compose`, `k3s`. |
| `templates/tool-candidates/` | Optional tool candidate contracts | ComfyUI, n8n, Keycloak, SOPS/age, Vault, Infisical, Harbor, restic/Kopia, Proxmox exporter. |
| `infra/proxmox/vm-compose/` | OpenTofu Proxmox VM skeleton | Cloud-init VM shell for the experimental Proxmox/Compose target. |
| `scripts/proxmox_inventory.py` | OpenTofu output bridge | Converts `tofu output -json` into local Ansible inventory. |
| `scripts/deploy_target_check.py` | Deploy target gate | Shared pre-commit/CI wrapper for `agmind.deploy.target_checks`; supports JSON output and requires Kubernetes `--require-cluster` proof commands to declare target, artifact dir, verifier, and bundle artifacts. |
| `scripts/tool_candidate_check.py` | Optional tool gate | Shared pre-commit/CI wrapper for `agmind.addons.checks`. |
| `scripts/governance_check.py` | Aggregate governance gate | Operator/pre-commit/CI summary wrapper for all M7 local gates; currently 7 checks including topology and Kubernetes proof workflow drift, with structured JSON payloads for every gate and text/JSON summary health counts plus affected gate names, status labels, per-gate health rows, status distribution counts, no-payload failure accounting, payload-error failure accounting, process-error fallback accounting, invalid structured-payload accounting, top-level payload-error classification, and consistent effective result `ok` semantics. |
| `scripts/kubernetes_proof_workflow_check.py` | Kubernetes proof workflow drift gate | Validates `.github/workflows/kubernetes-proof.yml` against the `k3s` deployment target proof contract, upload-scoped declared artifacts, always-run verifier step, verifier report artifact, and bundle diagnostics; supports JSON output. |
| `scripts/kubernetes_render_check.py` | Kubernetes render gate | Validates research Kubernetes targets render from current descriptors; current k3s baseline is 34 objects, 4 warnings, and 0 blockers, with strict mode passing against target-declared expected warning codes. |
| `scripts/kubernetes_dry_run.py` | Kubernetes server dry-run proof and artifact verifier | Runs `kubectl apply --dry-run=server` when kubectl/cluster access exists; `--target` scopes proof runs, `--require-amd-gpu` records allocatable `amd.com/gpu`, `--artifact-dir` writes manifest/report evidence plus invocation metadata, warning details, `proof-command.txt`, checksum-verified `run-metadata.json`, and checksums, and `--verify-artifact-dir` validates copied/uploaded bundles including required artifact presence, checksum coverage, checksum path containment, proof-command consistency, target report consistency, summary ok consistency, target_ids consistency, proof_command target consistency, and proof_command require_cluster consistency. |
| `templates/observability/` | Prometheus, Loki, Grafana, Alloy, alert configs | Includes Proxmox exporter examples; dashboard JSON remains a backlog item. |
| `ansible/` | Host bootstrap and install playbook | Python orchestrator invokes Ansible for privileged host work. |
| `docker/` | Runtime images for backend lanes | CI builds base, CPU, Vulkan, ROCm on the self-hosted runner. |
| `scripts/` | Audits, upstream checks, metrics textfile scripts | `audit_forbidden.py` is a merge gate. |
| `.github/workflows/` | Self-hosted CI, manual proof, and scheduled checks | `ci.yml` avoids `actions/setup-python` because the runner has system Python and `uv`; `kubernetes-proof.yml` is manual-only and requires a k3s-labeled self-hosted runner with kubeconfig. |
| `.planning/` | GSD memory | Claude-specific live config and old x86 migration archives are removed; `.planning/` is the durable handoff layer. |

## Green Baseline

The latest known full GitHub CI success is run `26333245295` on commit
`d21294f`:

- `pre-commit`
- `audit`
- `schema-validate`
- `compose-validate`
- `test-cpu`
- Docker build matrix: `cpu`, `vulkan`, `rocm`
- Strix Halo runtime smoke: `vulkan`, `rocm`

Local parity commands used during the repair:

```bash
$HOME/.local/bin/uv venv --python python3 /tmp/agmind-ci-system-python
$HOME/.local/bin/uv pip install --python /tmp/agmind-ci-system-python/bin/python -e '.[dev]'
/tmp/agmind-ci-system-python/bin/ruff check .
/tmp/agmind-ci-system-python/bin/ruff format --check .
/tmp/agmind-ci-system-python/bin/mypy agmind/
/tmp/agmind-ci-system-python/bin/pytest -q --cov=agmind --cov-branch --cov-report=xml:/tmp/agmind-ci-system-coverage.xml --cov-report=term -m 'backend_any or backend_cpu'
```

## Source Of Truth

Use these files when making changes:

- Project intent: `.planning/PROJECT.md`
- Current state: `.planning/STATE.md`
- Phase order: `.planning/ROADMAP.md`
- Work queue: `.planning/BACKLOG.md`
- Architecture map: `.planning/codebase/ARCHITECTURE.md`
- Dependency map: `.planning/codebase/DEPENDENCIES.md`
- Extension map: `.planning/codebase/EXTENSION_POINTS.md`
- Rules: `.planning/codebase/INVARIANTS.md`
- Known traps: `.planning/codebase/PITFALLS.md`
- Agent/tooling needs: `.planning/codebase/AGENT_TOOLING.md`

## Artifact Policy

Live Claude config has been removed from the repository:

- removed: `.claude/`
- removed: `CLAUDE.md`
- ignored going forward: `.claude/`, `CLAUDE.md`

Historical mentions of Claude inside `.planning/sessions/` and research notes
are retained only as migration context. Do not reintroduce active Claude config
as project state.

Legacy x86 migration artifacts were also retired from the active tree:

- removed: `AGMIND_MIGRATION_SPEC.md`
- removed: `docs/MIGRATION_PLAN.md`
- removed: `migration_progress.json`
- removed: `.planning/research/x86-migration/`
- removed: `scripts/migrate_services_to_descriptors.py`

Do not reintroduce those as active planning sources. Historical ADRs/session
logs may still mention them as past context.
