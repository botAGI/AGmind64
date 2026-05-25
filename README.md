# AGmind

> **Private LLM/RAG platform для AMD Strix Halo и generic x86_64.**
> Active project memory lives in [`.planning/`](.planning/).

![Status](https://img.shields.io/badge/status-alpha-orange)
![Platform](https://img.shields.io/badge/platform-x86_64-blue)
![Backend](https://img.shields.io/badge/backend-Vulkan%20%7C%20ROCm%20%7C%20CPU-green)
![License](https://img.shields.io/badge/license-Apache_2.0-blue)

## Reference hardware

- **AMD Ryzen AI Max+ 395 "Strix Halo"** — Zen 5 (16C/32T) + Radeon 8060S
  (gfx1151, RDNA 3.5, 40 CU) + 128 GB unified LPDDR5X
- Secondary: любой x86_64 Linux с / без AMD GPU (CPU fallback гарантирован)

## Quick start

### One-command install

```bash
# Bootstrap (один раз — клонит репо и ставит Python deps):
git clone https://github.com/botAGI/AGmind64 agmind && cd agmind
uv venv && uv pip install -e ".[dev]"  # либо `python -m venv .venv && pip install -e ".[dev]"`

# End-to-end install — один prompt sudo, дальше всё в TUI:
agmind install
```

`agmind install` запускает unified flow:

```
[sudo password]  →  TUI wizard (domain / CF token / services / model / context)
                 →  InstallProgressScreen с live log:
                       [✓] Preflight diagnostics          12s · 7 ok / 2 warn
                       [✓] System bootstrap (sudo/apt)    1m 48s · ansible OK
                       [✓] Docker image pull              2m 14s · all cached
                       [✓] Model download (Qwen3.6 Q4_K_M) 18m · 21 GB
                       [✓] Write runtime .env             <1s
                       [✓] Deploy compose + healthcheck   45s · 11/11 healthy
                 →  Summary с URLs (https://llama.yourdomain.com etc.)
```

### Selecting models

The setup wizard has separate selectors for LLM, embedding, and reranker GGUF
models, plus custom Hugging Face repo/file inputs for each role.

```bash
# List curated model catalog (★ = measured on this hardware):
agmind install --list-models
#
# ID                     NAME                                    SIZE QUANT    CTX
# ------------------------------------------------------------------------------------------
# ★ qwen36-a3b-q4km      Qwen3.6-35B-A3B (MoE)                 21.2GB Q4_K_M   16384
# ★ qwen36-a3b-q4_0      Qwen3.6-35B-A3B (MoE)                 19.7GB Q4_0     16384
# ★ qwen36-a3b-dyn       Qwen3.6-35B-A3B (MoE, DYNAMIC mix)    19.0GB DYNAMIC  16384
#   llama2-7b-q4_0       Llama-2-7B                             3.8GB Q4_0     4096
#   llama2-7b-q4km       Llama-2-7B                             4.1GB Q4_K_M   4096
#   bge-m3-q8            BGE-M3 (multilingual embed)            0.6GB Q8_0     8192
#   bge-reranker-v2-m3-q8 BGE Reranker v2 M3                    0.6GB Q8_0     8192

# Use curated LLM id (skip wizard):
agmind install --no-tui --domain lab.example.com --cf-token-file token.txt \
  --model-id qwen36-a3b-q4km --ctx-size 16384 --kv-cache q8_0

# Use custom HuggingFace repo / file:
agmind install --no-tui --domain lab.example.com --cf-token-file token.txt \
  --model-repo user/CustomGGUF --model-file model.Q5_K_M.gguf \
  --ctx-size 32768 --kv-cache q4_0
```

В TUI wizard'е "Model" section имеет:
- **LLM** — curated chat/reasoning model + context/KV/threads/parallel
- **Embed** — curated BGE-M3 default + independent context/KV/parallel
- **Rerank** — curated BGE Reranker v2 M3 default or custom model

### Fresh deploy readiness

Перед чистой установкой на Strix Halo хосте:

```bash
# Host/GPU preflight. Exit code 1 means warnings; exit code 2 means failures.
agmind doctor --json

# Detect the local deployment target and visible AGmind LAN peers:
agmind cluster inspect --timeout 5

# Validate the default Compose lane before touching /opt/agmind:
agmind render topology --profile core,rag,observability --json
agmind deploy --profile core,rag,observability \
  --install-dir /tmp/agmind-fresh-deploy-check \
  --domain lab.example.com \
  --no-prompt
```

For the actual install:

```bash
agmind install --no-tui \
  --domain lab.example.com \
  --cf-token-file token.txt \
  --model-id qwen36-a3b-q4km \
  --ctx-size 16384 \
  --kv-cache q8_0
```

`agmind install` writes runtime secrets/model env first; a raw
`agmind render compose | docker compose config` check without that env can show
blank-variable warnings for passwords and model filenames.

### Two-node cluster detection

AGmind discovers peers through mDNS service `_agmind._tcp.local.`. The second
device must advertise itself; being on the same LAN is not enough.

On the second Strix Halo device:

```bash
cd ~/agmind
uv venv && uv pip install -e ".[dev]"
agmind cluster advertise --duration 600
```

On the first device:

```bash
agmind cluster detect --timeout 10
agmind cluster status --timeout 10
agmind cluster inspect --timeout 10
```

If peers stay empty, check both nodes are on the same VLAN/subnet, `avahi-daemon`
is running, UDP 5353/mDNS is allowed by the firewall, and both environments have
the `zeroconf` Python dependency installed.

### Day-2 ops

```bash
# Preflight check:
agmind doctor

# Backend / device info:
agmind status

# Deployment target + LAN peer inspection:
agmind cluster inspect --timeout 5

# Live deployment dashboard:
agmind status --tui

# Logs / shell / backup / restore:
agmind logs llama-llm -f
agmind shell traefik --cmd "/bin/sh"
agmind backup --output ~/agmind-backup.tar.gz
agmind restore ~/agmind-backup.tar.gz

# State schema migrations:
agmind migrate status
agmind migrate up

# Audit (запрет CUDA/aarch64/etc в основном дереве):
make audit
```

## Compute backends

| Backend | Engine | Reference perf (gfx1151, Q4 30B) | Status |
|---------|--------|----------------------------------|--------|
| **Vulkan RADV** | llama_cpp | tg ~97 t/s, pp ~1321 t/s | M1 primary |
| **ROCm/HIP 7.2** | llama_cpp | tg ~64 t/s, pp ~986 t/s | M1 secondary (long-ctx pp) |
| **CPU (Zen 5)** | llama_cpp | tg ~3-5 t/s, embed 120-200 docs/sec | M1 fallback |
| ROCm + vLLM-patched | vllm | tool-calling / specdec | M2 |
| ROCm + Infinity | infinity | embed batch ≥16 | M2 |
| XDNA 2 NPU | — | not supported on Linux | stub |

Auto-selection проходит через `agmind.compute.get_backend()` and the backend
registry in `agmind.compute._registry`.

## Architecture

```
agmind/                # Python package
├── compute/           # Runtime backend abstraction (ABC + engines)
│   ├── base.py        # Backend, DeviceInfo, LLMHandle
│   ├── detect.py      # vulkaninfo / rocminfo / sysfs
│   ├── config.py      # AGMIND_* env vars
│   ├── _registry.py   # auto-select + factory
│   └── backends/
│       ├── cpu.py
│       ├── vulkan.py
│       ├── rocm.py
│       ├── npu_stub.py
│       └── _engines/  # llama_cpp_{vulkan,hip,cpu}.py, vllm/infinity (M2)
├── cli/               # typer app: install / deploy / render / cluster / doctor
├── services/          # service descriptor loading, topology, compose/k8s render
├── deploy/            # dry-run/apply/rollback/snapshots/target contracts
├── cluster/           # peer discovery, inventory, target inspection
├── components/        # component ownership and version governance
├── diagnostics/       # preflight + health
├── models.py          # YAML-backed curated model catalog
├── i18n/              # en + ru
├── config/            # .env render + write
├── secrets.py         # credentials.txt с chmod 600
└── log.py             # structured logging

docker/                # Dockerfile.{base,cpu,vulkan,rocm} + digest pins
scripts/audit_forbidden.py  # CI/pre-commit gate
docs/                  # BENCHMARKS.md, TROUBLESHOOTING.md, adr/
.planning/             # durable GSD project memory and current roadmap
```

## Documentation

- [`.planning/STATE.md`](.planning/STATE.md) — current milestone/state memory
- [`.planning/BACKLOG.md`](.planning/BACKLOG.md) — active and historical backlog
- [`.planning/codebase/`](.planning/codebase/) — compact codebase map for agents
- [`docs/BENCHMARKS.md`](docs/BENCHMARKS.md) — baseline numbers + run instructions
- [`docs/adr/`](docs/adr/) — Architecture Decision Records

## License

Apache-2.0. См. [LICENSE](LICENSE).
