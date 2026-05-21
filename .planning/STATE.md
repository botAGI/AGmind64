---
gsd_state_version: 1.0
milestone: v0.3.0
milestone_name: "AGmind x86 — UX + ops polish (M3 shipped)"
status: m3-shipped
last_updated: "2026-05-20"
last_activity: "2026-05-20 — M3 phases shipped: P.fix + Q + R + S.1 + S.2 + T"
progress:
  m1_phases: 7   # A-G (migration)
  m1_completed: 7
  m2_phases: 9   # H' L J.2 H N O P
  m2_completed: 9
  m3_phases: 6   # P.fix Q R S.1 S.2 T
  m3_completed: 6
  m3_percent: 100
---

# State: AGmind x86 — Milestone M3 shipped

## Project reference

See `.planning/PROJECT.md`.

**Core value:** Private LLM/RAG platform для AMD Strix Halo + generic
x86_64, **`agmind install` = one command from clean Ubuntu**, day-2 CLI
ergonomics, capability-aware service graph, multi-step TUI with i18n.

**Current focus:** M3 closed (UX + ops polish + version-check signal-to-noise).
Готово к M4 (cluster + plugin marketplace) или real-hardware E2E test.

## Current position

- **Milestone:** v0.3.0 (M3 shipped) → **M4 in progress (cluster + UX polish bundle)**
- **Tip commit (local):** `fafc6cb` (M4 cluster mDNS + UX bundle); push pending GH token refresh
- **Tests:** 865 passing, 0 skipped, 0 failed
- **Audit:** 0 findings on 215 files
- **Doctor:** 7 ok / 2 warn / 0 fail on real Strix Halo box
- **Cluster detect:** working — `agmind cluster {detect,advertise,status}` via mDNS

## M5 v0.5.0 NEXT — Model split + TUI polish round 2

User feedback 2026-05-21: "в одно окно выбора модели уебал и embedding;
где rerank? настройки тоже отдельные. внешний вид всё ещё очко".

| Phase | What | Effort |
|-------|------|-------:|
| M5.1 | Split model selector: 3 sections (LLM / Embed / Rerank), filter `models_for_wizard()` by kind, SetupState с embed_/rerank_ fields | ~3h |
| M5.2 | Per-service inference settings: AGMIND_{LLM,EMBED,RERANK}_{CTX,KV,PARALLEL} | ~1.5h |
| M5.3 | TUI polish round 2: Rule separators, full-width hardware panel, F1 help overlay, inline hint, empty-state banner, color-coded diff, animated progress | ~2h |
| M5.4 | Wizard cluster integration: detect peers banner в DomainScreen + "deploy to all" toggle | ~1.5h |

См. `.planning/BACKLOG.md` для full DoD per task.

## M4 wave (in progress, local-only pending push)

| Phase | What | Status |
|-------|------|--------|
| M4.1 | Multi-step wizard теперь DEFAULT (escape via `--legacy-wizard`) | ✅ `f9b220d` (local) |
| M4.U.1 | Cluster auto-detect via zeroconf mDNS (`agmind cluster ...`) | ✅ `fafc6cb` (local) |
| M4.2 | `agmind setup` alias для `install` | ✅ `fafc6cb` |
| M4.3 | i18n validators (DomainValidator/TokenLengthValidator → EN/RU) | ✅ `fafc6cb` |
| M4.4 | doctor colored Rich output (auto-detect + NO_COLOR) | ✅ `fafc6cb` |
| M4.5 | status `--tui` hotkeys: pause (p) / filter (f) / sort (s) | ✅ `fafc6cb` |
| M4.6 | Install model_pull MB/s + ETA throttling | ✅ `fafc6cb` |

## Milestones

| Milestone | Phases | Status |
|-----------|--------|--------|
| **M1 v0.1.0-dev — Migration alpha** | A B C D E F G | ✅ shipped 2026-05-19 |
| **M2 v0.2.0 — Production hardening** | H' L J.2 H N O P | ✅ shipped 2026-05-20 |
| **M3 v0.3.0 — UX + ops polish** | P.fix Q R S.1 S.2 T | ✅ shipped 2026-05-20 |
| **M4 v0.4.0 — Cluster + plugin marketplace** | U V W | 📋 next milestone |
| **M5 v1.0.0 — GA** | — | 📋 TBD (real-hardware E2E gate) |

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

## M3 v0.3.0 — UX + ops polish (✅ SHIPPED 2026-05-20)

| Phase | Description | Commit |
|-------|-------------|--------|
| **P.fix** | version_check filter: drop variant/RC/SHA tags; add gcr/quay probes; signal/noise 13 ✅ vs prior 6 | `b04a1b5` |
| **Q** | `agmind models {list,pull,rm,info}` standalone CLI + 19 tests | `59334d3` |
| **R** | `agmind upgrade --component X --version Y` lifecycle (check/bump/apply/rollback) + holds respect + 15 tests | `6a900cf` |
| **S.1** | TUI: Toast notifications + DomainValidator / TokenLengthValidator + ProgressBar show_eta + 7 tests | `cef5208` |
| **S.2** | Multi-step wizard split (Domain → Model → Services → Confirm), opt-in via AGMIND_WIZARD_MULTISTEP=1 + 9 tests | `7cd1293` |
| **T** | i18n EN/RU hookup в multi-step screens + agmind install --lang flag + 5 tests | `57fd3ab` |

**M3 outcome:** 843 passed (от 782), 13 ADRs, 17 R-recons, 33 services,
6 new TUI tests passed (validators), 4 multi-step screens, 47 wizard
i18n keys EN/RU.

## M4 v0.4.0 — Cluster + plugin marketplace (NEXT)

| Phase | Description | Effort |
|-------|-------------|-------:|
| U | Phase M cluster — multi-node Ansible inventory + dual-host deploy | ~12h |
| V | `agmind plugin install/list` marketplace | ~12h |
| W | Authelia 2FA toggle в wizard (currently service есть, не активирован) | ~3h |
| (opt) | mDNS endpoints advertising для *.local (legacy parity) | ~2h |
| (opt) | Grafana dashboard provision (M3 deferred — JSON dashboards для llama/system/services) | ~4h |
| (opt) | `agmind chat` REPL hook'ается в running deploy /v1/chat/completions | ~2h |

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
