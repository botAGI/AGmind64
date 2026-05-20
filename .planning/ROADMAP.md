# AGmind x86 — Roadmap

**Current milestone:** v0.2.0-alpha (M2 production hardening, ~70% complete)
**Next milestone:** v0.3.0 (M3 UX + ops polish)
**Target stable:** v1.0.0

## Milestone overview

| Milestone | Status | Phases |
|-----------|--------|--------|
| v0.1.0-dev (M1) — Migration alpha | ✅ SHIPPED 2026-05-19 | A B C D E F G |
| **v0.2.0 (M2) — Production hardening** | ⏳ in progress (70%) | H' L J.2 H N O P |
| **v0.3.0 (M3) — UX + ops polish** | 📋 planned | Q R S T P.fix |
| v0.4.0 (M4) — Cluster + plugins | 📋 deferred | U V |
| v1.0.0 (M5) — GA | 📋 TBD | — |

---

## v0.1.0-dev — Migration alpha (✅ SHIPPED 2026-05-19)

7 phases A-G complete. **Outcome:** 39 modules, 25 test files (306 tests),
31 Ansible files, 32 services, 5 model tiers, 12 recons, 3 ADRs.

См. `.planning/sessions/2026-05-19_phase-H-prime-to-J18.md` для post-A-G
работы.

## v0.2.0 — Production hardening (M2, in progress)

Focus: real deploy possible + day-2 ops + bench-verified hardware.

### M2.H' — Foundation refactor (✅ shipped earlier session)

- H'.A ServiceDescriptor Pydantic v2 + JSON Schema (ADR-0005)
- H'.B Split monolithic services.yaml → 32 files
- H'.C Python compose renderer + Traefik routing (ADR-0006)
- H'.D Observability skeleton (8 configs + structlog) (ADR-0007)
- H'.E setuptools entry_points + service CLI + legacy cleanup (ADR-0008)

### M2.L — Day-2 ops (✅ shipped 2026-05-19 → 2026-05-20)

- L.A pre-commit + GH Actions matrix + release-drafter
- L.B `agmind deploy` idempotent + snapshot + healthcheck + rollback
- L.C `agmind gc` (containers/images/volumes/networks/models)
- **L.D `agmind migrate` state schema migrations** (ADR-0009, `7560b11`)
- **L.E `agmind logs/shell/backup/restore` + R14 gaps** (`2d5de65`, `7e95fed`)

### M2.J.2 — Live dashboard (✅ shipped `accb2be`)

`agmind status --tui` — Textual DataTable + 5s refresh + service health glyphs.

### M2.H — Hardware bench (✅ shipped `c6421f1`)

**DoD passed:**
- Pull `ghcr.io/ggml-org/llama.cpp:full-vulkan-b9049`
- Download Qwen3.6-35B-A3B-Q4_K_M (19.7 GB)
- llama-bench: **pp512=1024 / tg128=73.47 t/s**
- +41% vs DGX Spark FP8 baseline
- `docs/BENCHMARKS.md` row added

### M2.N — End-to-end installer (✅ shipped `bd453af` + `1da001c` + `da225c3`)

- N.A orchestrator + 6 steps (doctor / bootstrap / pull / model / env_write / deploy)
- N.B InstallProgressScreen (live RichLog + per-step ProgressBar)
- N.C `agmind install` CLI + sudo via anonymous pipe
- N.G TUI model selector (curated + custom HF) + ctx_size + kv_cache_type
- N.H Threads + parallel slots + smart model detect/reuse from fallback paths
- ADR-0010

### M2.O — Service capability graph (✅ shipped `b260c20` + `99322d3`)

- O.A `provides` / `conflicts_with` / `consumes` в ServiceDescriptor
- O.B `capability_bindings.py` + `inject_capability_env` в renderer
- O.fix — research-driven correction: drop фейковые conflicts (ragflow vs
  dify, vector DBs, reverse proxies — все coexist); fix llama-server ports
  (8080 internal); add `dify_external_kb` capability для RAGflow→Dify
  integration; ragflow pin v0.25.4 → v0.25.5
