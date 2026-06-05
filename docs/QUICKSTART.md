# AGmind Quickstart

> Production deployment for AMD Strix Halo (gfx1151) — **5 минут до
> рабочего LLM endpoint**, при условии что hardware preflight passes
> (`agmind doctor`).

## Pre-requisites

1. AMD Strix Halo (Ryzen AI Max+ 395 + Radeon 8060S, 64+ GB RAM)
2. Ubuntu 24.04, kernel **≥ 6.17.0-19 HWE** или **≥ 6.18.4** mainline
3. `sudo` access
4. ~150 GB free disk (для XL tier модель)

Если железо другое (x86_64 без AMD GPU) — agmind работает в CPU fallback,
но quality / speed degraded. См. [HARDWARE.md](HARDWARE.md).

## TL;DR — single-node install

Clean machine, one command. `make setup` creates the local `.venv`, installs the agmind CLI
into it, and launches the TUI install wizard — there is no global `agmind` until the install
writes one (the templates/ansible the installer needs live in this checkout, so the repo IS the
bootstrap entry point):

```bash
git clone https://github.com/botAGI/AGmind64 && cd AGmind64
make setup
```

Optional non-destructive proof first (if Docker Compose 2.24+ is already installed), and the
non-interactive form:

```bash
make bootstrap                                  # just create .venv (no run)
.venv/bin/agmind verify install --domain lab.example.com
make install ARGS="--no-tui --domain lab.example.com --model-id qwen36-a3b-q4km"
```

The TUI path runs the wizard, privileged bootstrap, runtime `.env` write,
`docker compose config --quiet` for the exact selected stack, image pulls,
model pulls, deploy, health checks, rollback-aware failure handling, and a final
hint to `/opt/agmind/.env` without printing secret values.
If Docker is preinstalled manually, use the official Docker Engine packages
(`docker-ce`, `docker-ce-cli`, `containerd.io`, `docker-buildx-plugin`,
`docker-compose-plugin`) rather than the distro `docker.io` package.

Legacy/manual Ansible path:

```bash
# 1. Clone repo
git clone https://github.com/botAGI/AGmind64 && cd AGmind64

# 2. Install Ansible deps
sudo apt update && sudo apt install -y ansible python3-pip
ansible-galaxy collection install -r ansible/requirements.yml

# 3. Preflight (это даст конкретные fix-команды для warnings)
pip install -e ".[dev]"
python -m agmind doctor

# 4. Apply fixes из doctor warnings (BIOS, kernel cmdline, groups, etc).
# См. docs/HARDWARE.md § Recipes.

# 5. Bootstrap (apt + Docker + Strix Halo tuning + agmind venv)
ansible-playbook ansible/install.yml -t bootstrap

# 6. Download models (auto-tier по RAM — pulls the LLM + bge-m3 embed + bge-reranker)
ansible-playbook ansible/install.yml -t models
# Or pull a curated model by hand: `agmind models list` then `agmind models pull <id>`.

# 7. Bring up stack
ansible-playbook ansible/install.yml -t services

# 8. Verify
agmind doctor
agmind deploy status
```

После шага 7:
- **Dify console:** http://localhost:3000 (или `agmind-dify.local`)
- **LLM API (OpenAI compat):** http://localhost:8080/v1
- **Embed API:** http://localhost:8081/v1/embeddings
- **Rerank API:** http://localhost:8082/v1/rerank
- **Open WebUI** (опционально): `agmind-chat.local`

## Interactive chat

Chat goes through the OpenAI-compatible LLM endpoint (or the Open WebUI frontend if the
`ui` profile is deployed) — there is no `agmind chat` CLI:

```bash
curl http://localhost:8080/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"local","messages":[{"role":"user","content":"hello world"}]}'
```

## Embed / rerank

These are OpenAI-compatible HTTP endpoints (no `agmind embed`/`agmind rerank` CLI):

```bash
# Embed text(s)
curl http://localhost:8081/v1/embeddings \
  -H 'Content-Type: application/json' \
  -d '{"model":"bge-m3","input":["hello world","second text"]}'

# Rerank documents by query (sorted desc by relevance)
curl http://localhost:8082/v1/rerank \
  -H 'Content-Type: application/json' \
  -d '{"model":"bge-reranker","query":"Strix Halo benchmarks","documents":["doc-1...","doc-2..."]}'
```

## Profiles (компоненты стека)

`agmind_profiles` в `ansible/inventory/hosts.yml` (или `-e agmind_profiles='[core,rag]'`):

| Profile | Что включается |
|---------|----------------|
| `core` | traefik, llama-llm, llama-embed, llama-rerank, qdrant (минимум для inference) |
| `rag` | + Dify (api/worker/web/plugin-daemon/sandbox), postgres, redis, docling |
| `ragflow` | RAGFlow + mysql + elasticsearch + minio (M2 fallback, opt-in) |
| `ui` | Open WebUI (chat frontend) |
| `observability` | Prometheus + Grafana + Loki + Alloy + cAdvisor + Portainer + exporters |
| `security` | Authelia (2FA SSO) + UFW + fail2ban (host-level) |

Default: `core` + `rag`.

## Tier auto-detection

The `models` play (`ansible-playbook ansible/install.yml -t models`, and the `agmind install`
wizard) uses `agmind.models.detect_tier()`:

| RAM | Tier | LLM (primary) | Disk |
|-----|------|---------------|------|
| 16 GB | S | Qwen3.5-9B UD-Q4_K_XL | 6.0 GB |
| 32 GB | M | gemma-4-26B-A4B-it UD-Q4_K_M | 16.9 GB |
| 64 GB | L | Qwen3.6-35B-A3B UD-Q4_K_XL | 22.4 GB |
| 128 GB | XL | gpt-oss-120b MXFP4_MOE | 62.8 GB |
| 128+ GB | XXL | MiniMax M2.5 Q3_K_M | 101.8 GB |

Override the auto-detected tier with `AGMIND_MODELS_TIER` (or `-e agmind_models_tier=M`):
```bash
AGMIND_MODELS_TIER=M ansible-playbook ansible/install.yml -t models
```

## Multi-node cluster

См. [docs/CLUSTER.md](CLUSTER.md). TL;DR:

```bash
# 1. Edit ansible/inventory/cluster.yml — add worker IPs
# 2. Ensure SSH key-based access master → workers
# 3. Run cluster install
ansible-playbook -i ansible/inventory/cluster.yml ansible/install.yml
```

## Troubleshooting

См. [TROUBLESHOOTING.md](TROUBLESHOOTING.md).

Quick wins:
- `agmind doctor` показывает все warnings с конкретными fix-командами
- `agmind deploy logs <service>` — tail контейнерных логов
- `agmind status --json` — текущий backend selection с capabilities

## Backup / restore

```bash
agmind backup --ask-sudo-password --output ~/agmind-backup.tar.gz
agmind restore --ask-sudo-password ~/agmind-backup.tar.gz
```

This backs up AGmind config/state and deploy snapshots, not GGUF models or
Docker volume data. Keep service data under `/var/lib/agmind` protected by
separate storage snapshots.

## Что дальше

- [INSTALL.md](INSTALL.md) — детальный setup (BIOS, kernel cmdline, sysctl)
- [HARDWARE.md](HARDWARE.md) — Strix Halo specifics + recipes
- [BENCHMARKS.md](BENCHMARKS.md) — perf numbers (Vulkan RADV / HIP / CPU)
- [adr/](adr/) — Architecture Decision Records
