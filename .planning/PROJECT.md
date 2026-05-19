# AGmind x86 — Project Charter

**What:** Private LLM/RAG platform для AMD Strix Halo (Ryzen AI Max+ 395
+ Radeon 8060S, gfx1151) и generic x86_64. Replacement legacy AGmind
(Bash installer DGX Spark / aarch64 / NVIDIA CUDA) после full rewrite
в Python с Ansible orchestration.

**Who:**
- **Primary users:** Strix Halo owners (single-node) + small clusters
  (1 master + N workers)
- **Secondary:** generic x86 deployments (CPU fallback, no GPU)
- **Deploy profile:** LAN only, single-tenant (нет multi-tenant в M1)

**Hardware reference:** Beelink/Framework/GMKtec mini-PC с Strix Halo
APU (16C Zen 5 + 40 CU RDNA 3.5 iGPU + 128 GB unified LPDDR5X).

**Compute layer:** Vulkan RADV primary, ROCm/HIP secondary (для
long-context pp + GDN models + batch embed), CPU fallback.

---

## Current milestone: **v0.1.0-dev** (Migration complete, alpha)

**Status:** alpha — функциональность shipped, но real hardware validation
не выполнена. Use at own risk.

**Driver:** AGMIND_MIGRATION_SPEC.md (single source of truth для
архитектурных решений).

**Shipped:**
- 3-layer architecture: Ansible (orchestration) + Python `agmind/` (runtime
  + CLI + cluster) + declarative catalogs (services.yaml, models.yaml)
- 32 services pinned semver (27 с digest), 5 LLM tiers с GGUF inventory
- 4 routing strategies (round-robin, least-loaded, sticky-session, random)
- HTTP REST client для llama-server (OpenAI-compatible) + streaming
- LLMHandle ABC + 4 backends (cpu, vulkan, rocm, npu_stub) + 3 engines
  (llama_cpp_{cpu,vulkan,hip}) + llama_server_handle (HTTP)
- 11 Ansible roles (preflight, bootstrap, strix_halo, docker,
  agmind_python, models, services, observability, security, smoke_test,
  cluster)
- 306 test functions, 0 audit findings, 10 ADR-able decisions
- 4 user docs (QUICKSTART/INSTALL/TROUBLESHOOTING/CLUSTER) + 3 ADR + 12 recon reports

## Previous milestones

(нет — это первый проект milestone после migration)

## Reference documents

- `AGMIND_MIGRATION_SPEC.md` — source of truth архитектуры
- `docs/MIGRATION_PLAN.md` — phase A-G план миграции
- `docs/HARDWARE.md` — host setup (BIOS/kernel/sysctl/Mesa/AMDVLK purge)
- `docs/BENCHMARKS.md` — reference perf numbers
- `docs/QUICKSTART.md` + `docs/INSTALL.md` + `docs/CLUSTER.md` — user guides
- `docs/TROUBLESHOOTING.md` — cookbook
- `docs/adr/` — 3 ADRs (template, migration, compute abstraction)
- `.planning/research/x86-migration/` — 12 deep recon reports

## Out of scope (для M1)

- Multi-tenant RBAC
- Sharded multi-host inference (llama.cpp --rpc)
- mTLS между master/workers (LAN trust)
- ROCm production stability (Vulkan primary)
- NVIDIA / CUDA support (intentionally removed)
- arm64 support (intentionally removed)
- Air-gapped install bundle
- Web admin UI поверх agmind CLI

## Evolution rules

- **main-branch only** (когда git init сделается) — без feature branches
- **никогда `:latest`** — all image tags pinned + digest где можно
  (32 service registry, audit-enforced)
- **AMDVLK purge mandatory** — он officially discontinued + 2 GiB cap
  ломает LLM ≥30B
- **llama.cpp build ≥ b8765** — GDN Vulkan shader landed; older builds
  fallback to CPU для Qwen3.5/3.6 A3B
- **research over hacks** — recon reports перед нетривиальной фичей
  (R0/karpathy/R1-R12 как примеры)
- **3-layer separation** — Ansible не делает inference, Python не делает
  host bootstrap, services.yaml не impl logic
