# AGmind x86 — Roadmap

**Current milestone:** v0.1.0-dev (alpha, migration complete)
**Next milestone:** v0.2.0 (beta, production hardening)
**Target stable:** v1.0.0

## Milestones

### v0.1.0-dev — Migration alpha (✅ SHIPPED 2026-05-19)

7 phases A-G complete (см. `.planning/STATE.md`):

- ✅ A — Inventory & Plan (audit baseline + migration plan)
- ✅ B — Legacy quarantine (физический cleanup в этой сессии)
- ✅ C — Compute abstraction skeleton (4 backends + 3 engines)
- ✅ D — Vulkan/ROCm backends (HTTP client + LlamaServerHandle)
- ✅ E — CLI + diagnostics + cluster (17+ subcommands, 4 routing strategies)
- ✅ F — Docker + CI + pre-commit
- ✅ G — Docs + release prep (QUICKSTART/INSTALL/TROUBLESHOOTING/CLUSTER)

**Outcome:** 39 Python modules, 25 test files (306 tests), 31 Ansible files,
32 services, 5 model tiers, 12 recon reports, 3 ADRs, 4 user guides.

### v0.2.0 — Production hardening (NEXT, ~1-2 weeks)

Phases H-K. Focus: **make real deploy possible + day-2 ops**.

#### Phase H — Hardware validation (1-2 days)

**Goal:** validate full install flow на real Strix Halo dev машине.

- [ ] H1: `sudo apt install vulkan-tools mesa-vulkan-drivers libvulkan1`
- [ ] H2: Mesa kisak PPA upgrade
- [ ] H3: GRUB cmdline + reboot → verify GTT pool ~117 GiB
- [ ] H4: User groups (render, video, docker)
- [ ] H5: `pip install -e ".[dev]"` + `pytest -m backend_any` (306 functions)
- [ ] H6: `agmind doctor` → all green
- [ ] H7: llama.cpp wheel build (`CMAKE_ARGS=-DGGML_VULKAN=ON pip install --no-binary llama-cpp-python`)
- [ ] H8: `agmind models download --tier M` (smaller для bandwidth)
- [ ] H9: `docker pull` для всех 32 services + build base/cpu/vulkan/rocm
- [ ] H10: Ansible `--check` dry-run + real apply on localhost

**DoD:**
- `agmind doctor` 9/9 ✓
- `pytest` green (или explicit skips для hardware-only markers)
- `agmind chat` returns coherent reply
- `agmind deploy status` все services UP

#### Phase I — Git + release prep (1 day)

- [ ] I1: `rm -rf .git/` (user manual, classifier-blocked)
- [ ] I2: `git init -b main` + config user.email/name
- [ ] I3: `.gitignore` validate + первый `git add .`
- [ ] I4: Initial commit "Initial: AGmind x86 v0.1.0-dev"
- [ ] I5: ADR-0001/0002 → "accepted"
- [ ] I6: ADR-0003 (memory budgeting Strix Halo)
- [ ] I7: ADR-0004 (engine selection inside backend)
- [ ] I8: AGMIND_MIGRATION_SPEC.md changelog (D1-D4 entries)
- [ ] I9: migration_progress.json final (phase G complete)
- [ ] I10: `git tag v0.1.0-dev`

**DoD:**
- Fresh git repo, clean baseline commit
- All ADRs accepted
- Spec changelog matches actual changes

#### Phase J — Day-2 ops CLI (3-5 days)

- [ ] J1: `agmind install --profile X` (Python wrapper Ansible)
- [ ] J2: `agmind upgrade --check/--apply/--rollback`
- [ ] J3: `agmind backup create [--name X]`
- [ ] J4: `agmind backup verify --dry-run`
- [ ] J5: `agmind backup restore --name X`
- [ ] J6: `agmind config validate` (env-placeholders, version drift)
- [ ] J7: `agmind config diff` (planned vs current)
- [ ] J8: `agmind creds rotate` (rotate-secrets.sh equivalent)
- [ ] J9: `agmind state {get,set,migrate}` (state store API)
- [ ] J10: Shell completion (zsh/bash)

**DoD:**
- All CLI-09..14 → ✅
- All BACKUP-02..04 → ✅

#### Phase K — Observability + production polish (3-5 days)

- [ ] K1: 10 Grafana dashboards JSON (LLM perf, GPU metrics, RAG pipeline, alerts, audit)
- [ ] K2: Prometheus alert rules (high memory, slow inference, peer down)
- [ ] K3: Alertmanager Telegram/webhook routing
- [ ] K4: agmind /metrics endpoint (Python prom_client exporter)
- [ ] K5: GPU metrics exporter (rocm-smi + sysfs → Prometheus textfile)
- [ ] K6: HTTP retry/backoff in LlamaServerClient
- [ ] K7: Async support (asyncio.run для batch operations)
- [ ] K8: Models SHA256 verify + progress bar
- [ ] K9: CONTRIBUTING.md + CHANGELOG.md
- [ ] K10: ansible-vault для production secrets

**DoD:**
- All OBS-06..09 → ✅
- All SEC-11 → ✅

### v0.3.0 — Integration + benchmarks (~2 weeks after v0.2.0)

Phases L-M. Focus: **real-world tests, full benchmark suite, multi-node**.

- Phase L: Real multi-node cluster setup (2+ Strix Halo nodes)
- Phase M: Full benchmark suite (pytest-benchmark + k6 load tests)
- Address all TEST-12..14 and PERF-02..05

### v0.4.0 — RAG depth (~3 weeks)

- Dify integration validated (workflows, plugin marketplace)
- RAGFlow opt-in fully tested
- Document parsing pipelines (MinerU sidecar)
- VLM picture description через Qwen2.5-VL

### v1.0.0 — GA stable

- All REQ-IDs ✅ или explicit "won't fix"
- Real production deployments documented
- Sphinx API reference auto-gen
- Asciinema demos / video walkthrough
- Public PyPI release `pip install agmind`

## M2 / M3 deferred items (после v0.2.0)

### M2 (v0.2.x — v0.3.x)
- vLLM-ROCm engine integration (для tool calling, structured outputs, specdec)
- Infinity engine для production embed/rerank
- ansible-vault encrypted secrets
- Trivy / vulnerability scanning images
- Multi-GPU support (device_id selection tested)
- LLM-08 tool/function calling validated
- LLM-09 JSON mode / structured outputs

### M3 (v1.0.x+)
- Sharded multi-host inference (llama.cpp --rpc)
- mTLS между cluster nodes (cert management)
- ROCm production stability (когда ROCm 7.3+ stable)
- Dynamic worker add/remove (без Ansible re-run)
- KV cache replication для failover
- OpenTelemetry tracing
- Web admin UI поверх agmind CLI
- MCP server для AI agent integration

## Out of scope (never)

- NVIDIA / CUDA support (intentionally removed)
- arm64 / aarch64 support (intentionally removed; legacy/ DGX Spark path
  удалён в cleanup)
- Multi-tenant RBAC (single-tenant per AGMIND_MIGRATION_SPEC.md §1.4 LAN)
- Air-gapped install bundle (есть в legacy AGmind, для x86 пока не нужно)
- Windows support
