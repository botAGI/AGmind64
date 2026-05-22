# AGmind Architecture (4-layer, post-Phase P snapshot 2026-05-20)

> **Updated 2026-05-20** after Phase L.D + L.E + N + O + P. Added Layer 4
> (CI/CD workflows) и расширил Python layer modules. См. ниже Δ-changes
> блок для diff vs предыдущий snapshot.



```
┌────────────────────────────────────────────────────────────────┐
│ Layer 1 — ORCHESTRATION (Ansible YAML, ~1241 LOC)              │
│                                                                │
│  ansible/install.yml                                           │
│    ├─ preflight        (x86_64 / kernel / disk / GPU check)    │
│    ├─ bootstrap        (apt + Vulkan tooling + groups + sysctl)│
│    ├─ strix_halo       (AMDVLK purge + GRUB ttm + Mesa warn)   │
│    ├─ docker           (docker-ce + daemon.json + GPU access)  │
│    ├─ agmind_python    (venv + pip -e . + /usr/local/bin)      │
│    ├─ models           (tier autodetect + HF GGUF download)    │
│    ├─ services         (render compose.yml + nginx + up)       │
│    ├─ observability    (Prometheus/Grafana/Loki/Alloy provision)│
│    ├─ security         (UFW + fail2ban + opt Authelia)         │
│    ├─ cluster          (render cluster.yaml + worker firewall) │
│    └─ smoke_test       (agmind doctor + compose ps)            │
└────────────────────────────────────────────────────────────────┘
                            │
                            ▼  (writes /etc/agmind/, /opt/agmind/.env,
                            │   /etc/agmind/cluster.yaml, services to systemd)
                            │
┌────────────────────────────────────────────────────────────────┐
│ Layer 3 — DECLARATIVE CATALOGS (YAML, ~750 LOC)                │
│                                                                │
│  templates/services.yaml    (32 services, pinned semver+digest)│
│    profiles: core/rag/ragflow/ui/observability/security        │
│    consumed by: Ansible (jinja2 lookup) + Python (registry.py) │
│                                                                │
│  templates/models.yaml      (5 LLM tiers + embed/rerank/VLM)   │
│    tiers: S(16GB)/M(32GB)/L(64GB)/XL(128GB)/XXL(128GB+)        │
│    consumed by: agmind.models + Ansible models role            │
└────────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌────────────────────────────────────────────────────────────────┐
│ Layer 2 — RUNTIME (Python, ~11.5k LOC, 74 modules)             │
│                                                                │
│  ┌─ agmind.cli ─────── (typer app, 9 modules + 7 TUI)          │
│  │   doctor / status / version / audit                         │
│  │   install (Phase N)                                          │
│  │   migrate {up,down,list} (Phase L.D)                        │
│  │   logs/shell/backup/restore (Phase L.E)                     │
│  │   deploy {render,apply,snapshots,rollback,gc} (Phase L.B/C) │
│  │   models {list,pull,info,tier}                              │
│  │   service {list,scaffold,validate} (Phase H'.E)             │
│  │   chat / embed / render compose                             │
│  │                                                             │
│  │   tui/ (Phase J/N TUI screens):                             │
│  │     setup_wizard (J + N.G — model selector + ctx settings)  │
│  │     install_screen (N — orchestrator UI)                    │
│  │     status_dashboard (J.2 — live deploy view)               │
│  │     deploy_screen + summary_screen + logo                   │
│  └─────────────────────                                        │
│      │                                                         │
│      ▼ delegates to                                            │
│                                                                │
│  ┌─ agmind.compute ───── (backend abstraction, 18 modules)     │
│  │   get_backend() → auto-select per Profile                   │
│  │   ┌─ backends/ ──── (4 implementations)                     │
│  │   │   cpu / vulkan / rocm / npu_stub                        │
│  │   └─ _engines/ ──── (HTTP + 3 in-process)                   │
│  │       llama_server_handle (HTTP REST OpenAI-compat)         │
│  │       llama_cpp_{cpu,vulkan,hip} (in-process Llama())       │
│  │       http_helper (DRY backend HTTP fallback)               │
│  └─────────────────────                                        │
│                                                                │
│  ┌─ agmind.models ────── (tier-based GGUF resolver, 1 file)    │
│  │   detect_tier(ram_gib) → tier                               │
│  │   resolve_llm/embed/reranker/vlm() → ModelSpec              │
│  │   model_path(spec) → local Path                             │
│  └─────────────────────                                        │
│                                                                │
│  ┌─ agmind.services ──── (service catalog, 1 file)             │
│  │   load_registry() → dict[name, Service]                     │
│  │   services_for_profile(profile) → list[Service]             │
│  │   validate_no_latest(registry) → list[violation]            │
│  └─────────────────────                                        │
│                                                                │
│  ┌─ agmind.cluster ───── (multi-node coordination, 2 files)    │
│  │   load_cluster_config() → ClusterConfig                     │
│  │   probe_all(peers) → list[PeerHealth]                       │
│  │   choose_peer(healths, strategy) → Peer                     │
│  └─────────────────────                                        │
│                                                                │
│  ┌─ agmind.diagnostics ─ (preflight, 1 file)                   │
│  │   run_preflight() → DoctorReport (9 checks)                 │
│  │   multi-GPU Vulkan parse (post-3dda542)                     │
│  └─────────────────────                                        │
│                                                                │
│  ┌─ agmind.deploy ───── (Phase L.B/C, 5 files, 1053 LOC)       │
│  │   runner.deploy() — render→snapshot→up→wait_healthy→rollback│
│  │   snapshot.SnapshotManager (retention=10)                   │
│  │   diff.compute_diff + format_diff                           │
│  │   gc.gc_all + gc_{containers,images,volumes,networks,models}│
│  └─────────────────────                                        │
│                                                                │
│  ┌─ agmind.install ──── (Phase N, 4 files, 1021 LOC) NEW       │
│  │   orchestrator.InstallOrchestrator + ProgressEvent          │
│  │   steps: doctor/bootstrap/pull/model/env_write/deploy       │
│  │   models.CURATED_MODELS catalog + CTX/KV/THREADS/PARALLEL   │
│  │     presets                                                 │
│  │   sudo via anonymous pipe → ansible-playbook                │
│  └─────────────────────                                        │
│                                                                │
│  ┌─ agmind.ops ──────── (Phase L.E, 3 files, 355 LOC) NEW      │
│  │   backup.create_backup + restore_backup (tarball + meta)    │
│  │   exec.exec_service (docker compose logs + exec wrapper)    │
│  └─────────────────────                                        │
│                                                                │
│  ┌─ agmind.migrations ─ (Phase L.D, 6 files, 214 LOC) NEW      │
│  │   MigrationRunner: discover + up + down                     │
│  │   SchemaState (~/.local/share/agmind/schema.json)           │
│  │   v001_initial baseline                                     │
│  └─────────────────────                                        │
│                                                                │
│  ┌─ agmind.services ─── (Phase H'.B/C + O, 5 files, 1231 LOC)  │
│  │   registry.load_descriptors + legacy bridge                 │
│  │   renderer.render_compose + inject_capability_env (O.B)     │
│  │   compatibility.check_service_compatibility (soft warnings) │
│  │   capability_bindings.BINDINGS (vector_db / llm / dify_kb)  │
│  └─────────────────────                                        │
│                                                                │
│  ┌─ Utility modules ────                                       │
│  │   log (structlog), _env, secrets, config.env, i18n          │
│  │   observability (OpenTelemetry placeholder)                 │
│  └─────────────────────                                        │
└────────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌────────────────────────────────────────────────────────────────┐
│ Layer 4 — CI/CD (.github/workflows/, NEW for Phase P)           │
│                                                                │
│  ci.yml             — pytest + mypy + audit (per push/PR)      │
│  release-drafter.yml — auto release notes (per merge to main)  │
│  version-check.yml  — Phase P weekly cron → issue с label      │
│                       'upstream-update' (mirror legacy #63)    │
└────────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌────────────────────────────────────────────────────────────────┐
│ Docker Compose stack (32 containers, profile-filtered)         │
│                                                                │
│  Core profile:                                                 │
│    llama-llm (Vulkan b9049) → port 8080                        │
│    llama-embed (Vulkan b9049) → port 8081                      │
│    llama-rerank (Vulkan b9049) → port 8082                     │
│    qdrant (v1.18.0) → port 6333                                │
│    nginx (1.31.0-alpine) → ports 80/443                        │
│                                                                │
│  RAG profile (+ above):                                        │
│    dify-api/web/worker/plugin-daemon/sandbox (1.14.2)          │
│    postgres (17.10-alpine)                                     │
│    redis (8.4.3-alpine)                                        │
│    docling-serve-cpu (v1.18.0)                                 │
│                                                                │
│  Observability (10 services):                                  │
│    prometheus v3.5.3 + grafana 13.0.1 + loki 3.7.2             │
│    alloy v1.16.1 + cadvisor v0.57.0 + portainer 2.41.1         │
│    alertmanager v0.32.1 + node/postgres/redis exporters        │
│                                                                │
│  RAGFlow profile (opt-in, M2):                                 │
│    ragflow + mysql + elasticsearch + minio                     │
└────────────────────────────────────────────────────────────────┘
```