- ADR-0011 + 2026-05-20 amendment

### M2.P — Upstream version check (✅ shipped `1e4923e`)

- `scripts/version_check.py` (~250 LOC scanner + report renderer)
- `templates/version_holds.yaml` (HOLD config с reasons)
- `.github/workflows/version-check.yml` (weekly cron Monday 06:00 UTC →
  create/update issue с label `upstream-update`)
- ADR-0012

**M2 outcome (snapshot 2026-05-20):**
- 782 passed, 0 skipped, 0 failed
- 13 ADRs, 17 R-recons
- 74 Python modules (~11.5k LOC)
- 33 service descriptors with capability annotations

**M2 remaining (rolled to M3 as scope creep):**
- M2.K (Grafana dashboards provisioned) — был на roadmap, deferred в M3
- M2.U (Ansible cluster role testing) — был на roadmap, deferred в M4

---

## v0.3.0 — UX + ops polish (M3, NEXT)

Focus: **make TUI feel modern + fill remaining day-2 ops CLI gaps**.

### M3.P.fix — version_check tag filter (~1h, 🔴 high priority)

**Problem:** live `version_check.py` run показал шум:
- `grafana 13.1.0-25893932881-ubuntu` — OS variant flagged как newer version
- `prometheus v3.12.0-rc.0-distroless` — RC tag, не stable
- `weaviate 1.38.0-dev-fc90344-arm64` — dev + arch variant
- `caddy 2.11.3-windowsservercore-ltsc2025` — Win variant
- `dify-sandbox 5631afef06ec...` — SHA tag (не version)

**Tasks:**
- [ ] Tag filter: drop suffixes `-windowsservercore`, `-arm64`, `-amd64`,
      `-distroless`, `-ubuntu`, `-alpine` (но keep `-stable`)
- [ ] Drop RC / dev / nightly / SHA tags via regex
- [ ] Add gcr.io probe (cadvisor)
- [ ] Add quay.io probe (minio, docling)
- [ ] Fix ghcr.io probe для tags-only-as-SHAs (open-webui case)
- [ ] Re-test live + verify signal-to-noise ratio

**DoD:** weekly report shows только meaningful patch/minor/major bumps;
"❌ error" count < 5 (только known un-probable like gcr.io/cadvisor если
API не доступен).

### M3.Q — `agmind models {list,pull,rm,info}` CLI (~2h, 🟡 medium)

**Problem:** model management сейчас только через `agmind install` flow.
User не может скачать вторую модель без re-install. Legacy AGmind имел
`agmind models download --tier M` etc.

**Tasks:**
- [ ] `agmind models list` — list local *.gguf в `/var/lib/agmind/models/`
      + size + last-used timestamp
- [ ] `agmind models pull <model-id>` — выбор из curated catalog +
      huggingface-hub download
- [ ] `agmind models pull --repo user/x --file y.gguf` — custom HF
- [ ] `agmind models rm <model-id>` — удалить + warn если в use (compose
      ps шоwit llama-llm с этой моделью)
- [ ] `agmind models info <model-id>` — display size, quant, params, ctx
- [ ] Tests (mock HF + filesystem) + reuse Phase N.H detect/reuse logic

**DoD:** standalone command для управления моделями; reuse curated
catalog из Phase N.G.

### M3.R — `agmind upgrade --component X` (~2h, 🟡 medium)

**Problem:** для bump single image pin сейчас нужно edit YAML вручную
+ rerun `agmind deploy --apply`. Хочется one-shot.

**Tasks:**
- [ ] `agmind upgrade --component <service> --version <tag>` — update
      `templates/services/<service>.yaml` image tag + digest auto-resolve
- [ ] `agmind upgrade --check` — synonym для `scripts/version_check.py`
- [ ] `agmind upgrade --apply` — full re-deploy after bump (use Phase L.B
      runner с rollback safety)
- [ ] `agmind upgrade --rollback` — revert template + redeploy snapshot
- [ ] Integration: respect `templates/version_holds.yaml` (refuse to
      upgrade held pin без `--force`)

