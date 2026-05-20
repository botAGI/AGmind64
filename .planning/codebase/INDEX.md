# AGmind codebase index

Scanned 2026-05-20 (post-Phase P). Replaces 2026-05-19 baseline. Significant
growth: install/ + ops/ + migrations/ + observability/ + version_check +
TUI install screen.

- **Python:** 74 files in `agmind/`, 35 test files in `tests/`
- **Service templates:** 33 YAML descriptors + JSON Schema
- **ADRs:** 13 (Phase O capability graph + Phase P version check added)
- **Ansible roles:** 11
- **R-recons:** 17 + 4 baselines + 6 deep dives
- **Workflows:** 3 (ci + release-drafter + **version-check** — Phase P)
- **Tests:** 782 passing, 0 skipped (post-Phase P)

## agmind/ — Python tree

### Root (6 files)

| File | Lines | Purpose |
|------|------:|---------|
| `__init__.py` | 10 | Package version |
| `__main__.py` | 9 | CLI entry (`python -m agmind`) |
| `log.py` | 140 | structlog logger (Phase H'.D) |
| `models.py` | 319 | GGUF tier resolver + registry |
| `secrets.py` | 110 | Credentials chmod 600 |
| `_env.py` | 77 | Env parser (без python-dotenv) |

### cli/ (9 files, 1756 LOC)

| File | Lines | Purpose |
|------|------:|---------|
| `__init__.py` | 683 | Typer app + 17 commands incl. `install` + `migrate` + ops |
| `models_cmd.py` | 223 | `agmind models {list,pull,info,tier}` |
| `ops_cmd.py` | 173 | `agmind logs/shell/backup/restore` (L.E) |
| `deploy_cmd.py` | 163 | `agmind deploy/rollback` (L.B) |
| `service_cmd.py` | 158 | `agmind service {list,scaffold,validate}` |
| `migrate_cmd.py` | 120 | `agmind migrate up/down` (L.D) |
| `chat_cmd.py` | 102 | `agmind chat` REPL |
| `embed_cmd.py` | 79 | `agmind embed` |
| `render_cmd.py` | 75 | `agmind render compose` |

### cli/tui/ (7 files, 1961 LOC)

| File | Lines | Purpose |
|------|------:|---------|
| `setup_wizard.py` | 794 | AgmindSetupApp wizard (J + N.G — model + ctx + threads + parallel) |
| `deploy_screen.py` | 262 | DeployProgressScreen (J.1.5) |
| `status_dashboard.py` | 251 | StatusDashboardApp (J.2 — `agmind status --tui`) |
| `install_screen.py` | 246 | InstallProgressScreen (N — orchestrator UI) |
| `summary_screen.py` | 233 | SummaryScreen post-deploy |
| `logo.py` | 163 | AnimatedLogo (pyfiglet + AGMIND_LOGO_DISABLE_ANIMATION) |
| `__init__.py` | 12 | Module exports |

### compute/ (18 files, 2599 LOC)

5 root + 6 backends + 5 _engines + 2 clients. См. ARCHITECTURE.md для DAG.

| File | Lines | Notable |
|------|------:|---------|
| `detect.py` | 433 | Multi-GPU Vulkan parser (post-fix `3dda542`) |
| `base.py` | 235 | Backend ABC, LLMHandle |
| `_registry.py` | 159 | entry_points discovery (ADR-0008) |
| `config.py` | 101 | Profile reading |
| `__init__.py` | 27 | Public API |
| `backends/vulkan.py` | 203 | RADV path |
| `backends/rocm.py` | 180 | HIP path |
| `backends/cpu.py` | 139 | Fallback |
| `backends/npu_stub.py` | 61 | XDNA placeholder |
| `backends/_engines/llama_cpp_cpu.py` | 151 | llama-cpp-python CPU |
| `backends/_engines/llama_server_handle.py` | 144 | HTTP client to llama-server |
| `backends/_engines/llama_cpp_vulkan.py` | 112 | llama-cpp-python Vulkan |
| `backends/_engines/llama_cpp_hip.py` | 101 | llama-cpp-python HIP |
| `backends/_engines/http_helper.py` | 64 | Shared HTTP helpers |
| `clients/llama_server.py` | 451 | LlamaServerClient (chat + embed + rerank) |

### deploy/ (5 files, 1053 LOC) — Phase L.B/C

| File | Lines | Purpose |
|------|------:|---------|
| `runner.py` | 317 | deploy() orchestrator: render→snapshot→up→wait_healthy→rollback |
| `gc.py` | 328 | `agmind gc` containers/images/volumes/networks/models |
| `snapshot.py` | 192 | SnapshotManager (retention=10) |
| `diff.py` | 168 | compute_diff + format_diff |
| `__init__.py` | 48 | Exports |

### install/ (4 files, 1021 LOC) — Phase N (NEW)

| File | Lines | Purpose |
|------|------:|---------|
| `orchestrator.py` | 228 | InstallOrchestrator + ProgressEvent + InstallConfig + step sequencer |
| `steps.py` | 531 | 6 steps: doctor / bootstrap (ansible+sudo) / pull / model / env_write / deploy |
| `models.py` | 221 | CURATED_MODELS + CTX/KV/THREADS/PARALLEL presets |
| `__init__.py` | 41 | Exports |

### ops/ (3 files, 355 LOC) — Phase L.E (NEW)

| File | Lines | Purpose |
|------|------:|---------|
| `backup.py` | 244 | create_backup / restore_backup tarball + metadata.json |
| `exec.py` | 111 | docker compose logs + exec (`agmind logs/shell`) |
| `__init__.py` | 5 | Package marker |

### migrations/ (6 files, 214 LOC) — Phase L.D (NEW)

| File | Lines | Purpose |
|------|------:|---------|
| `runner.py` | 132 | MigrationRunner: discover + up + down |
| `state.py` | 83 | SchemaState (persisted ~/.local/share/agmind/schema.json) |
| `base.py` | 54 | Migration ABC + MigrationContext |
| `__init__.py` | 29 | Public API |
| `versions/v001_initial.py` | 25 | Baseline migration (placeholder) |
| `versions/__init__.py` | 5 | Discovery marker |

### services/ (5 files, 1231 LOC) — Phase H'.B/C + O (NEW capability + bindings)

| File | Lines | Purpose |
|------|------:|---------|
| `renderer.py` | 404 | render_compose + inject_capability_env (Phase O.B) |
| `registry.py` | 309 | load_descriptors + legacy bridge |
| `capability_bindings.py` | 155 | BINDINGS table (vector_db / llm_inference / dify_external_kb) |
| `compatibility.py` | 149 | check_service_compatibility (soft warnings only post O.fix) |
| `__init__.py` | 23 | Exports |

### schemas/ (2 files, 377 LOC) — ADR-0005 + Phase O

| File | Lines | Purpose |
|------|------:|---------|
| `service.py` | 346 | ServiceDescriptor + provides/consumes/conflicts_with |
| `__init__.py` | 31 | Exports |

### diagnostics/ (2 files, 389 LOC)

| File | Lines | Purpose |
|------|------:|---------|
| `doctor.py` | 379 | 9 checks (multi-GPU Vulkan after `3dda542`) |
| `__init__.py` | 10 | Public API |

### cluster/ (3 files, 251 LOC)

| File | Lines | Purpose |
|------|------:|---------|
| `peer.py` | 151 | Peer + ClusterConfig + probe_peer |
| `router.py` | 74 | RoutingStrategy + choose_peer |
| `__init__.py` | 26 | Exports |

### config/ + i18n/ + observability/ (4 files, 149 LOC)

| File | Lines | Status |
|------|------:|--------|
| `config/env.py` | 53 | render_env + write_env |
| `config/__init__.py` | 10 | Exports |
| `i18n/__init__.py` | 70 | Lang detect + lookup (НЕ подключен в TUI) |
| `observability/__init__.py` | 16 | OpenTelemetry placeholder |

## tests/ (35 files, ~6500 LOC, 782 tests)

| File | Lines | Coverage |
|------|------:|----------|
| `test_ops_backup.py` | 366 | Phase L.E backup/restore |
| `test_migrations.py` | 346 | Phase L.D schema migrations |
| `test_service_schema.py` | 311 | Pydantic ServiceDescriptor validation |
| `test_tui_setup.py` | 308 | Wizard state + Pilot tests (un-skipped via env) |
| `test_install_orchestrator.py` | 299 | Phase N orchestrator + steps |
| `test_renderer.py` | 297 | compose rendering + Traefik labels |
| `test_service_compatibility.py` | 290 | Phase O compatibility (corrected post-fix) |
| `test_models.py` | 264 | Tier resolver |
| `test_status_dashboard.py` | 210 | Phase J.2 dashboard |
| `test_observability_configs.py` | 207 | Prometheus/Loki/Grafana templates |
| `test_ops_exec.py` | 186 | logs/shell wrapper |
| `test_deploy.py` | 184 | Phase L.B runner + snapshot |
| `test_ansible_layout.py` | 158 | Ansible role structure |
| `test_secrets.py` | 143 | Credentials |
| `test_install_model_detect.py` | 141 | Phase N.H detect+reuse logic |
| `test_services_descriptors.py` | 132 | Descriptor loading |
| `test_gc.py` | 131 | Phase L.C gc |
| `test_audit_script.py` | 131 | audit_forbidden.py (post-fix) |
| `test_version_check.py` | 122 | **Phase P** version scanner |
| `test_log.py` | 114 | structlog |
| `test_config_env.py` | 108 | .env rendering |
| `test_env.py` | 101 | env parsing |
| `test_install_models.py` | 87 | CURATED_MODELS catalog |
| `test_i18n.py` | 86 | i18n strings |
| **compute/** | 1187 | 7 files: contract/detect/registry/config/engines/handle/client |
| **services/test_registry.py** | 226 | services YAML load |
| **cluster/test_router.py** | 225 | peer routing |
| **diagnostics/test_doctor.py** | 119 | doctor.run_preflight |

## templates/

- `services/` — **33** descriptors (post-O annotations: provides/consumes)
- `schemas/service.json` — JSON Schema export (ADR-0005)
- `version_holds.yaml` — Phase P upstream pin holds
- `observability/` — prometheus.yml + loki.yml + grafana/provisioning + alloy/config.alloy + alert rules
- `traefik/` — middlewares + transport
- `models.yaml` — GGUF tier catalog

## ansible/ — 11 roles

agmind_python · bootstrap · cluster · docker · models · observability · preflight · security · services · smoke_test · strix_halo

Playbook: `install.yml` (Phase N orchestrator вызывает через subprocess).

## docs/adr/ — 13 ADRs

| # | Title |
|--:|-------|
| 0 | Template |
| 1 | Migration to x86 Strix Halo |
| 2 | Compute backend abstraction |
| 3 | Memory budgeting Strix Halo |
| 4 | Engine selection within backend |
| 5 | ServiceDescriptor schema |
| 6 | Traefik routing + Python renderer |
| 7 | Observability stack |
| 8 | Plugin system + legacy cleanup |
| 9 | **State migration system** (Phase L.D) |
| 10 | **End-to-end installer** (Phase N) |
| 11 | **Service capability graph** (Phase O, with 2026-05-20 amendment) |
| 12 | **Upstream version check** (Phase P) |

Other docs: QUICKSTART · INSTALL · CLUSTER · HARDWARE · SETUP_ROCM_STRIX_HALO · SETUP_CLOUDFLARE_DOMAIN · TROUBLESHOOTING · **BENCHMARKS** (Phase H result row added) · MIGRATION_PLAN

## scripts/

| File | Purpose |
|------|---------|
| `audit_forbidden.py` | Forbidden pattern scanner — unfrozen post `3dda542` |
| `version_check.py` | **Phase P** upstream version scanner + markdown report |
| `export_schemas.py` | Pydantic → templates/schemas/service.json |
| `migrate_services_to_descriptors.py` | Legacy → ServiceDescriptor conversion |
| `amdgpu_textfile.sh` | R13 textfile collector for node-exporter |

## .planning/research/x86-migration/ — 17 recons + extras

R0 autonomous workflow · R1 PyTorch+ROCm docker · R2 Vulkan RADV vs AMDVLK · R3 llama.cpp Vulkan+HIP · R4 vLLM ROCm · R5 TEI embed/rerank · R7 Docling alternatives · R10 Strix Halo BIOS UMA · R11 RAGFlow alternatives · R12 Versions (x86, May 2026) · **R14 backup/restore gaps** (Phase L.E) · **R15 Phase H bench protocol** · **R16 Qwen Strix Halo flags** · R16-bench-vulkan-qwen-b9049.log

Deep dives: 01 Traefik+llama-server · 02 Hydra profiles · 03 Observability pipeline · 04 Service onboarding · 05 Go vs Python · 06 Steal-fest

## .github/workflows/

- `ci.yml` — pytest + mypy + audit
- `release-drafter.yml` — auto release notes
- **`version-check.yml`** — Phase P weekly cron → issue update

## Top-level

`README.md` · `AGMIND_MIGRATION_SPEC.md` · `CLAUDE.md` · `pyproject.toml` · `Makefile` · `migration_progress.json`
