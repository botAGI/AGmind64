# AGmind x86 — Requirements

REQ-IDs categorized. Status: ✅ shipped | 🟡 partial | ❌ deferred to M2/M3.

## CORE compute (`COMPUTE-*`)

| ID | Requirement | Status | Notes |
|----|-------------|:------:|-------|
| COMPUTE-01 | Runtime backend abstraction (ABC) | ✅ | `agmind/compute/base.py` |
| COMPUTE-02 | CPU fallback всегда available | ✅ | `backends/cpu.py` |
| COMPUTE-03 | Vulkan RADV primary backend на gfx1151 | ✅ | `backends/vulkan.py` |
| COMPUTE-04 | ROCm/HIP secondary backend | ✅ | `backends/rocm.py` |
| COMPUTE-05 | NPU stub (XDNA 2 not supported on Linux) | ✅ | `backends/npu_stub.py` |
| COMPUTE-06 | Engine selection inside backend (llama_cpp vs vllm M2 vs infinity M2) | ✅ | `backends/_engines/` |
| COMPUTE-07 | Auto-select by AGMIND_BACKEND_PROFILE | ✅ | `_registry.py::_select_auto` |
| COMPUTE-08 | HTTP mode (llama-server URL) для production | ✅ | `clients/llama_server.py` + `http_helper.py` |
| COMPUTE-09 | AMDVLK detection + hard fail | ✅ | `backends/vulkan.py::_assert_no_amdvlk` |
| COMPUTE-10 | Mesa version warning (< 25.2.8) | ✅ | `detect.py::detect_host` warnings |
| COMPUTE-11 | GTT pool detection + sub-optimal warn | ✅ | `detect.py` |
| COMPUTE-12 | Strix Halo PCI ID detection (0x1586/0x150e) | ✅ | `detect.py::_find_amd_card` |
| COMPUTE-13 | Multi-GPU support (device_id selection) | 🟡 | env var есть, но не tested |
| COMPUTE-14 | Performance counters export (inflight, latency) | ❌ | M2 |

## LLM operations (`LLM-*`)

| ID | Requirement | Status |
|----|-------------|:------:|
| LLM-01 | generate() sync completion | ✅ |
| LLM-02 | generate_stream() streaming (SSE) | ✅ |
| LLM-03 | chat() с OpenAI messages structure | ✅ |
| LLM-04 | chat_stream() с server-side chat template | ✅ |
| LLM-05 | embed() через /v1/embeddings | ✅ |
| LLM-06 | rerank() через /v1/rerank (pooling=rank) | ✅ |
| LLM-07 | SamplingParams (temp/top_p/top_k/seed/penalties) | ✅ |
| LLM-08 | Tool/function calling support | 🟡 | passing через kwargs, не tested |
| LLM-09 | JSON mode / structured outputs | ❌ | M2 (vLLM engine) |
| LLM-10 | Speculative decoding (Eagle-v3) | ❌ | M2 |
| LLM-11 | Token-level usage tracking | ❌ | M2 |

## Models inventory (`MODELS-*`)

| ID | Requirement | Status |
|----|-------------|:------:|
| MODELS-01 | Declarative models.yaml inventory | ✅ |
| MODELS-02 | 5 tiers (S/M/L/XL/XXL) с verified-strix benchmarks | ✅ |
| MODELS-03 | Auto-tier detection по RAM | ✅ |
| MODELS-04 | Primary embed (bge-m3 Q8_0) + reranker pinned | ✅ |
| MODELS-05 | VLM support (Qwen2.5-VL + mmproj) | ✅ |
| MODELS-06 | Strix-optimized variants (0xSero DYNAMIC) | ✅ |
| MODELS-07 | Coding-focused fallback (Qwen3-Coder family) | ✅ |
| MODELS-08 | Antipatterns documented (12 entries) | ✅ |
| MODELS-09 | Download via `agmind models download` (HF URLs) | ✅ |
| MODELS-10 | Verify locally downloaded (size match) | ✅ |
| MODELS-11 | SHA256 checksum verification | ❌ | M2 |
| MODELS-12 | Progress bar / resumable downloads | ❌ | M2 |
| MODELS-13 | Models sync between cluster nodes | ❌ | M3 |

## Services registry (`SVC-*`)

| ID | Requirement | Status |
|----|-------------|:------:|
| SVC-01 | Declarative services.yaml | ✅ |
| SVC-02 | NO :latest enforced (audit) | ✅ |
| SVC-03 | digest pins (27/32 services) | 🟡 | 5 без digest |
| SVC-04 | Profile-based filtering (core/rag/ragflow/ui/observability/security) | ✅ |
| SVC-05 | Multi-profile membership (qdrant в core + rag) | ✅ |
| SVC-06 | Container resource limits (cpus/mem_limit) | ✅ |
| SVC-07 | Healthchecks definitions | 🟡 | 8/32 services с health |

