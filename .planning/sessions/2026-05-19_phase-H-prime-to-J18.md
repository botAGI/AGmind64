# Session 2026-05-19 — Phase H' → L.A-C → ROCm 7.2.3 → TUI per-service (J.1.9)

> **Branch:** `develop` · **Remote:** `botAGI/AGmind64.git` · **Tip:** `ef1c580` · **Status:** clean

## ✅ Что закрыто этой сессией

### Phase H' — Foundation Refactor (A → E)
- **H'.A** ServiceDescriptor Pydantic v2 + JSON Schema export → `templates/schemas/service.json` (ADR-0005)
- **H'.B** Split монолита `services.yaml` → 32 файла `templates/services/*.yaml` + check-jsonschema pre-commit
- **H'.C** Python compose renderer + Traefik routing (8 public services) + SSE-safe labels (ADR-0006)
- **H'.D** Observability skeleton (8 configs: Prometheus docker_sd + Alloy + Loki + Alertmanager + Grafana provisioning) + structlog rewrite + R13 gfx1151 textfile collector (ADR-0007)
- **H'.E** setuptools entry_points для backends + `agmind service` CLI + cleanup legacy 3 файла (ADR-0008)

### Fact-check (post-Phase-H')
- Removed CVE-2026-44774 hallucination из deep-dive 01
- Compose v3.9 → modern compose-spec без `version:`
- Verified sysfs paths на реальном железе

### ROCm 7.2.3 install (real Strix Halo)
- amdgpu-install_7.2.3.70203-1_all.deb (verified URL HTTP 200)
- gfx1151 detected as Agent 2 в rocminfo
- `DEF-ROCM-VERSION-GFX1151` resolved
- `docs/SETUP_ROCM_STRIX_HALO.md` с реальным install log

### Dockerfile digests filled
- `ubuntu:24.04@sha256:cdb5fd9...` (verified)
- `rocm/dev-ubuntu-24.04:7.2.3-complete@sha256:ec1b59b...` (web research, +LLAMA_HIP_UMA flag)
- `DEF-DOCKERFILE-DIGESTS` resolved

### Phase L (CI/CD + DevOps Excellence)
- **L.A** pre-commit (mypy + gitleaks + hadolint + shellcheck + ansible-lint + conventional-commits) + GH Actions matrix + release-drafter
- **L.B** `agmind deploy` — idempotent + snapshot/rollback + healthcheck wait + `agmind snapshots list`
- **L.C** `agmind gc` — containers/images/volumes/networks/models cleanup

### Phase J (TUI Wizard)
- **J.1.0** Initial wizard с form + animated logo (pyfiglet + 8-color gradient)
- **J.1.1** Logo themes (amd/red/cyan/matrix via `AGMIND_LOGO_THEME`) — replaced rainbow
- **J.1.2** STATE_PATH → `~/.local/share/agmind/` (no sudo) + big Rich Panel post-exit
- **J.1.3** Subdomain hint в UI + removed placeholder reject (user owns agmind.dev)
- **J.1.5** DeployProgressScreen — live progress + RichLog + ProgressCallback в runner
- **J.1.6** Unified TUI flow — wizard → deploy → SummaryScreen → quit (всё в одном app)
- **J.1.7** Dynamic discovery profiles + backends (zero hardcode)
- **J.1.8** Per-service selection (33 services, 5 tiers, 11 smart defaults)
- **J.1.9** Visible checkboxes (fixed `height: 1` bug) + bordered tier cards

## 📊 Текущие метрики

- **Tests:** 630/634 passed (99.4%), 2 skipped (Pilot async limitations), 2 pre-existing audit fixture bugs
- **Audit:** 175+ files, 0 findings
- **Commits на develop:** 17 за session (range f0aef25 → ef1c580)
- **ADRs:** 0005, 0006, 0007, 0008 written
- **Tools installed на dev box:** uv 0.11.15, Vulkan 1.3.275, ROCm 7.2.3, Docker 29.1.3, git, structlog, textual, pyfiglet, ansible-core

## 🚧 Pending (выбор для next session)

| Phase | Что | Cost |
|---|---|---|
| **J.2** | `agmind status --tui` — live deployment dashboard (real-time services view) | ~6h |
| **L.D** | Migration system (`agmind migrate up/down`) для schema evolution | ~5h |
| **L.E** | `agmind logs/shell/backup/restore` polish | ~8h |
| **Phase H** | Real hardware bench — docker pull llama-server + real chat smoke + tps numbers | TBD |

## 🐞 Known defects (low priority)

- `DEF-AUDIT-GITIGNORE` — audit не уважает .gitignore (frozen, требует разморозки)
- `DEF-AUDIT-FIXTURE-TESTS` — 2 test fixture bugs (pre-existing, не блокеры)

## 🎯 Точка остановки

User протестировал TUI после J.1.8, написал "пустые блоки + уебанское оформление" → исправлено в J.1.9 (`ef1c580`). Pushed. **Ждёт визуальной проверки** user'ом после `agmind setup`.

## 🔑 Critical reminders для next session

1. **CLAUDE.md правила:**
   - Reply in Russian
   - Read `AGMIND_MIGRATION_SPEC.md` перед нетривиальной работой
   - Do not edit (frozen): `scripts/audit_forbidden.py`, `migration_progress.json` (хотя session log/defects OK)
   - 1 PR = 1 phase

2. **User preferences (см. memory):**
   - feedback-no-guessing: НЕ выдумывать URLs/filenames, verify через WebFetch/curl
   - feedback-research-depth: top-2 surveys посредственны, нужны конкретные configs + версии
   - feedback-git-workflow: auto-commit+push в develop, main только с подтверждением
   - feedback-tui-devops: TUI wizard, idempotent deploys, AmneziaWG для РФ inter-node
   - user-communication-style: прямой тон, "погоди" = жёсткий стоп

3. **GitHub auth:** настроен через `gh auth login` (botAGI account). Push в `develop` работает без вопросов.

4. **ROCm permissions trick:**
   - `usermod -aG render,video beelinknode` уже сделан
   - Для immediate access без relogin: `sudo setfacl -m u:beelinknode:rw /dev/kfd /dev/dri/renderD128`
   - rocminfo в `/opt/rocm-7.2.3/bin/`, симлинк есть в `/usr/local/bin/`

5. **agmind CLI symlink:** `/usr/local/bin/agmind → ~/AGmindx86/.venv/bin/agmind` — работает из любой папки.

## Как продолжить в новой сессии

```bash
# В новой Claude Code session:
cd ~/AGmindx86
# Скажи Claude'у: «продолжаем AGmindx86, читай .planning/sessions/2026-05-19_phase-H-prime-to-J18.md»
# Claude автоматически прочитает MEMORY.md (auto-loaded)
# Дальше выбери phase: J.2 / L.D / L.E / Phase H
```

Или просто упомяни последний commit `ef1c580` и phase H'.J.1.9 — Claude разберётся.