## Δ-changes vs previous snapshot (2026-05-19)

| Module / artefact | Status |
|---|---|
| `agmind/install/` | **NEW** (Phase N) — orchestrator + 6 steps + curated catalog |
| `agmind/ops/` | **NEW** (Phase L.E) — backup tarball + exec wrapper |
| `agmind/migrations/` | **NEW** (Phase L.D) — schema migrations runner |
| `agmind/services/capability_bindings.py` | **NEW** (Phase O) — provider → consumer env table |
| `agmind/services/compatibility.py` | **NEW** (Phase O) — provides/conflicts checker |
| `agmind/cli/tui/install_screen.py` | **NEW** (Phase N) — TUI install progress |
| `agmind/cli/tui/status_dashboard.py` | **NEW** (Phase J.2) — live deploy view |
| `scripts/version_check.py` | **NEW** (Phase P) — upstream version scanner |
| `.github/workflows/version-check.yml` | **NEW** (Phase P) — weekly cron → issue |
| `templates/version_holds.yaml` | **NEW** (Phase P) — HOLD config |
| `agmind/compute/detect.py` | **modified** — multi-GPU Vulkan parser (`3dda542`) |
| `scripts/audit_forbidden.py` | **modified** — unfreeze + regex fixes (`3dda542`, `8a6c621`) |
| `templates/services/*.yaml` | **modified** — annotated с provides/consumes (18 descriptors) |
| `agmind/schemas/service.py` | **modified** — `provides`/`conflicts_with`/`consumes` fields |
| `agmind/cli/tui/setup_wizard.py` | **modified** — Phase J.1.10 compact + Phase N.G model selector + Phase N.H threads/parallel |
| `docs/adr/` | **+4 ADRs** (0009 L.D, 0010 N, 0011 O + amendment, 0012 P) |
| `.planning/research/x86-migration/` | **+3 recons** (R14 backup gaps, R15 Phase H bench, R16 Qwen flags) |