## Cluster (`CLUSTER-*`)

| ID | Requirement | Status |
|----|-------------|:------:|
| CLUSTER-01 | Single-node default mode | ✅ |
| CLUSTER-02 | Multi-node inventory (master + N workers) | ✅ |
| CLUSTER-03 | Peer discovery через Ansible inventory | ✅ |
| CLUSTER-04 | 4 routing strategies | ✅ |
| CLUSTER-05 | Health probing (consecutive_failures tracking) | ✅ |
| CLUSTER-06 | mDNS auto-advertise workers | 🟡 | Ansible sets avahi-publish, не tested real |
| CLUSTER-07 | Firewall auto-open для worker IPs | ✅ |
| CLUSTER-08 | nginx round-robin upstream между workers | ❌ | M2 (сейчас single backend) |
| CLUSTER-09 | Sharded inference (llama.cpp --rpc) | ❌ | M3 |
| CLUSTER-10 | mTLS между master/workers | ❌ | M3 |
| CLUSTER-11 | Add/remove worker dynamic (no Ansible re-run) | ❌ | M3 |

## CLI (`CLI-*`)

| ID | Requirement | Status |
|----|-------------|:------:|
| CLI-01 | typer app with subcommands | ✅ |
| CLI-02 | `agmind doctor` 9 checks с fix hints | ✅ |
| CLI-03 | `agmind status --json` | ✅ |
| CLI-04 | `agmind audit` wrapper | ✅ |
| CLI-05 | `agmind models {list,download,verify,path}` | ✅ |
| CLI-06 | `agmind deploy {up,down,status,ps,logs,restart,pull}` | ✅ |
| CLI-07 | `agmind chat` interactive REPL streaming | ✅ |
| CLI-08 | `agmind embed/rerank` one-liners | ✅ |
| CLI-09 | `agmind install --profile` (Python wrapper Ansible) | ❌ | M2 |
| CLI-10 | `agmind upgrade --check/--apply/--rollback` | ❌ | M2 |
| CLI-11 | `agmind backup create/list/verify/restore` | ❌ | M2 |
| CLI-12 | `agmind config validate/diff` | ❌ | M2 |
| CLI-13 | `agmind creds {show,rotate}` | ❌ | M2 |
| CLI-14 | Shell completion (zsh/bash) | ❌ | M2 |

## Ansible orchestration (`ANS-*`)

| ID | Requirement | Status |
|----|-------------|:------:|
| ANS-01 | Idempotent install playbook | ✅ |
| ANS-02 | Single-node + multi-node inventories | ✅ |
| ANS-03 | 11 roles по responsibilities | ✅ |
| ANS-04 | Tags для selective execution (bootstrap/strix-halo/services/etc) | ✅ |
| ANS-05 | Preflight role с fail-fast | ✅ |
| ANS-06 | Strix Halo specific: AMDVLK purge + GRUB + Mesa warn | ✅ |
| ANS-07 | Render compose.yml из services.yaml | ✅ |
| ANS-08 | Secrets generation (lookup password) | ✅ |
| ANS-09 | Healthchecks post-install | ✅ |
| ANS-10 | ansible-vault для production secrets | ❌ | M2 |
| ANS-11 | Real --check tested на target host | ❌ | needs hardware |

## Documentation (`DOC-*`)

| ID | Requirement | Status |
|----|-------------|:------:|
| DOC-01 | `.planning/` + `.planning/codebase/` are active source of truth | ✅ |
| DOC-02 | Legacy migration plan retired from active docs | ✅ |
| DOC-03 | HARDWARE.md (Strix Halo setup) | ✅ |
| DOC-04 | BENCHMARKS.md (skeleton + reference numbers) | 🟡 | reference only, нет real local runs |
| DOC-05 | QUICKSTART.md (5 min setup) | ✅ |
| DOC-06 | INSTALL.md (detailed) | ✅ |
| DOC-07 | TROUBLESHOOTING.md (10 sections) | ✅ |
| DOC-08 | CLUSTER.md (multi-node) | ✅ |
| DOC-09 | ADR template + 0001 (migration) + 0002 (compute) | ✅ |
| DOC-10 | ADR-0003 (memory budgeting) | ❌ | M2 |
| DOC-11 | ADR-0004 (engine selection inside backend) | ❌ | M2 |
| DOC-12 | CONTRIBUTING.md | ❌ | M2 |
| DOC-13 | API reference (Sphinx/mkdocs auto-gen) | ❌ | M3 |
| DOC-14 | Asciinema demos | ❌ | M3 |

