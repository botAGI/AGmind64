# AGmind x86 — Backlog (post-M5, 2026-05-22)

> **M5 SHIPPED 2026-05-21:** model split + per-service settings + TUI polish round 2
> + cluster TUI integration. 886 tests · 0 audit findings.
> Commits: `1e63fb0` (M5.1+M5.2) + `c86b3e0` (M5.3+M5.4).
>
> **Current gate 2026-05-22:** reconcile dirty cloud-artifact layer before
> new feature work. `HEAD=80a12c9`, pytest 886 passed, audit 0, doctor 7 ok / 2 warn.


Структурированный backlog для next sessions. Сгруппировано по
**milestone × priority**. См. `ROADMAP.md` для phase context.

Legend:
- 🔴 **Critical** — production blocker
- 🟡 **High** — production-readiness
- 🟢 **Medium** — UX polish
- 🔵 **Low** — nice-to-have

## Status snapshot (2026-05-22)

- **M1 v0.1.0-dev (Migration alpha):** ✅ SHIPPED
- **M2 v0.2.0 (Production hardening):** ✅ SHIPPED
- **M3 v0.3.0 (UX + ops polish):** ✅ SHIPPED
- **M4 wave (Cluster + UX bundle):** ✅ SHIPPED
- **M5 v0.5.0 (Model split + TUI polish round 2):** ✅ SHIPPED
- **M6 v0.6.0 candidate (Hardening + E2E):** current

Test baseline: **886 passed, 0 skipped, 0 failed.** Audit: 0 findings.

---

## Live queue — M6.S0 Cloud-artifact reconciliation

| # | Task | Priority | Notes |
|---|------|----------|-------|
| S0.1 | Classify 101-file dirty worktree | 🔴 | Separate formatting churn from semantic fixes |
| S0.2 | Decide commit grouping | 🔴 | Likely groups: planning sync, schema/tooling, formatter cleanup, small behavior fixes |
| S0.3 | Validate with project gates after each group | 🔴 | pytest + audit minimum |
| S0.4 | Make lint/pre-commit policy explicit | 🟡 | `ruff check .` currently not a clean gate |
| S0.5 | Update `.planning/codebase/*` after M5 | 🟡 | Architecture/index still stale |

## Live queue — M6 hardening candidates

| # | Task | Priority | Notes |
|---|------|----------|-------|
| M6.C | Real `agmind install` E2E on Strix Halo | 🔴 | Record dry-run/full path evidence |
| M6.D | Cluster deploy smoke with second LAN node | 🟡 | mDNS exists; replication needs evidence |
| M6.B | Tooling gate cleanup | 🟡 | ruff/pre-commit/ansible-lint/mypy story |
| M6.E.1 | Grafana dashboards JSON provision | 🟢 | Deferred since M2 |
| M6.E.2 | Authelia 2FA wizard flow | 🟢 | Service exists; UX/config missing |
| M6.E.3 | `agmind chat` against running deploy | 🟢 | Small demo-value feature |
| M6.E.4 | Plugin marketplace | 🔵 | Larger deferred scope |

---

## Historical backlog below

The sections below are kept for traceability. Many M2/M3/M5 items are already
shipped in git history; use the live queue above for current work.

## M2 — Remaining (rolled into M3)

These were originally scoped в M2 но rolled to M3:

