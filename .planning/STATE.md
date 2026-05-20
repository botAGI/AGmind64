---
gsd_state_version: 1.0
milestone: v0.2.0-alpha
milestone_name: "AGmind x86 — Production hardening (M2 in progress)"
status: m2-in-progress
last_updated: "2026-05-20"
last_activity: "2026-05-20 — Phase L.D + audit fix + J.1.10 + J.2 + L.E + Phase H bench + N + N.G + N.H + O.fix + Phase P shipped"
progress:
  m1_phases: 7   # A-G (migration)
  m1_completed: 7
  m2_phases_planned: 9   # H-prime + L + J.2 + H + N + O + P + ... (see ROADMAP)
  m2_completed: 9
  m2_percent: ~70   # core production hardening done; UX polish + multi-node + plugin marketplace pending
---

# State: AGmind x86 — Milestone M2 in progress

## Project reference

See `.planning/PROJECT.md`.

**Core value:** Private LLM/RAG platform для AMD Strix Halo + generic
x86_64, **`agmind install` = one command from clean Ubuntu**, day-2 CLI
ergonomics, capability-aware service graph.

**Current focus:** post-M2-core polish — UX hardening (toast / multi-step
wizard), version_check filter false positives, `agmind models` + `agmind
upgrade` CLI, optional i18n + Authelia 2FA hookup.

## Current position

- **Milestone:** v0.2.0-alpha (M2 production hardening) — **~70% complete**
- **Tip commit:** `1e4923e` (Phase P upstream version check) on develop
- **Tests:** 782 passing, 0 skipped, 0 failed
- **Audit:** 0 findings on 207 files
- **Doctor:** 7 ok / 2 warn / 0 fail on real Strix Halo box
- **Last activity:** 2026-05-20 — 22 коммитов за день (L.D → Phase P)

## Milestones

| Milestone | Phases | Status |
|-----------|--------|--------|
| **M1 v0.1.0-dev — Migration alpha** | A B C D E F G | ✅ shipped 2026-05-19 |
| **M2 v0.2.0 — Production hardening (in progress)** | H' L J.2 H N O P | ✅ core done; ⏳ UX polish remaining |
| **M3 v0.3.0 — UX + ops polish (next)** | Q R S T P.fix | 📋 planned (see ROADMAP) |
| **M4 v0.4.0 — Cluster + plugin marketplace** | U V | 📋 deferred |
| **M5 v1.0.0 — GA** | — | 📋 TBD |

## M2 phase tracker

| Phase | Description | Status | Commit |
|-------|-------------|--------|--------|
| H' | Foundation refactor (A-E: ServiceDescriptor + split + renderer + observability + plugins) | ✅ | (earlier session) |
| L.A | pre-commit + GH Actions matrix + release-drafter | ✅ | (earlier session) |
| L.B | `agmind deploy` idempotent + snapshot + healthcheck + rollback | ✅ | (earlier session) |
| L.C | `agmind gc` (containers/images/volumes/networks/models) | ✅ | (earlier session) |
| **L.D** | **State schema migration system + ADR-0009** | ✅ | `7560b11` |
| **L.E** | **`agmind logs/shell/backup/restore` + R14 gaps doc** | ✅ | `2d5de65` + `7e95fed` |
| **J.1.10** | **Compact TUI wizard ([✓] checkboxes, 2-col grid)** | ✅ | `1b7dfe9` |
| **J.2** | **`agmind status --tui` live dashboard** | ✅ | `accb2be` |
| **H** | **Phase H — real Strix Halo bench (Qwen3.6 73 t/s)** | ✅ | `c6421f1` |
| **N** | **`agmind install` end-to-end installer + ADR-0010** | ✅ | `bd453af` |
| **N.G** | **TUI model selector + ctx/kv settings + ADR amend** | ✅ | `1da001c` |
| **N.H** | **TUI threads/parallel + smart model detect+reuse** | ✅ | `da225c3` |
| **O** | **Service capability graph (provides/conflicts/consumes) + ADR-0011** | ✅ | `b260c20` |
| **O.fix** | **Drop выдуманные conflicts + verified bindings + ragflow v0.25.5** | ✅ | `99322d3` |
| **P** | **Upstream version check workflow + ADR-0012** | ✅ | `1e4923e` |
| **Misc fixes** | audit unfreeze + vulkan multi-GPU + un-skip TUI tests + mypy clean + model out of repo | ✅ | `3dda542` `5f4ad67` `38aa5e2` `8a6c621` `a477eb2` |

## M3 v0.3.0 — UX + ops polish (next milestone)

See `ROADMAP.md` for full DoD per phase.

| Phase | Description | Effort | Priority |
|-------|-------------|-------:|----------|
| **P.fix** | version_check filter: drop variant/RC/SHA tags; add gcr/quay probes | 1h | 🔴 высокий (weekly report сейчас шумный) |
| **Q** | `agmind models {list,pull,rm}` standalone CLI | 2h | 🟡 medium |
| **R** | `agmind upgrade --component X` (bump pin + redeploy) | 2h | 🟡 medium |
| **S.1** | TUI: Toast notifications + inline Input validation (red border) | 2h | 🟢 UX polish |
| **S.2** | TUI: multi-step wizard split (Domain → Model → Services → Confirm) | 4h | 🟢 UX polish |
| **T** | i18n hookup в wizard (EN/RU select) | 1.5h | 🔵 low (если non-RU users) |

**Total M3:** ~12.5h. Можно split на 2-3 sessions.

## M4 v0.4.0 — Cluster + plugin marketplace (deferred)

