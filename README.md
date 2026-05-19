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

```bash
# Install (после установки git):
git clone <repo-url> agmind && cd agmind
pip install -e ".[dev]"

# Preflight check:
python -m agmind doctor

# Backend selection probe:
python -m agmind status

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
