---
gsd_state_version: 1.2
milestone: v0.6.0
milestone_name: "AGmind x86 — post-M5 hardening + release confidence"
status: ready-for-m6c-real-install-e2e
last_updated: "2026-05-23"
last_activity: "2026-05-23 — refreshed codebase map, removed Claude artifacts, documented agent tooling needs, verified cleanup"
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
- **Latest full green CI:** GitHub Actions run `26297545718` on `33a2050`
- **Green gates:** pre-commit, audit, schema validate, compose validate,
  test-cpu, Docker build matrix `cpu/vulkan/rocm`, Strix Halo runtime smoke
  `vulkan/rocm`
- **Python package:** 79 files under `agmind/`
- **Tests:** 46 Python files under `tests/`
- **Service descriptors:** 33 files under `templates/services/`
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

Recommended order after this cleanup commit:

1. **M6.C Real install E2E:** run dry/full install path on the Strix Halo box
   and record evidence.
2. **M6.B follow-up:** stop Dependabot/release-drafter from stealing the only
   self-hosted runner.
3. **M6.D Cluster deploy smoke:** validate second-node inventory and worker
   inference path.
4. **M6.E GA pruning:** choose Grafana dashboards, Authelia 2FA, chat REPL,
   plugin marketplace, or DeepDoc/RAGFlow sidecar path based on value.

## Known Live Gaps

- Doctor host tuning: kernel/GTT pool.
- Dependabot/release-drafter can occupy the self-hosted runner.
- Grafana dashboard JSON provision remains deferred.
- Authelia 2FA wizard flow remains deferred.
- Plugin marketplace remains deferred.
- Full cluster deploy with peer replication still needs real smoke evidence.

## Reference Documents

- `AGMIND_MIGRATION_SPEC.md`
- `.planning/PROJECT.md`
- `.planning/REQUIREMENTS.md`
- `.planning/ROADMAP.md`
- `.planning/BACKLOG.md`
- `.planning/codebase/`
- `.planning/sessions/`
- `.planning/research/x86-migration/`
- `docs/adr/`
- `docs/BENCHMARKS.md`

**Last updated:** 2026-05-23.
