# AGmind Architecture (3-layer)

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
│ Layer 2 — RUNTIME (Python, ~4753 LOC)                          │
│                                                                │
│  ┌─ agmind.cli ─────── (typer app, 5 modules)                  │
│  │   doctor / status / version / audit                         │
│  │   models {list,download,verify,path}                        │
│  │   deploy {up,down,status,ps,logs,restart,pull}              │
│  │   chat / embed / rerank                                     │
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
│  │   doctor_report(as_json) → str                              │
│  └─────────────────────                                        │
│                                                                │
│  ┌─ Utility modules ────                                       │
│  │   log, _env, secrets, config.env, i18n                      │
│  └─────────────────────                                        │
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
