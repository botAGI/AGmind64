# AGmind x86 — Backlog

Структурированный backlog задач для next sessions. Сгруппировано по
**urgency × effort**. См. ROADMAP.md для milestone planning.

Legend:
- 🔴 **Critical** — без этого нельзя deploy
- 🟡 **High** — production-readiness
- 🟢 **Medium** — UX polish
- 🔵 **Low** — nice-to-have

## P0 — Critical (Phase H — Hardware validation)

| # | Task | Effort | REQ-ID |
|---|------|:------:|--------|
| H1 | `sudo apt install vulkan-tools mesa-vulkan-drivers libvulkan1` + verify `vulkaninfo --summary` показывает RADV GFX1151 | 30 min | COMPUTE-03 |
| H2 | Mesa 26+ через `ppa:kisak/kisak-mesa` + verify `glxinfo` | 30 min | COMPUTE-10 |
| H3 | GRUB cmdline `ttm.pages_limit=30788044` + reboot + verify `mem_info_gtt_total ~117 GiB` | 30 min | COMPUTE-11 |
| H4 | `sudo usermod -aG render,video,docker $USER && newgrp render` | 5 min | doctor warnings |
| H5 | `pip install -e ".[dev]"` + run `pytest -m backend_any` (expect 200+ green) | 1 hour | TEST-01..10 |
| H6 | Run `agmind doctor` — expect 9/9 ✓ | 5 min | CLI-02 |
| H7 | Build llama-cpp-python с Vulkan: `CMAKE_ARGS='-DGGML_VULKAN=ON -DGGML_NATIVE=OFF' pip install --no-binary llama-cpp-python 'llama-cpp-python>=0.3.23'` (~10-15 min compile) | 30 min | COMPUTE-03 |
| H8 | `agmind models download --tier M` (gemma-4-26B-A4B-it ~16.9 GB) | 30 min (bandwidth) | MODELS-09 |
| H9 | `docker compose pull` для всех 32 services (~200 GB images total) | 1-3 hours | SVC-01 |
| H10 | Build self-hosted: `docker build -f docker/Dockerfile.{base,cpu,vulkan,rocm}` | 30 min | F1-F4 |
| H11 | Ansible dry-run: `ansible-playbook install.yml --check` | 30 min | ANS-11 |
| H12 | Real install: `sudo ansible-playbook install.yml` | 1 hour | ANS-01 |
| H13 | Smoke chat: `agmind chat` с реальным LLM, verify coherent reply | 15 min | LLM-04 |
| H14 | Benchmark: `agmind embed "test"` + measure latency | 15 min | LLM-05 |
| H15 | Update BENCHMARKS.md с реальными numbers | 30 min | DOC-04 |

**Phase H total estimate:** 7-12 hours зависит от bandwidth для downloads.

## P0 — Critical (Phase I — Git baseline)

| # | Task | Effort |
|---|------|:------:|
| I1 | User manual: `rm -rf .git/` (classifier blocks мне) | 1 min |
| I2 | `git init -b main && git config user.{email,name} ...` | 5 min |
| I3 | `.gitignore` validate + check that legacy/ + AGmind.zip excluded | 5 min |
| I4 | `git add . && git status` (review staged), `git commit -m "Initial: AGmind x86 v0.1.0-dev"` | 15 min |
| I5 | Update ADR-0001 status: `proposed` → `accepted` | 10 min |
| I6 | Update ADR-0002 status: `proposed` → `accepted` | 10 min |
| I7 | Write ADR-0003 "Memory budgeting on Strix Halo" (на основе R10) | 30 min |
| I8 | Write ADR-0004 "Engine selection inside backend (M1 llama_cpp only, M2 vllm/infinity)" | 30 min |
| I9 | AGMIND_MIGRATION_SPEC.md — расширенный changelog (D1-D4 + cleanup entries) | 30 min |
| I10 | migration_progress.json: phase_status A-G → done, populate completion_dates | 15 min |
| I11 | `git tag v0.1.0-dev` + commit message | 5 min |

