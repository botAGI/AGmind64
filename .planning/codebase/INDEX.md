# AGmind codebase index

Scanned 2026-05-19 via Explore agent. **39 Python files (4753 LOC) в
agmind/, 25 test files, 12 Ansible roles, 2 YAML catalogs.**

## agmind/ Python tree

### Root (6 files)

| File | Purpose | Key exports | Lines |
|------|---------|-------------|------:|
| `agmind/__init__.py` | Package API & version | `__version__` | 10 |
| `agmind/__main__.py` | CLI entry (`python -m agmind`) | `app()` | 9 |
| `agmind/log.py` | Logging utilities | `setup()`, `logger()` | 42 |
| `agmind/_env.py` | .env parser без python-dotenv | `parse_env_text/file()`, `env_get()`, `shell_quote()` | 77 |
| `agmind/secrets.py` | Credentials chmod 600 | `write_creds()`, `read_creds()`, `mask_value()` | 110 |
| `agmind/models.py` | Tier-based GGUF resolver | `load_models_registry()`, `detect_tier()`, `resolve_llm/embed/reranker/vlm()` | 319 |

### agmind/compute/ (5 root + backends + clients)

| File | Purpose | Lines |
|------|---------|------:|
| `compute/__init__.py` | Compute backend abstraction API | 27 |
| `compute/base.py` | ABC: Backend / DeviceInfo / LLMHandle | 235 |
| `compute/config.py` | ComputeConfig + Profile enum | 101 |
| `compute/detect.py` | HW detection (sysfs + vulkaninfo + rocminfo) | 376 |
| `compute/_registry.py` | Backend registry + auto-select | 138 |

### agmind/compute/backends/ (5 files)

| File | Purpose | Lines |
|------|---------|------:|
| `backends/__init__.py` | Module stub | 9 |
| `backends/cpu.py` | CPU backend (llama-cpp-cpu) | 139 |
| `backends/vulkan.py` | Vulkan RADV backend | 203 |
| `backends/rocm.py` | ROCm/HIP backend | 180 |
| `backends/npu_stub.py` | XDNA 2 NPU stub | 61 |

### agmind/compute/backends/_engines/ (6 files)

| File | Purpose | Lines |
|------|---------|------:|
| `_engines/__init__.py` | Module stub | 11 |
| `_engines/llama_cpp_cpu.py` | llama-cpp-python (CPU build) | 151 |
| `_engines/llama_cpp_hip.py` | llama-cpp-python (HIP/ROCm build) | 101 |
| `_engines/llama_cpp_vulkan.py` | llama-cpp-python (GGML_VULKAN) | 112 |
| `_engines/llama_server_handle.py` | LLMHandle поверх HTTP REST | 144 |
| `_engines/http_helper.py` | DRY `try_http_handle/embed/rerank` | 64 |

### agmind/compute/clients/ (2 files)

| File | Purpose | Lines |
|------|---------|------:|
| `clients/__init__.py` | Public API | 18 |
| `clients/llama_server.py` | OpenAI-compatible REST client (urllib stdlib, SSE streaming) | 451 |

### agmind/cli/ (5 files)

| File | Purpose | Lines |
|------|---------|------:|
| `cli/__init__.py` | typer app + subcommand wiring | 118 |
| `cli/models_cmd.py` | `agmind models {list,download,verify,path}` | 223 |
| `cli/deploy_cmd.py` | `agmind deploy {up,down,status,ps,logs,restart,pull}` | 95 |
| `cli/chat_cmd.py` | `agmind chat` interactive REPL | 102 |
| `cli/embed_cmd.py` | `agmind embed` + `agmind rerank` | 79 |

### agmind/cluster/ (3 files)

| File | Purpose | Lines |
|------|---------|------:|
| `cluster/__init__.py` | Public API | 26 |
| `cluster/peer.py` | Peer + PeerHealth + load_cluster_config + probe_peer/all | 151 |
| `cluster/router.py` | 4 routing strategies (round-robin/least-loaded/sticky/random) | 74 |

### agmind/services/ (2 files)

| File | Purpose | Lines |
|------|---------|------:|
| `services/__init__.py` | Public API | 23 |
| `services/registry.py` | Service catalog + validate_no_latest + YAML fallback parser | 252 |

### agmind/diagnostics/ (2 files)

| File | Purpose | Lines |
|------|---------|------:|
| `diagnostics/__init__.py` | API | 10 |
| `diagnostics/doctor.py` | 9 preflight checks с fix hints | 379 |

### agmind/config/ (2 files)

| File | Purpose | Lines |
|------|---------|------:|
| `config/__init__.py` | API | 10 |
| `config/env.py` | render_env (placeholder substitution) + write_env (atomic) | 53 |

### agmind/i18n/ (1 file + 2 JSON)

| File | Purpose | Lines |
|------|---------|------:|
| `i18n/__init__.py` | `t()` + `detect_lang()` | 70 |
| `i18n/en.json` | English translations | — |
| `i18n/ru.json` | Russian translations | — |