| # | Task | Effort | Priority | Notes |
|---|------|:------:|----------|-------|
| M2.K.1 | Grafana dashboards auto-provision (datasources + 3 dashboards: system / llama / services) | 4h | 🟡 | configs готовы (Phase H'.D), JSON dashboards не написаны |
| M2.K.2 | Prometheus alert rules tuning (CPU/RAM/GPU/disk thresholds) | 2h | 🟡 | skeleton есть, thresholds default |
| M2.U.1 | Ansible cluster role smoke test (1 host playbook --check) | 2h | 🟡 | playbook есть, не run'нен |

---

## M3 v0.3.0 — UX + ops polish (next sprint)

### M3.P.fix — version_check tag filtering (~1h, 🔴 high)

Weekly Phase P report сейчас шумный — много false positives. Fix перед
next Monday cron run.

| # | Task | Effort |
|---|------|:------:|
| P.fix.1 | Variant tag filter regex (`-windowsservercore`, `-arm64`, `-ubuntu`, `-alpine`, `-distroless`) | 15 min |
| P.fix.2 | RC/dev/nightly drop (`-rc`, `-dev-`, `+security-`, дата-based) | 15 min |
| P.fix.3 | SHA-only tags filter (40-char hex без semver prefix) | 10 min |
| P.fix.4 | Quay.io probe (`quay.io/<org>/<image>` через v2 API) | 10 min |
| P.fix.5 | GCR probe (`gcr.io/<project>/<image>`) | 10 min |
| P.fix.6 | Re-test live + verify signal-to-noise ratio | 10 min |

**DoD:** weekly report shows только real bumps; "❌ error" < 5.

### M3.Q — `agmind models {list,pull,rm,info}` (~2h, 🟡 medium)

Standalone CLI для управления GGUF files (без `agmind install`).

| # | Task | Effort |
|---|------|:------:|
| Q.1 | `agmind models list` — local *.gguf + size + last-used | 20 min |
| Q.2 | `agmind models pull <id>` — выбор из CURATED_MODELS | 30 min |
| Q.3 | `agmind models pull --repo X --file Y` — custom HF | 20 min |
| Q.4 | `agmind models rm <id>` — delete + warn если в use | 20 min |
| Q.5 | `agmind models info <id>` — size + quant + params + ctx | 15 min |
| Q.6 | Tests (mock HF + filesystem) | 30 min |

**Reuse:** Phase N.H detect/reuse + Phase N.G CURATED_MODELS catalog.

### M3.R — `agmind upgrade --component X` (~2h, 🟡 medium)

Bump single image pin + redeploy с rollback safety.

| # | Task | Effort |
|---|------|:------:|
| R.1 | `agmind upgrade --check` — synonym для version_check | 10 min |
| R.2 | `agmind upgrade --component X --version Y` — edit YAML + auto-resolve digest | 40 min |
| R.3 | `agmind upgrade --apply` — re-deploy after bump (reuse Phase L.B runner) | 30 min |
| R.4 | `agmind upgrade --rollback` — revert + redeploy snapshot | 20 min |
| R.5 | Respect version_holds.yaml (refuse без --force) | 10 min |
| R.6 | Tests | 20 min |

### M3.S.1 — TUI feedback polish (~2h, 🟢 UX)

| # | Task | Effort |
|---|------|:------:|
| S.1.1 | Replace `#status-msg` Static с `self.notify(...)` Toast | 30 min |
| S.1.2 | Inline domain Input validator (red border до fix) | 30 min |
| S.1.3 | Inline CF token validator (length check live) | 20 min |
| S.1.4 | Modal ConfirmScreen для Apply (destructive guard) | 30 min |
| S.1.5 | ProgressBar(show_eta=True) в Install screen | 10 min |

### M3.S.2 — Multi-step wizard split (~4h, 🟢 UX)

| # | Task | Effort |
|---|------|:------:|
| S.2.1 | `DomainScreen` extract (domain + CF token + inline validation) | 60 min |
| S.2.2 | `ModelScreen` extract (curated/custom + ctx/kv/threads/parallel) | 60 min |
| S.2.3 | `ServicesScreen` extract (per-tier checkboxes) | 30 min |
| S.2.4 | `ConfirmScreen` (summary + Apply / Back) | 30 min |
| S.2.5 | Navigation flow + Tab keybinding + back-button-restores-state | 30 min |
| S.2.6 | Persist partial state ~/.local/share/agmind/setup-state.json | 20 min |
| S.2.7 | Update tests для new screen flow | 30 min |

### M3.T — i18n hookup (~1.5h, 🔵 low)

| # | Task | Effort |
|---|------|:------:|
| T.1 | Auto-detect через LANG env var | 10 min |
| T.2 | `--lang en/ru` CLI flag | 10 min |
| T.3 | Wrap all user-facing strings через i18n.get() | 60 min |
| T.4 | Update en.json + ru.json (cover все wizard strings) | 30 min |
| T.5 | Test LANG=ru_RU agmind setup → русский UI | 10 min |

**M3 total estimate:** ~12.5h. Можно split на 2-3 sessions.

---

## M5 v0.5.0 — Model selectors split + TUI polish round 2 ✅ SHIPPED 2026-05-21

User feedback 2026-05-21 (контекст compaction approaching):
"очень тупая логика — ты в одно окно выбора модели уебал и embedding;
а где rerank? и настройки тоже отдельные. + внешний вид всё ещё очко."

### M5.1 — Split model selector на 3 secции (LLM + Embed + Rerank)

Сейчас wizard's `ModelScreen` имеет ОДИН `model-select` для всех типов
моделей — но CURATED_MODELS уже содержит `kind="llm"|"embed"|"rerank"`.
User видит embed-модели вместе с LLM в одном dropdown.

| # | Task | Effort |
|---|------|:------:|
| M5.1.1 | Filter `models_for_wizard()` по kind — return 3 separate lists | 20 min |
| M5.1.2 | SetupState: + `embed_model_id`, `embed_repo`, `embed_file`; + `rerank_model_id`, `rerank_repo`, `rerank_file` | 15 min |
| M5.1.3 | ModelScreen split на три blocked sections: LLM / Embed / Rerank, каждая с свой curated + "Custom HF" + ctx (только LLM) | 1.5h |
| M5.1.4 | InstallConfig + steps.ModelDownloadStep: pull all три модели (sequential) | 30 min |
| M5.1.5 | llama-embed.yaml / llama-rerank.yaml templates параметризовать через AGMIND_EMBED_FILE / AGMIND_RERANK_FILE | 30 min |
| M5.1.6 | Tests: per-section catalog filtering + 3-model download | 30 min |

### M5.2 — Per-service inference settings

Сейчас AGMIND_CTX_SIZE / KV_CACHE / THREADS / PARALLEL применяются ко
**всем** llama-* services. Реально:
- LLM сервер — ctx 16K-256K, KV q8_0, parallel 1+
- Embed сервер — ctx обычно 8K (max), KV f16 (короткие inputs), parallel высокий
- Rerank сервер — ctx 512-2048, KV f16

| # | Task | Effort |
|---|------|:------:|
| M5.2.1 | SetupState добавить `embed_ctx_size`, `embed_kv_cache`, `embed_parallel`, `rerank_ctx_size` | 15 min |
| M5.2.2 | EnvWriteStep пишет AGMIND_LLM_CTX_SIZE / AGMIND_EMBED_CTX_SIZE / AGMIND_RERANK_CTX_SIZE (renamed) | 30 min |
| M5.2.3 | templates/services/llama-{embed,rerank}.yaml: command stanza с separate env vars | 30 min |

### M5.3 — TUI polish round 2 ("внешний вид всё ещё очко")

Concrete refinements (after M4.7.1-4 already shipped):

| # | Task | Approach |
|---|------|----------|
| M5.3.1 | Textual `Rule` widget для visual separators между form sections | Replace empty Static с Rule(line_style="heavy", color="$pip-faint") |
| M5.3.2 | Detected hardware: full-width Panel вверху wizard (не сейчас "одна строка dim в углу") | Use `Panel` + ASCII art-table layout |
| M5.3.3 | Field hint inline label-side (Tooltip widget) | f"Domain    [dim](TLS, subdomain recommended)[/dim]" |
| M5.3.4 | Empty-state визуально явный — Services screen если 0 selected | Show "[ NO SERVICES SELECTED — PRESS SPACE TO CHECK ]" banner |
| M5.3.5 | Color-coded SetupState diff в ConfirmScreen — changed fields в amber | Compare initial vs final state |
| M5.3.6 | Animated progress bar в InstallProgressScreen — current step pulse | Textual reactive interval @ 200ms |
| M5.3.7 | Help overlay (F1 keybinding) — модальный screen с full keymap | New HelpScreen pushed on F1 |
| M5.3.8 | TabbedContent или Pages для config groups (alternative к multi-step) | Investigate `from textual.widgets import TabbedContent` |

### M5.4 — agmind cluster TUI integration

User уже подключил второй LAN node. Сейчас `agmind cluster detect` есть.
Wizard ServicesScreen НЕ показывает «cluster peers — deploy to all?».

| # | Task |
|---|------|
| M5.4.1 | DomainScreen — после CF token block добавить «Cluster peers detected (N)» auto-discover banner |
| M5.4.2 | Checkbox «Deploy on this node only / Replicate to peers» |
| M5.4.3 | Ansible inventory generation если "replicate" + N peers |

**M5 total estimate:** ~7h split на 3 sub-milestones (model split / settings / TUI polish).

## M4 v0.4.0 — Cluster + plugins (deferred)

### M4.U — Phase M cluster (multi-node)

| # | Task | Effort | Notes |
|---|------|:------:|-------|
| U.1 | Ansible cluster inventory parser (2+ hosts) | 4h | role skeleton есть |
| U.2 | Inter-node WireGuard (AmneziaWG для РФ per user feedback) | 4h | TBD |
| U.3 | mDNS endpoints advertise per node | 2h | legacy *.local pattern |
| U.4 | Cluster-aware deploy в Phase L.B runner | 4h | parallel apply per node |
| U.5 | `agmind status --tui` показывает все nodes | 2h | dashboard cluster mode |

### M4.V — Plugin marketplace

| # | Task | Effort | Notes |
|---|------|:------:|-------|
| V.1 | `agmind plugin list` (от agmind.dev/plugins TBD endpoint) | 2h | endpoint TBD |
| V.2 | `agmind plugin install <id>` (download + verify + register) | 4h | |
| V.3 | Plugin metadata schema (similar to ServiceDescriptor) | 2h | |
| V.4 | Sample plugins (e.g. authelia-2fa, gpu-monitor) | 4h | |

### M4.W — Authelia 2FA + Authentication

| # | Task | Effort | Notes |
|---|------|:------:|-------|
| W.1 | TUI wizard Authelia toggle (currently service есть но wizard не запрашивает) | 30 min | |
| W.2 | Auto-provision Authelia config (users.yml + access rules) | 2h | template есть |
| W.3 | TOTP secret generation + QR code в SummaryScreen | 1h | |

---

## Known defects (DEF-*)

Resolved (M2 session 2026-05-20):
- ✅ DEF-AUDIT-FIXTURE-TESTS (resolved `3dda542`)
- ✅ DEF-AUDIT-GITIGNORE (resolved `8a6c621`)
- ✅ DEF-VULKAN-MULTI-GPU-PARSE (resolved `3dda542`)
- ✅ DEF-ROCM-VERSION-GFX1151 (resolved earlier session)
- ✅ DEF-DOCKERFILE-DIGESTS (resolved earlier session)

Open:
- 🟡 DEF-PYTEST9-CAPLOG — test_logger_emits_to_configured_stream — caplog
  empty в pytest 9.0.3 (root logger propagation change). Workaround: 1
  test skipped. Fix: переписать через propagate=True или pin pytest<9.

---

## Long-term wishlist (M5 / GA)

- **PERF** — XDNA 2 NPU support (когда Linux driver появится)
- **PERF** — Async LLM serving (vLLM ROCm когда gfx1151 supported)
- **OBS** — OpenTelemetry traces (`agmind/observability/` placeholder есть)
- **DOCS** — Full user manual + tutorial videos
- **SEC** — mTLS между services + ansible-vault для secrets
- **SEC** — RBAC в `agmind` CLI (multi-user host)
- **CLUSTER** — Auto-failover между nodes если primary падает
- **MODELS** — Auto-detect best model для hardware (memory budget aware)

---

## Session notes / reminders

- **Tip commit:** `1e4923e` on develop branch
- **GitHub remote:** botAGI/AGmind64
- **Daily commits convention:** conventional (`feat:` / `fix:` / `docs:`)
- **Branch policy:** auto-push to develop, main требует confirmation
- **Verify before commit:** `pytest -q && python3 scripts/audit_forbidden.py`
- **PR convention:** 1 PR = 1 phase (per migration spec)
