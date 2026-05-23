# AGmind Codebase Index

Last updated: 2026-05-23, after the self-hosted CI repair and Claude artifact cleanup.

This directory is the compact codebase map for the active GSD loop. Read this
file first, then open the topic-specific files when changing a subsystem.

## Current Snapshot

| Area | Current shape |
|------|---------------|
| Python package | `agmind/`, 79 Python files, about 14.5k LOC |
| Tests | `tests/`, 46 Python files, about 8.2k LOC |
| Service descriptors | `templates/services/`, 33 YAML descriptors |
| Ansible roles | `ansible/roles/`, 11 roles |
| Docker backend images | `docker/Dockerfile.base`, `cpu`, `vulkan`, `rocm` |
| CI workflows | `.github/workflows/ci.yml`, `release-drafter.yml`, `version-check.yml` |
| ADRs | `docs/adr/0000` through `0012`, 13 markdown files |
| Planning memory | `.planning/STATE.md`, `ROADMAP.md`, `BACKLOG.md`, `sessions/`, `codebase/`, `research/` |

## Directory Map

| Path | Purpose | Notes |
|------|---------|-------|
| `agmind/cli/` | Typer CLI and Textual TUI | Commands are thin wrappers over domain modules. TUI is setup/install/status/deploy oriented. |
| `agmind/compute/` | Backend abstraction and runtime backend selection | Built-ins: CPU, Vulkan, ROCm, NPU stub. Backends are discovered via `agmind.backends` entry points. |
| `agmind/services/` | Service descriptor loading, compose rendering, capability graph | `templates/services/*.yaml` is the source of truth. |
| `agmind/schemas/` | Pydantic service schema | JSON schema exported to `templates/schemas/service.json`. |
| `agmind/install/` | End-to-end install orchestrator | Calls doctor/bootstrap/model/env/deploy steps. |
| `agmind/deploy/` | Render/apply/snapshot/rollback/gc | Day-2 deployment workflows. |
| `agmind/ops/` | Backup/restore/logs/exec helpers | Wraps Docker Compose operations. |
| `agmind/cluster/` | Peer detection, inventory, routing | Local node is master; peers are llama workers. |
| `agmind/diagnostics/` | `agmind doctor` | Host/kernel/GPU/Docker/service checks. |
| `templates/services/` | Capability-aware service catalog | Core/RAG/RAGFlow/UI/observability/security profiles. |
| `templates/observability/` | Prometheus, Loki, Grafana, Alloy, alert configs | Dashboard JSON remains a backlog item. |
| `ansible/` | Host bootstrap and install playbook | Python orchestrator invokes Ansible for privileged host work. |
| `docker/` | Runtime images for backend lanes | CI builds base, CPU, Vulkan, ROCm on the self-hosted runner. |
| `scripts/` | Audits, upstream checks, metrics textfile scripts | `audit_forbidden.py` is a merge gate. |
| `.github/workflows/` | Self-hosted CI and scheduled checks | `ci.yml` avoids `actions/setup-python` because the runner has system Python and `uv`. |
| `.planning/` | GSD memory | Claude-specific live config is removed; `.planning/` is the durable handoff layer. |

## Green Baseline

The latest known full GitHub CI success is run `26297545718` on commit
`33a2050`:

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
