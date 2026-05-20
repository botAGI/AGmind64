# AGmind

> **Private LLM/RAG platform для AMD Strix Halo и generic x86_64.**
> Migration target от NVIDIA GB10/aarch64 — см. [AGMIND_MIGRATION_SPEC.md](AGMIND_MIGRATION_SPEC.md).

![Status](https://img.shields.io/badge/status-alpha-orange)
![Platform](https://img.shields.io/badge/platform-x86_64-blue)
![Backend](https://img.shields.io/badge/backend-Vulkan%20%7C%20ROCm%20%7C%20CPU-green)
![License](https://img.shields.io/badge/license-Apache_2.0-blue)

## Reference hardware

- **AMD Ryzen AI Max+ 395 "Strix Halo"** — Zen 5 (16C/32T) + Radeon 8060S
  (gfx1151, RDNA 3.5, 40 CU) + 128 GB unified LPDDR5X
- Secondary: любой x86_64 Linux с / без AMD GPU (CPU fallback гарантирован)

## Quick start

### One-command install (Phase N, recommended)

```bash
# Bootstrap (один раз — клонит репо и ставит Python deps):
git clone <repo-url> agmind && cd agmind
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

### Selecting a model

Phase N.G даёт curated catalog + custom HF input в wizard:

```bash
# List of verified models (★ = measured on this hardware):
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

# Use curated id (skip wizard):
agmind install --no-tui --domain lab.example.com --cf-token-file token.txt \
  --model-id qwen36-a3b-q4km --ctx-size 16384 --kv-cache q8_0

# Use custom HuggingFace repo / file:
agmind install --no-tui --domain lab.example.com --cf-token-file token.txt \
  --model-repo user/CustomGGUF --model-file model.Q5_K_M.gguf \
  --ctx-size 32768 --kv-cache q4_0
```

В TUI wizard'е "Model" section имеет:
- **Select** — curated catalog с пометкой ★ для tested + "Custom HuggingFace…"
- **HF repo / filename** input'ы (заполняются если выбран Custom)
- **Context size** — 4K / 8K / 16K / 32K / 64K / 128K presets
- **KV cache** — q8_0 (recommended) / q4_0 (aggressive) / f16 (default llama.cpp)

### Day-2 ops

```bash
# Preflight check:
agmind doctor

# Backend / device info:
agmind status

# Live deployment dashboard (Phase J.2):
agmind status --tui

# Logs / shell / backup / restore (Phase L.E):
agmind logs llama-llm -f
agmind shell traefik --cmd "/bin/sh"
agmind backup --output ~/agmind-backup.tar.gz
agmind restore ~/agmind-backup.tar.gz

# State schema migrations (Phase L.D):
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

Auto-selection через `agmind.compute.get_backend()` per
[selection rules](AGMIND_MIGRATION_SPEC.md#126-selection-rules--decision-matrix).

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
├── cli/               # typer app: doctor / status / version / audit
├── diagnostics/       # preflight + health
├── i18n/              # en + ru
├── config/            # .env render + write
├── secrets.py         # credentials.txt с chmod 600
└── log.py             # structured logging

docker/                # Dockerfile.{base,cpu,vulkan,rocm} + digest pins
scripts/audit_forbidden.py  # CI/pre-commit gate
docs/                  # MIGRATION_PLAN.md, HARDWARE.md, BENCHMARKS.md, adr/
legacy/gb10/           # Old DGX Spark Bash installer (rollback up to 2027-Q1)
```

## Documentation

- [`AGMIND_MIGRATION_SPEC.md`](AGMIND_MIGRATION_SPEC.md) — рабочая спека миграции (single source of truth)
- [`docs/MIGRATION_PLAN.md`](docs/MIGRATION_PLAN.md) — план A→G фаз + OQ для апрува
- [`docs/HARDWARE.md`](docs/HARDWARE.md) — Strix Halo host setup (BIOS, kernel, sysctl)
- [`docs/BENCHMARKS.md`](docs/BENCHMARKS.md) — baseline numbers + run instructions
- [`docs/adr/`](docs/adr/) — Architecture Decision Records (0001 migration, 0002 compute, 0003+ TBD)
- [`.planning/research/x86-migration/`](.planning/research/x86-migration/) — recon-отчёты R0-R11

## Migration status (как Phase G open M1)

- ✅ Phase A: Inventory & Plan — done
- ✅ Phase B: Legacy quarantine — *virtual через EXCLUDED_DIRS audit-script*; physical `git mv` после установки git binary
- ✅ Phase C: `agmind/compute/` skeleton + CPU backend + contract tests
- ✅ Phase D: Vulkan + ROCm backends (engine: llama_cpp; vLLM/Infinity = M2)
- ✅ Phase E: CLI + diagnostics + secrets + config + i18n
- ✅ Phase F: 4 Dockerfile + CI workflow + pre-commit
- ⏳ Phase G: README + BENCHMARKS skeleton; real-hardware bench pending после
  установки vulkaninfo/rocminfo на dev машине

## Rollback (старый DGX Spark installer)

Старый AGmind (Bash installer для DGX Spark / GB10) deprecated, остаётся
до 2027-Q1 в `legacy/gb10/` (после физической перестановки через `git mv`).
До этого виртуально quarantined через `EXCLUDED_DIRS` в `scripts/audit_forbidden.py`.

См. [`legacy/gb10/README.md`](legacy/gb10/README.md.draft) (draft до Phase B
физической миграции).

## License

Apache-2.0. См. [LICENSE](LICENSE).