## Layer separation invariants

1. **Ansible не делает inference** — оркестрация только; долгоиграющие
   процессы — в containers либо systemd.
2. **Python (agmind/) не делает host bootstrap** — apt/sysctl/groups
   через Ansible. Python код **может** ставить chmod 600 на credentials и
   render configs, но не управлять apt.
3. **YAML catalogs не impl logic** — services.yaml/models.yaml только
   descriptive. Logic — в Python либо Ansible.
4. **services.yaml читается обоими** через одинаковый schema
   (`schema_version: 1`). Если структура меняется — bump version.
5. **Ansible templates рендерятся из services.yaml**, не дублируют его.
   Compose YAML = jinja2 шаблон вокруг `lookup('file', services.yaml) | from_yaml`.

## Request flow (RAG chat example)

```
User → nginx :80
        ├─ /v1/chat/completions → dify-api :5001
        │   └─ Python (Dify) → ${OPENAI_API_BASE_URL=http://llama-llm:8080/v1}
        │       └─ llama-llm container → /v1/chat/completions
        │           └─ llama-server (Vulkan b9049) → RADV → gfx1151 GPU
        │
        └─ /v1/embeddings → llama-embed :8081
            └─ llama-server (Vulkan b9049, pooling=cls) → RADV
```

Python `agmind.compute.get_backend()` flow (для standalone CLI):

```
agmind chat → agmind.cli.chat_cmd
  → LlamaServerClient("http://localhost:8080")
  → client.chat_stream(messages, sampling)
  → urllib SSE → llama-server → RADV → tokens (delta chunks)
  ← stream → CLI prints char-by-char
```

Backend selection (when not using HTTP):

```
agmind.compute.get_backend()
  → config = read_config()        # AGMIND_* env vars
  → registry = _load_backends()    # lazy import cpu/vulkan/rocm/npu_stub
  → available = list_available_backends()  # filter по .available()
  → chosen = _select_auto(config, available)  # per Profile rules
  → return backends[chosen].make(engine=config.engine)
```

## Service resolution flow

```
ansible-playbook install.yml -t services
  → role: services
    → lookup('file', templates/services.yaml) | from_yaml
    → set_fact: agmind_selected_services = filter(profiles ∩ agmind_profiles)
    → template: docker-compose.yml.j2  → /opt/agmind/docker-compose.yml
    → template: env.j2                  → /opt/agmind/.env (chmod 600)
    → community.docker.docker_compose_v2: state=present
    → ↳ docker compose up -d (pull missing)
```

## Cluster routing flow (M1)

```
nginx (master) → /v1/chat/completions
  → upstream "llama_cluster" {
       server llama-llm:8080;          # local master
       # M2: + worker endpoints from /etc/agmind/cluster.yaml
       # M2: + nginx upstream weight=N (least_conn)
     }

Python agmind.cluster (M1 partial — module ready, nginx integration M2):
  load_cluster_config() → peers
  probe_all(peers, timeout=5) → healths
  choose_peer(healths, strategy=round-robin)
  → return Peer
  → LlamaServerClient(peer.url).chat(...)
```

## Test architecture

- `tests/conftest.py` — pytest fixtures (clean_env, has_vulkan, has_rocm, has_strix_halo, has_llama_cpp)
- `tests/compute/` — backend contract tests (markers `backend_cpu/vulkan/rocm/any`)
- `tests/cluster/` — routing strategy tests с mock peers
- `tests/services/` — registry validation
- `tests/diagnostics/` — doctor checks (real /sys/ reading, gracefully degrade)
- `tests/*` — unit для utility modules
- `tests/test_ansible_layout.py` — YAML syntax + role structure
- `tests/test_audit_script.py` — audit_forbidden.py rules

CI matrix:
- `ubuntu-24.04` amd64: audit + lint + test-cpu
- `[self-hosted, strix-halo]` (workflow_dispatch only): backend_vulkan + backend_rocm

## Extension points

См. `EXTENSION_POINTS.md` (TODO next session).