| Phase | Description |
|-------|-------------|
| U | Phase M cluster — multi-node Ansible inventory + dual-host deploy |
| V | `agmind plugin install/list` marketplace (witmeng/ragflow-api style) |
| (optional) | mDNS endpoints advertising (legacy *.local — на отдельных нодах) |
| (optional) | `agmind chat` REPL: integrate с running deploy через /v1/chat/completions |

## Architecture snapshot (post-Phase P)

Layer 1 — **Ansible** (orchestration): 11 roles + install.yml playbook.

Layer 2 — **Python `agmind/`** (runtime): 74 modules, ~11.5k LOC.
- **compute/** (18 files): backend abstraction + 4 backends + 5 engines + 2 clients
- **cli/** (9 files): 17 typer commands incl. `install` + `migrate` + ops
- **cli/tui/** (7 files): 5 screens (wizard / deploy / install / dashboard / summary) + logo
- **deploy/** (5 files): idempotent runner + gc + snapshot + diff
- **install/** (4 files): orchestrator + 6 steps + curated model catalog
- **ops/** (3 files): backup tarball + exec wrapper
- **migrations/** (6 files): runner + state + v001 baseline
- **services/** (5 files): registry + renderer + capability_bindings + compatibility
- **schemas/** (2 files): ServiceDescriptor Pydantic v2 + JSON Schema export
- **diagnostics/** + **cluster/** + **config/** + **i18n/** + **observability/** (~700 LOC)

Layer 3 — **Declarative catalogs** (`templates/`): 33 service YAMLs + JSON
Schema + observability configs + Traefik dynamic + models.yaml + version_holds.yaml.

Layer 4 — **CI/CD** (`.github/workflows/`): ci + release-drafter + **version-check** (Phase P).

## Architectural invariants (still hold)

См. `.planning/codebase/INVARIANTS.md`. Phase O.fix amendment refined:
- **I.O.1:** `provides`/`consumes` decoupled — provider можно swap, consumer
  получает env vars через capability_bindings injection.
- **I.O.2:** `conflicts_with` field оставлен в schema, но в production
  descriptors **никем не заполнен** (soft warnings only post-O.fix).
- **I.O.3:** Inside docker network все llama-server ports = 8080
  (host-side 8080/8081/8082 — это публикация, not internal hostnames).

## Outstanding gaps (gap analysis vs legacy AGmind)

См. session report для подробного gap analysis. Top items:

| Item | x86 status |
|------|-----------|
| TUI install wizard | ✅ Phase J + N |
| Service registry | ✅ Phase H'.B (33 descriptors) |
| Backup/Restore | ✅ Phase L.E + L.E.1/4/5 safety hints |
| Snapshots + rollback | ✅ Phase L.B |
| State migrations | ✅ Phase L.D |
| Monitoring stack configs | ✅ Phase H'.D |
| Phase H bench | ✅ 73 t/s Qwen3.6 |
| Version check | ✅ Phase P (M3.P.fix needed) |
| `agmind models {list,pull,rm}` | ❌ M3.Q |
| `agmind upgrade --component X` | ❌ M3.R |
| Multi-step wizard | ❌ M3.S.2 |
| Toast / inline validation | ❌ M3.S.1 |
| i18n in wizard | ❌ M3.T |
| Authelia 2FA toggle | ❌ M4 |
| mDNS endpoints | ❌ M4 |
| Multi-node cluster | ❌ M4.U |
| Plugin marketplace | ❌ M4.V |
| Grafana dashboards (provisioned) | ❌ M3 candidate (deferred) |

## Key decisions log (M2 additions)

| Date | Decision | Source |
|------|----------|--------|
| 2026-05-20 | `agmind install` — one-command Python orchestrator, не bash | User feedback, ADR-0010 |
| 2026-05-20 | Sudo через anonymous pipe + ansible --become-password-file | ADR-0010 |
| 2026-05-20 | Capability graph: provides/conflicts/consumes — но conflicts soft (warnings only) | ADR-0011 + amendment |
| 2026-05-20 | RAGflow + Dify coexist (witmeng/ragflow-api plugin) | Phase O.fix research |
| 2026-05-20 | RAGflow DOC_ENGINE supports только ES/infinity/oceanbase/opensearch/seekdb, NOT milvus | github.com/infiniflow/ragflow/.env verified |
| 2026-05-20 | llama-server inside docker network = port 8080 (host 8080/8081/8082 — публикация) | post-O.fix research |
| 2026-05-20 | curated model catalog в TUI + "Custom HF" input | Phase N.G — legacy UX parity |
| 2026-05-20 | Smart model detect/reuse (default `/var/lib/agmind/models/` + fallback `~/.local/share/agmind/models/`) | Phase N.H user request |
| 2026-05-20 | Upstream version check weekly cron → single auto-updated issue | Phase P — issue#63 mirror |

## Reference documents

- `AGMIND_MIGRATION_SPEC.md` — single source of truth
- `.planning/PROJECT.md` — milestone charter
- `.planning/REQUIREMENTS.md` — 119 REQ-IDs
- `.planning/ROADMAP.md` — phase order + M3/M4
- `.planning/BACKLOG.md` — prioritized gap list
- `.planning/research/x86-migration/` — 17 recons + 4 baselines + 6 deep dives
- `.planning/sessions/` — session journals (2 entries, 2026-05-19 + 2026-05-20)
- `docs/MIGRATION_PLAN.md` + 13 ADRs

**Last updated:** 2026-05-20 — после Phase P + GSD refresh.
