---
gsd_state_version: 1.1
milestone: v0.6.0
milestone_name: "AGmind x86 — post-M5 hardening + cloud-artifact reconciliation"
status: ci-testcpu-followup-local
last_updated: "2026-05-22"
last_activity: "2026-05-22 — self-hosted CI rerun proved standard gates; Strix smoke now checks runtime backend instead of pytest inside non-dev images"
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

# State: AGmind x86 — Post-M5 Handoff

## Project reference

See `.planning/PROJECT.md`.

**Core value:** Private LLM/RAG platform для AMD Strix Halo + generic
x86_64, **`agmind install` = one command from clean Ubuntu**, day-2 CLI
ergonomics, capability-aware service graph, cluster discovery, and a
multi-step Textual TUI with EN/RU i18n.

**GSD operating model:** `.planning/` is the active project memory:

- `STATE.md` — current truth / handoff point.
- `ROADMAP.md` — phase order and DoD-level milestone plan.
- `BACKLOG.md` — prioritized next work.
- `sessions/` — session journals for context transfer.
- `codebase/` and `research/` — architectural maps and recon evidence.

## Current position

- **Branch:** `develop`
- **HEAD:** latest pushed baseline before this smoke follow-up is `5d6ddad`
  — `ci: use system python on self-hosted runner`; next commit fixes Strix
  runtime smoke to avoid pytest inside non-dev backend images.
- **Remote:** `origin/develop` has the standard self-hosted gates green; Strix
  smoke follow-up is staged for push.
- **Tests:** `886 passed in 25.39s` via `.venv/bin/python -m pytest -q`; clean
  dev-only CI parity now `882 passed, 4 deselected` with coverage
- **Audit:** 0 findings on 218 files via `.venv/bin/python scripts/audit_forbidden.py`
- **Pre-commit:** all hooks pass locally after tooling fixes
- **Compose validate:** all CI profiles render and pass `docker compose config --quiet`
- **Docker builds:** base/cpu/vulkan pass locally with daemon `docker build`;
  GitHub run `26293422173` also proved cpu/vulkan/rocm Docker builds green on
  the self-hosted runner
- **Doctor:** 7 ok / 2 warn / 0 fail on Strix Halo box
- **Doctor warnings:** kernel below 6.18.4 guidance; GTT pool 62.5 GiB needs GRUB tuning
- **Dirty worktree:** yes, large cloud-artifact layer across ~101 files

## Immediate GSD checkpoint

The repo history says M5 is shipped, but the worktree is not clean. Treat
this as the current gate before new feature work:

| Gate | Status | Notes |
|------|--------|-------|
| S0.1 Identify dirty layer | in progress | Mix of formatter churn, pre-commit/ansible-lint tweak, schema export, CI fixes, small wizard/install fixes |
| S0.2 Verify behavior | done | `pytest -q` = 886 passed; audit = 0 findings; clean dev-only CI parity = 882 passed / 4 deselected |
| S0.3 Decide split | done | CI repair committed as focused chunks through `5d6ddad`, plus pending Strix smoke command fix |
| S0.4 Planning sync | in progress | This file + session note record the runner evidence |
| S0.5 GitHub CI rerun | in progress | Run `26297034205` proved all standard gates; Strix smoke failed only because runtime images lack pytest and need an entrypoint-overridden backend smoke |

**Rule:** do not start M6 feature work until the dirty layer is classified
and either committed in scoped chunks or intentionally left documented.

## Shipped milestones

| Milestone | Status | Anchor commits / notes |
|-----------|--------|------------------------|
| **M1 v0.1.0-dev — Migration alpha** | shipped 2026-05-19 | A-G migration |
| **M2 v0.2.0 — Production hardening** | shipped 2026-05-20 | H', L, J.2, H, N, O, P |
| **M3 v0.3.0 — UX + ops polish** | shipped 2026-05-20 | `57fd3ab` final M3 commit |
| **M4 wave — Cluster + UX bundle** | shipped 2026-05-21 | `f9b220d`, `fafc6cb`, M4.7.x polish |
| **M5 v0.5.0 — Model split + TUI polish round 2** | shipped 2026-05-21 | `1e63fb0`, `c86b3e0`, `6d92135` |
| **CI runner workaround** | shipped 2026-05-22 | `80a12c9` self-hosted runner |

## M5 outcome

User feedback that drove M5:

> "в одно окно выбора модели уебал и embedding; где rerank? настройки тоже
> отдельные. внешний вид всё ещё очко"

Shipped:

- Model selector split into LLM / Embed / Rerank sections.
- Separate per-service inference settings.
- Textual TUI polish round 2: hardware panel, section separators, help overlay,
  empty-state banner, diff coloring, progress polish.
- Cluster discovery integrated into wizard with peer banner and replicate toggle.
- 886 passing tests, 0 audit findings.

## Next focus

**M6 candidate:** release hardening and real deployment confidence.

Recommended phase order:

1. **M6.S0 — Cloud-artifact reconciliation:** split or commit current dirty
   layer after verification.
2. **M6.A — Planning/codebase refresh:** update `.planning/codebase/*` to
   post-M5 module counts and architecture.
3. **M6.B — Tooling gate cleanup:** local pre-commit/test-cpu/compose/docker
   base+cpu+vulkan are green; GitHub has proved docker cpu/vulkan/rocm and
   compose; next evidence is the test-cpu dependency follow-up rerun.
4. **M6.C — Real install E2E:** run `agmind install` dry-run/full path on the
   Strix Halo box and record evidence.
5. **M6.D — Cluster deploy smoke:** validate generated inventory + second LAN
   node workflow.
6. **M6.E — GA backlog pruning:** choose plugin marketplace, Authelia 2FA,
   Grafana dashboards, chat REPL, or DeepDoc/RAGFlow path based on value.

## Known live gaps

- One Strix smoke follow-up still needs push + GitHub rerun: use
  `python3 -c get_backend().device_info()` inside the runtime images instead
  of pytest.
- Dirty worktree must stay reconciled before feature work.
- `.planning/codebase/*` still reflects older module/test counts.
- Doctor warnings need host tuning: kernel/GTT pool.
- Grafana dashboards JSON provision remains deferred.
- Authelia 2FA wizard flow remains deferred.
- Plugin marketplace remains deferred.
- Full cluster deploy with peer replication still needs real smoke evidence.

## Reference documents

- `AGMIND_MIGRATION_SPEC.md` — migration and architecture source of truth
- `.planning/PROJECT.md` — charter
- `.planning/REQUIREMENTS.md` — requirements catalog
- `.planning/ROADMAP.md` — phase plan
- `.planning/BACKLOG.md` — next work
- `.planning/sessions/2026-05-22_gsd-handoff.md` — current Codex handoff
- `docs/adr/` — ADRs 0000-0012+
- `docs/BENCHMARKS.md` — Strix Halo benchmark evidence

**Last updated:** 2026-05-22 — post-M5 GSD handoff.