## Tests (`TEST-*`)

| ID | Requirement | Status |
|----|-------------|:------:|
| TEST-01 | Backend contract tests параметризованные | ✅ |
| TEST-02 | Hardware detection tests | ✅ |
| TEST-03 | HTTP client tests (mocked urllib) | ✅ |
| TEST-04 | LLMHandle ABC + LlamaServerHandle | ✅ |
| TEST-05 | Cluster router strategies | ✅ |
| TEST-06 | Services registry validation | ✅ |
| TEST-07 | Models tier resolution | ✅ |
| TEST-08 | Doctor preflight checks | ✅ |
| TEST-09 | secrets / config / env / i18n / log | ✅ |
| TEST-10 | audit_forbidden.py rules + opt-out | ✅ |
| TEST-11 | Ansible YAML syntax + role structure | ✅ |
| TEST-12 | Integration tests (real llama-server) | ❌ | M2 |
| TEST-13 | E2E test (ansible-playbook --check + smoke) | ❌ | needs hardware |
| TEST-14 | Load testing (k6/locust) | ❌ | M3 |
| TEST-15 | Mutation tests | ❌ | M3 |

## Security (`SEC-*`)

| ID | Requirement | Status |
|----|-------------|:------:|
| SEC-01 | NO :latest pinning enforced | ✅ |
| SEC-02 | credentials.txt chmod 600 | ✅ |
| SEC-03 | secrets mask in logs | ✅ |
| SEC-04 | UFW LAN-only firewall (Ansible) | ✅ |
| SEC-05 | fail2ban sshd jail | ✅ |
| SEC-06 | AMDVLK purge (security-relevant: discontinued) | ✅ |
| SEC-07 | rootful Docker (rootless не работает с ROCm) | ✅ |
| SEC-08 | Authelia opt-in (2FA SSO) | 🟡 | profile есть, не configured |
| SEC-09 | mTLS между cluster nodes | ❌ | M3 |
| SEC-10 | API keys на llama-server | 🟡 | client supports, server config не sets |
| SEC-11 | ansible-vault для secrets | ❌ | M2 |
| SEC-12 | Trivy / vulnerability scanning images | ❌ | M2 |

## Observability (`OBS-*`)

| ID | Requirement | Status |
|----|-------------|:------:|
| OBS-01 | Prometheus + Grafana + Loki + Alloy stack | ✅ |
| OBS-02 | cAdvisor per-container metrics | ✅ |
| OBS-03 | node-exporter, postgres-exporter, redis-exporter | ✅ |
| OBS-04 | Loki + Alloy log shipping | ✅ |
| OBS-05 | Grafana datasources auto-provisioned | ✅ |
| OBS-06 | Grafana dashboards JSON | ❌ | M2 |
| OBS-07 | Prometheus alert rules | ❌ | M2 |
| OBS-08 | Alertmanager Telegram/webhook routing | 🟡 | service есть, config skeleton |
| OBS-09 | agmind /metrics endpoint (Python exporter) | ❌ | M2 |
| OBS-10 | GPU metrics (rocm-smi / sysfs) | ❌ | M2 |
| OBS-11 | Tracing (OpenTelemetry) | ❌ | M3 |

## Backup/Restore (`BACKUP-*`)

| ID | Requirement | Status |
|----|-------------|:------:|
| BACKUP-01 | Volume bind-mount strategy | ✅ | docker-compose volumes |
| BACKUP-02 | `agmind backup create` CLI | ❌ | M2 |
| BACKUP-03 | `agmind restore --dry-run` | ❌ | M2 |
| BACKUP-04 | Backup manifest (MANIFEST.txt + checksums) | ❌ | M2 |
| BACKUP-05 | Encrypted backups (ansible-vault или age) | ❌ | M3 |
| BACKUP-06 | DR drill script | ❌ | M3 |

## Performance (`PERF-*`)

| ID | Requirement | Status |
|----|-------------|:------:|
| PERF-01 | Reference benchmarks documented (R3/R-llm-models) | ✅ |
| PERF-02 | Local pytest-benchmark suite | ❌ | M2 |
| PERF-03 | Smoke benchmark в `agmind deploy` post-install | ❌ | M2 |
| PERF-04 | Load testing scenarios (k6) | ❌ | M3 |
| PERF-05 | Profile-specific budgets (tier × workload) | ❌ | M3 |

## Total

- ✅ Shipped: 73
- 🟡 Partial: 9
- ❌ Deferred: 37
- **Total tracked: 119 REQ-IDs**
