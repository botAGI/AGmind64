# AGmind x86 — Roadmap

**Current milestone:** v0.6.0 candidate — post-M5 hardening and release
confidence.
**Current gate:** M6.S0 cloud-artifact reconciliation.
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

Latest observed result, 2026-05-22:

- `pytest`: 886 passed
- audit: 0 findings
- doctor: 7 ok / 2 warn / 0 fail

---

## M6.S0 — Cloud-artifact Reconciliation (current)

**Problem:** HEAD is clean in git history, but local worktree contains a large
uncommitted layer left by prior cloud work. It includes mechanical formatting,
schema export, tooling tweaks, and small behavior fixes. This must be sorted
before new feature work.

| # | Task | DoD |
|---|------|-----|
| S0.1 | Inventory dirty files | `git diff --stat`, `git diff -w --stat`, and scoped notes in session journal |
| S0.2 | Verify current behavior | pytest + audit + doctor captured |
| S0.3 | Split mechanical vs semantic changes | candidate patch groups listed before commit |
| S0.4 | Commit or defer groups | each group has one conventional commit or explicit defer note |
| S0.5 | Refresh GSD docs | STATE/ROADMAP/BACKLOG/session updated |

**DoD:** worktree either clean or intentionally documented, and next phase can
start from a stable GSD checkpoint.

## M6.A — Planning + Codebase Refresh

**Problem:** `.planning/codebase/*`, `PROJECT.md`, and some backlog sections
still describe M1-M3/M5 planning history rather than the current post-M5
codebase.

| # | Task | DoD |
|---|------|-----|
| A.1 | Refresh `.planning/codebase/INDEX.md` | module/test counts match `rg --files` |
| A.2 | Refresh `.planning/codebase/ARCHITECTURE.md` | includes M4/M5 TUI, cluster, model split |
| A.3 | Refresh `PROJECT.md` | current milestone and shipped features accurate |
| A.4 | Prune historical backlog noise | shipped M3/M5 tasks marked historical |

## M6.B — Tooling Gate Cleanup

**Problem:** `pytest` and audit pass, but lint/pre-commit story is muddy.
`ruff check .` emits broad style findings, while `ruff format --check .` still
wants 14 files reformatted.

| # | Task | DoD |
|---|------|-----|
| B.1 | Decide lint policy | Makefile/pre-commit rules match intended gate |
| B.2 | Resolve or scope ruff rule explosion | no surprise hundreds of findings in normal dev path |
| B.3 | Validate ansible-lint bump | pre-commit can run in local venv / self-hosted runner |
| B.4 | Document exact green commands | README/CLAUDE/STATE agree |

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