**DoD:** safe bump flow с automatic snapshot + rollback.

### M3.S.1 — TUI feedback polish (~2h, 🟢 UX)

**Problem:** wizard сейчас использует `#status-msg` Static для feedback —
не non-blocking, scroll up чтобы прочитать.

**Tasks:**
- [ ] `self.notify(...)` Textual toast вместо status-msg для preview /
      success / error
- [ ] Inline `Input.validators` для domain + CF token (red border до
      того как user исправит)
- [ ] Modal `ConfirmScreen` для Apply confirmation (deploy = destructive)
- [ ] `ProgressBar(show_eta=True)` в InstallProgressScreen — ETA для
      model download visible

**DoD:** wizard feedback consistent с Textual best practices (см. research).

### M3.S.2 — Multi-step wizard split (~4h, 🟢 UX)

**Problem:** current `AgmindSetupApp` shows 6 sections на одном screen
(cognitive overload). Best practice — wizard pattern: step-by-step.

**Tasks:**
- [ ] Split в 4 screens:
      1. **DomainScreen** — domain + CF token (с inline validation)
      2. **ModelScreen** — curated select / custom HF / ctx / KV / threads / parallel
      3. **ServicesScreen** — per-tier service checkboxes (existing)
      4. **ConfirmScreen** — summary + Apply / Back
- [ ] Navigation: Next / Back buttons + footer keybindings (Tab → Next)
- [ ] Single shared `SetupState` dataclass (existing), passed между
      screens через app.push_screen()
- [ ] Persist partial state в `~/.local/share/agmind/setup-state.json`
      между steps — позволяет resume если cancel
- [ ] Update tests для new screen flow

**DoD:** linear wizard UX; user видит за раз только relevant fields;
back button восстанавливает previous selections.

### M3.T — i18n hookup (~1.5h, 🔵 low if non-RU users)

**Problem:** `agmind/i18n/` module существует с `en.json` + `ru.json`
catalogs, но wizard hardcoded русский / mix RU + EN. Legacy AGmind
имел EN/RU select в первом screen.

**Tasks:**
- [ ] Auto-detect via `os.environ.get('LANG')` (default 'en')
- [ ] First wizard screen (или command line `--lang`): EN/RU select
- [ ] Wrap all user-facing strings через `i18n.get(key)`
- [ ] Update en.json + ru.json
- [ ] Test: `LANG=ru_RU agmind setup` → русский UI

**DoD:** wizard готов для non-RU users без RU strings; legacy parity.

**M3 effort estimate:** ~12.5h. Можно split на 2-3 sessions.

---

## v0.4.0 — Cluster + plugin marketplace (M4, DEFERRED)

### M4.U — Phase M cluster (multi-node)

- Ansible inventory с dual/triple host
- mDNS endpoints advertising
- Inter-node WireGuard (или AmneziaWG для РФ per user feedback)
- Cluster-aware deploy в Phase L.B runner

### M4.V — Plugin marketplace

- `agmind plugin {list,install,remove}` similar to Dify plugin marketplace
- Discovery from agmind.dev/plugins (TBD endpoint)

---

## v1.0.0 — GA (M5, TBD)

DoD: all M1-M4 phases shipped, 4 weeks production runtime, 0 P0/P1
issues, complete user docs, public release.

---

## Phase dependency graph

```
M1 (A-G) ─→ M2.H' ─→ M2.L ─→ M2.J.2 ─→ M2.H ─→ M2.N ─→ M2.O ─→ M2.P
                                                                  │
                                                                  ↓
                                                                M3 (P.fix, Q, R, S, T)
                                                                  │
                                                                  ↓
                                                                M4 (U, V)
                                                                  │
                                                                  ↓
                                                                M5 (GA)
```

Cross-phase dependencies:
- M3.Q requires Phase N.H detect/reuse logic (already exists)
- M3.R requires Phase L.B deploy runner (already exists)
- M3.S.1/S.2 requires Phase J wizard (already exists)
- M4.U requires M2.L.B + Ansible cluster role (skeleton exists, не tested)