## tests/ (25 files, 306 test functions)

| File | Coverage |
|------|----------|
| `tests/conftest.py` | Pytest fixtures (has_vulkan/has_rocm/has_strix_halo/has_llama_cpp/clean_env) |
| `tests/test_log.py` | 7 tests — logging setup/level/env |
| `tests/test_env.py` | 16 tests — .env parsing edge cases |
| `tests/test_secrets.py` | 19 tests — chmod 600 / mask / validate keys |
| `tests/test_models.py` | 23 tests — tier resolve / antipatterns |
| `tests/test_i18n.py` | 12 tests — translation + lang detection |
| `tests/test_config_env.py` | 10 tests — render_env / write_env |
| `tests/test_cli.py` | 6 tests — typer app construction |
| `tests/test_audit_script.py` | 13 tests — audit rules + opt-out behaviour |
| `tests/test_ansible_layout.py` | 10 tests — Ansible structure validation |
| `tests/compute/test_detect.py` | 8 tests — detect_host + Strix Halo sysfs |
| `tests/compute/test_config.py` | 16 tests — Profile / env vars |
| `tests/compute/test_contract.py` | 19 tests — Backend ABC contract по backend |
| `tests/compute/test_engines.py` | 22 tests — engine selection / M2 NotImplementedError |
| `tests/compute/test_llmhandle.py` | 17 tests — ABC fallbacks + LlamaServerHandle |
| `tests/compute/test_llama_server_client.py` | 31 tests — HTTP client + SSE + sampling |
| `tests/compute/test_registry.py` | 11 tests — auto-select decision matrix |
| `tests/cluster/test_router.py` | 17 tests — 4 routing strategies + cluster.yaml |
| `tests/services/test_registry.py` | 23 tests — service loading / validate_no_latest |
| `tests/diagnostics/test_doctor.py` | 12 tests — preflight checks |

## scripts/ (1 file)

| File | Purpose |
|------|---------|
| `scripts/audit_forbidden.py` | Forbidden-pattern audit (cuda/aarch64/nvcr.io/etc); 7 rules + RULES self-reference opt-out |

## ansible/ (31 files, 11 roles)

| Role | Tasks |
|------|-------|
| `preflight` | x86_64 / kernel / Strix Halo / disk |
| `bootstrap` | apt + Vulkan tooling + agmind user/group + sysctl |
| `strix_halo` | AMDVLK purge + GRUB ttm.pages_limit + Mesa warn |
| `docker` | docker-ce install + daemon.json + GPU access verify |
| `agmind_python` | venv + pip install -e + CLI wrapper |
| `models` | Tier autodetect + GGUF download (HF) |
| `services` | Render compose.yml + nginx + bring up |
| `observability` | Prometheus + Grafana + Loki + Alloy provision |
| `security` | UFW + fail2ban + opt Authelia |
| `smoke_test` | `agmind doctor` + compose ps verify |
| `cluster` | Render cluster.yaml + firewall к worker IPs |

## templates/ (2 YAML files)

| File | Schema |
|------|--------|
| `templates/services.yaml` | schema_version: 1 — 32 services, 27 digest-pinned |
| `templates/models.yaml` | schema_version: 1 — 5 LLM tiers + embed + rerank + VLM + 12 antipatterns |

## docs/ (10 MD + 3 ADR)

| File | Purpose |
|------|---------|
| `docs/MIGRATION_PLAN.md` | Phase A-G план + OQ-1..7 |
| `docs/HARDWARE.md` | Host setup (BIOS/kernel/sysctl/Mesa/AMDVLK) |
| `docs/BENCHMARKS.md` | Reference numbers из R3/R-llm-models |
| `docs/QUICKSTART.md` | 5-min setup |
| `docs/INSTALL.md` | Detailed walkthrough |
| `docs/TROUBLESHOOTING.md` | 10-section cookbook |
| `docs/CLUSTER.md` | Multi-node setup |
| `docs/adr/0000-template.md` | ADR template |
| `docs/adr/0001-migration-to-x86-strix-halo.md` | Migration ADR (proposed) |
| `docs/adr/0002-compute-backend-abstraction.md` | Compute layer ADR (proposed) |

## .planning/ (10 files)

| File | Purpose |
|------|---------|
| `PROJECT.md` | Milestone charter |
| `REQUIREMENTS.md` | 119 REQ-IDs |
| `STATE.md` | Current position + key decisions log |
| `ROADMAP.md` | Phase H-K plan |
| `BACKLOG.md` | Prioritized P0-P3 tasks |
| `codebase/INDEX.md` | This file |
| `codebase/DEPENDENCIES.md` | Import graph |
| `codebase/ARCHITECTURE.md` | 3-layer overview |
| `research/x86-migration/R*.md` | 12 deep recon reports |
| `sessions/2026-05-19-overnight.md` | Migration session log |
