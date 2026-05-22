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

## Current milestone: **v0.6.0 candidate** (post-M5 hardening)

**Status:** post-M5 handoff. M1-M5 shipped; current work is release
hardening, dirty cloud-artifact reconciliation, and real E2E/cluster
confidence before GA.

**Driver:** AGMIND_MIGRATION_SPEC.md (single source of truth для
архитектурных решений).

**Shipped:**
- 3-layer architecture: Ansible (orchestration) + Python `agmind/` (runtime
  + CLI + cluster) + declarative catalogs (services.yaml, models.yaml)
- 33 services pinned semver/digest where possible, curated LLM/embed/rerank
  GGUF catalog
- 4 routing strategies (round-robin, least-loaded, sticky-session, random)
- HTTP REST client для llama-server (OpenAI-compatible) + streaming
- LLMHandle ABC + 4 backends (cpu, vulkan, rocm, npu_stub) + 3 engines
  (llama_cpp_{cpu,vulkan,hip}) + llama_server_handle (HTTP)
- 11 Ansible roles (preflight, bootstrap, strix_halo, docker,
  agmind_python, models, services, observability, security, smoke_test,
  cluster)
- `agmind install`, `deploy`, `gc`, `migrate`, `logs`, `shell`, `backup`,
  `restore`, `models`, `upgrade`, `cluster`
- Textual multi-step wizard with EN/RU i18n, LLM/embed/rerank model split,
  per-service settings, cluster peer banner, replicate toggle, help overlay
- Real Strix Halo benchmark evidence for Qwen3.6-35B-A3B Q4_K_M
- 886 passing tests, 0 audit findings as of 2026-05-22
- User docs, ADRs 0000-0012, and recon reports R0-R18

## Previous milestones

- **M1 v0.1.0-dev:** migration alpha
- **M2 v0.2.0:** production hardening
- **M3 v0.3.0:** UX + ops polish
- **M4 wave:** cluster mDNS + TUI/UX bundle
- **M5 v0.5.0:** LLM/embed/rerank split + TUI polish round 2

## Reference documents

- `AGMIND_MIGRATION_SPEC.md` — source of truth архитектуры
- `docs/MIGRATION_PLAN.md` — phase A-G план миграции
- `docs/HARDWARE.md` — host setup (BIOS/kernel/sysctl/Mesa/AMDVLK purge)
- `docs/BENCHMARKS.md` — reference perf numbers
- `docs/QUICKSTART.md` + `docs/INSTALL.md` + `docs/CLUSTER.md` — user guides
- `docs/TROUBLESHOOTING.md` — cookbook
- `docs/adr/` — ADRs 0000-0012+
- `.planning/research/x86-migration/` — recon reports R0-R18 + baselines

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
- **GSD memory lives in `.planning/`** — update STATE/ROADMAP/BACKLOG/session
  notes at handoff points so the next agent can continue from facts, not vibes.