**Phase I total:** ~3 hours.

## P1 — High (Phase J — Day-2 ops CLI)

| # | Task | Effort | REQ-ID |
|---|------|:------:|--------|
| J1 | `agmind install --profile <X>` — Python wrapper над `ansible-playbook install.yml` | 1 hour | CLI-09 |
| J2 | `agmind upgrade --check` — read `versions.env`, diff vs running compose | 2 hours | CLI-10 |
| J3 | `agmind upgrade --apply` — atomic state migrations + tarball backup | 4 hours | CLI-10 |
| J4 | `agmind upgrade --rollback <schema_version>` | 2 hours | CLI-10 |
| J5 | `agmind backup create [--name X]` — tar `/var/lib/agmind/{postgres,qdrant,redis}` + `.env` | 2 hours | BACKUP-02 |
| J6 | `agmind backup list` + verify (manifest.txt + checksums) | 1 hour | BACKUP-03 |
| J7 | `agmind backup verify --dry-run` | 1 hour | BACKUP-03 |
| J8 | `agmind backup restore --name X` | 2 hours | BACKUP-04 |
| J9 | `agmind config validate` (env-placeholders / versions-consistent / compose-schema) | 2 hours | CLI-12 |
| J10 | `agmind config diff` — show planned vs running | 1 hour | CLI-12 |
| J11 | `agmind creds show [--show]` (chmod-600-aware, masked-by-default) | 1 hour | CLI-13 |
| J12 | `agmind creds rotate [--service X]` | 2 hours | CLI-13 |
| J13 | `agmind state {get,set,migrate}` (state.py API в CLI) | 2 hours | — |
| J14 | typer shell completion install `agmind --install-completion zsh/bash` | 1 hour | CLI-14 |
| J15 | Tests для всех новых CLI commands | 2 hours | TEST |

**Phase J total:** ~26 hours.

## P1 — High (Phase K — Observability)

| # | Task | Effort | REQ-ID |
|---|------|:------:|--------|
| K1 | Grafana dashboard JSON: "AGmind LLM performance" (tokens/sec, latency p50/p95/p99) | 3 hours | OBS-06 |
| K2 | Grafana dashboard JSON: "Strix Halo GPU" (GTT usage, temperature, Mesa shader cache) | 2 hours | OBS-06 |
| K3 | Grafana dashboard JSON: "Containers overview" (cAdvisor) | 1 hour | OBS-06 |
| K4 | Grafana dashboard JSON: "Logs explorer" (Loki + Alloy) | 1 hour | OBS-06 |
| K5 | Grafana dashboard JSON: "Cluster routing" (peers alive, inflight, balance) | 2 hours | OBS-06 |
| K6 | Prometheus alert rules: high memory (>85% GTT), slow inference (p99 > 5s), peer down | 2 hours | OBS-07 |
| K7 | Alertmanager Telegram bot integration | 2 hours | OBS-08 |
| K8 | `agmind/exporter.py` — Python prom_client wrapper, /metrics endpoint | 4 hours | OBS-09 |
| K9 | GPU metrics: `rocm-smi --showmeminfo all` parsed → Prometheus textfile collector | 2 hours | OBS-10 |
| K10 | Provision dashboards через Ansible (UID/version-stable JSON) | 1 hour | OBS-06 |

**Phase K total:** ~20 hours.

## P1 — High (Misc production polish)

| # | Task | Effort | REQ-ID |
|---|------|:------:|--------|
| M1 | HTTP retry/backoff в LlamaServerClient (urllib3-style) | 2 hours | — |
| M2 | Connection pool в LlamaServerClient (keep-alive) | 1 hour | — |
| M3 | Async API (`AsyncLlamaServerClient` через httpx) | 4 hours | — |
| M4 | Models SHA256 verify (HF предоставляет в /resolve/main/{file}/raw?download=true) | 2 hours | MODELS-11 |
| M5 | Models download progress bar (tqdm или ASCII) | 1 hour | MODELS-12 |
| M6 | ansible-vault для secrets вместо `lookup('password', ...)` | 3 hours | ANS-10, SEC-11 |
| M7 | Trivy scan в CI workflow | 1 hour | SEC-12 |
| M8 | Tests for `agmind/cli/` (mock typer.CliRunner) | 3 hours | TEST |
| M9 | Tests for `agmind/cluster/peer.probe_*` с mock LlamaServerClient | 2 hours | TEST |
| M10 | Multi-GPU device_id selection tested | 2 hours | COMPUTE-13 |

