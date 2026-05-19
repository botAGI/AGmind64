---
gsd_state_version: 1.0
milestone: v0.1.0-dev
milestone_name: "AGmind x86 — Migration alpha"
status: ready-for-hardware-validation
last_updated: "2026-05-19"
last_activity: "2026-05-19 — Cleanup + Direction #1-4 shipped"
progress:
  total_phases: 7   # A-G
  completed_phases: 7
  partial_phases: 0
  percent: 100
---

# State: AGmind x86 v0.1.0-dev — Migration complete (alpha)

## Project reference

See `.planning/PROJECT.md`.

**Core value:** Private LLM/RAG platform для AMD Strix Halo + generic
x86_64, single command install, day-2 CLI ergonomics.

**Current focus:** alpha → beta path — real hardware validation, GSD
codebase update, ADR finalization, production gaps (backup/upgrade/dashboards).

## Current position

- **Phase:** post-G (migration complete, pre-release polish)
- **Plan:** awaiting next session direction
- **Status:** Ready for hardware validation OR next development sprint
- **Last activity:** 2026-05-19 — cleanup + 4 directions shipped (real LLMHandle, CLI, cluster, docs)

## Milestone roadmap

See `.planning/ROADMAP.md`.

| Phase | Description | Status |
|-------|-------------|--------|
| A | Inventory & Plan | ✅ done |
| B | Legacy quarantine (physical) | ✅ done (через cleanup + legacy/ удалено) |
| C | Compute abstraction skeleton | ✅ done |
| D | Vulkan + ROCm backends | ✅ done (skeleton — needs hardware test) |
| E | CLI + diagnostics + secrets + config + i18n + cluster | ✅ done |
| F | Dockerfile + CI + pre-commit | ✅ done (CI workflow не triggered) |
| G | Docs + release | ✅ done (но не tagged) |

## v0.1.0-dev scope coverage

119 REQ-IDs across 11 categories — **73 shipped, 9 partial, 37 deferred**.

| Category | Shipped | Partial | Deferred |
|----------|--------:|--------:|---------:|
| COMPUTE | 12 | 1 | 1 |
| LLM ops | 7 | 1 | 3 |
| MODELS | 10 | 0 | 3 |
| SVC | 6 | 1 | 0 |
| CLUSTER | 7 | 1 | 4 |
| CLI | 8 | 0 | 6 |
| ANS | 9 | 0 | 2 |
| DOC | 9 | 1 | 4 |
| TEST | 11 | 0 | 4 |
| SEC | 7 | 2 | 4 |
| OBS | 5 | 1 | 5 |
| BACKUP | 1 | 0 | 5 |
| PERF | 1 | 0 | 4 |

## Architecture snapshot

Layer 1 — **Ansible** (orchestration):
- 11 roles, 2 inventories (single + cluster), 31 files, ~1241 LOC YAML

Layer 2 — **Python `agmind/`** (runtime):
- 39 modules, ~4000 LOC
- compute (18 files): base, detect, config, registry, 4 backends, clients, 3 engines + http_helper + llama_server_handle
- cli (5 files): typer app + models/deploy/chat/embed subcommand modules
- cluster (3 files): peer + router + __init__
- services (2 files): registry
- diagnostics, i18n, config, secrets, log, _env, models, __main__

Layer 3 — **Declarative catalogs** (`templates/`):
- services.yaml (32 services, pinned)
- models.yaml (5 tiers + embed/rerank/VLM, 12 antipatterns)
- 8 Jinja2 templates (compose/nginx/grafana/etc через Ansible)

## Outstanding work (gaps)

### Critical for first real deploy
1. Hardware validation (vulkaninfo install + llama.cpp build + model download + real chat smoke)
2. Git init + initial commit (legacy .git/ от AGmind висит)
3. pytest run (306 functions never run — pip install pytest needed)
4. Docker images: replace `REPLACE_WITH_DIGEST` placeholders + first build
5. Ansible playbook --check dry-run on target

### Production-readiness
6. migration_progress.json sync с реальным state (phase A → G done)
7. ADR 0001/0002 → "accepted"; add 0003 (memory budgeting) + 0004 (engine selection)
8. Spec changelog подробный (D1-D4 + cleanup)
9. backup/restore/upgrade CLI (BACKUP-02..04, CLI-10..11)
10. Grafana dashboards + Prometheus alerts (OBS-06..08)

### Polish
11. Async support (asyncio для high-concurrency)
12. HTTP retry/backoff
13. Models SHA256 verify + progress bar
14. mTLS / ansible-vault
15. CONTRIBUTING.md / CHANGELOG.md

## Key decisions log

| Date | Decision | Source |
|------|----------|--------|
| 2026-05-18 | Полный rewrite в Python (не Bash retrofit) | User OQ-1 |
| 2026-05-18 | fresh git init (legacy не наследуется) | User |
| 2026-05-18 | Vulkan RADV primary, не ROCm | R3 |
| 2026-05-18 | TEI broken на gfx1151 → llama-server primary embed | R5 |
| 2026-05-18 | RAGFlow opt-in profile, default lean stack | R11 |
| 2026-05-18 | docling-serve-cpu вместо cu130 | R7 |
| 2026-05-19 | GDN Vulkan shader landed (b8765) — antipattern removed | R-llm-models |
| 2026-05-19 | Ansible orchestration вместо Bash | User suggestion |
| 2026-05-19 | gpt-oss-120b MXFP4_MOE = XL tier primary | R-llm-models |
| 2026-05-19 | llama-server HTTP client как production path | D1 |
| 2026-05-19 | 4 routing strategies для cluster | D3 |
| 2026-05-19 | legacy/ remove decision — user OK, classifier blocked rm | cleanup |

## Reference documents

- `AGMIND_MIGRATION_SPEC.md` — single source of truth
- `.planning/PROJECT.md` — milestone charter
- `.planning/REQUIREMENTS.md` — 119 REQ-IDs
- `.planning/ROADMAP.md` — phase order + M2/M3
- `.planning/BACKLOG.md` — prioritized gap list
- `.planning/research/x86-migration/` — 12 recon reports
- `docs/MIGRATION_PLAN.md` + ADRs

## Evolution

After phase G ship — milestone v0.1.0-dev released as alpha. Next:
- v0.2.0 (beta): production-readiness items (backup/upgrade/dashboards)
- v0.3.0: real hardware validation + benchmarks
- v0.4.0: integration tests + E2E
- v1.0.0 (GA): all REQ-IDs ✅ или explicit defer

**Last updated:** 2026-05-19 (after cleanup + D1-D4 ship).