## P2 — Medium (Documentation polish)

| # | Task | Effort | REQ-ID |
|---|------|:------:|--------|
| D1 | CONTRIBUTING.md | 1 hour | DOC-12 |
| D2 | CHANGELOG.md (под v0.1.0-dev released) | 30 min | — |
| D3 | docs/API.md — public Python API reference | 2 hours | DOC-13 |
| D4 | docs/UPGRADE.md — version migration guide | 1 hour | — |
| D5 | docs/SECURITY.md — threat model + best practices | 2 hours | — |
| D6 | docs/DEVELOPMENT.md — local dev setup без real hardware | 2 hours | — |
| D7 | asciinema demos: install / chat / models download | 2 hours | DOC-14 |
| D8 | Architecture diagrams (mermaid) embedded в README | 1 hour | — |

## P3 — Low (Niceties)

| # | Task | Effort |
|---|------|:------:|
| N1 | MCP server для agmind ops (AI agent integration) | 8 hours |
| N2 | Sphinx auto-gen API reference | 4 hours |
| N3 | mkdocs static site generation | 3 hours |
| N4 | GitHub Pages deployment | 1 hour |
| N5 | `agmind doctor --fix` (interactive applying suggestions) | 4 hours |
| N6 | Plugin system для custom backends (entry_points) | 6 hours |
| N7 | Web admin UI (FastAPI + htmx) | 16 hours |
| N8 | Telegram bot для agmind status / chat | 4 hours |

## GSD codebase update (immediate next session priority)

`.planning/codebase/` skeleton — нужно создать новый под x86 проект.
Background agent scan уже запущен. После его возврата:

| # | File | Status |
|---|------|:------:|
| C1 | `.planning/codebase/INDEX.md` (file-by-file table) | ⏳ in-flight via Explore agent |
| C2 | `.planning/codebase/DEPENDENCIES.md` (import graph) | ⏳ in-flight |
| C3 | `.planning/codebase/ARCHITECTURE.md` (3-layer overview) | next session |
| C4 | `.planning/codebase/DATAFLOW.md` (request → backend → engine → model) | next session |
| C5 | `.planning/codebase/CONCERNS.md` (cross-cutting: security/obs/i18n/secrets) | next session |
| C6 | `.planning/codebase/PITFALLS.md` (24+ известных gotchas из recons) | next session |
| C7 | `.planning/codebase/INVARIANTS.md` (no :latest, RADV mandatory, etc) | next session |
| C8 | `.planning/codebase/EXTENSION_POINTS.md` (где добавлять new engines / services / tiers) | next session |

## Notes — какие direction'ы я не успел

1. **Real hardware validation** — нужно vulkan-tools/ROCm/llama-cpp install
2. **Git init** — classifier blocks `rm -rf .git`, user сам
3. **pytest run** — нет pytest installed на dev
4. **Docker build** — нет docker daemon access (или есть? not tested)
5. **Ansible playbook real run** — needs `sudo` permissions

## Recommended next session focus

**Option A (rec):** Phase H (hardware validation) + Phase I (git baseline) —
закрывают critical gaps, делают проект "real" вместо "skeleton".

**Option B:** Phase J (day-2 ops CLI) — usable production deployment без
hardware testing.

**Option C:** Phase K (observability) — production monitoring + alerting.

**Option D:** GSD codebase maps — для long-term maintainability и
onboarding новых contributors.

Я бы выбрал **A → I → C (GSD docs) → J → K** в этом порядке.
