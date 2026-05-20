# R15 — Phase H benchmark protocol для real Strix Halo

- **Date:** 2026-05-20
- **Status:** pre-execution recon (Phase H still pending)
- **Driver:** user reminder "не забывай ресерчить"; verify before pulling
  multi-GiB images / models на реальное железо

## TL;DR

Phase H = real-hardware smoke + numeric baseline.

- Текущий pin `server-vulkan-b9049` в `templates/services/llama-llm.yaml` **жив**
  (`HTTP 200` на GHCR manifest API). Latest known existing — `b9085`. Tag
  `server-vulkan-b9246` который вернул WebFetch на github page UI **не
  существует в реестре** (`HTTP 404`). UI обманывает — verify через registry.
- Community baseline на Llama-2-7B Q4_0 / Vulkan RADV: **pp512 ≈ 882 t/s,
  tg128 ≈ 52 t/s**. Это reference для нашего bench.
- Vulkan RADV выигрывает у ROCm HIP по default; HIP **может** обогнать
  только с `ROCBLAS_USE_HIPBLASLT=1` + rocWMMA + Flash Attention (986/50).
- ROCm для gfx1151 требует `HSA_OVERRIDE_GFX_VERSION=11.5.1` если ROCm
  build не знает родного `11.5.0` (наш ROCm 7.2.3 знает — проверить).
- Phase H execution требует физического `docker pull` (~6 GiB) и pull
  GGUF модели (~3.83 GiB). Не делать без подтверждения user'а.

## Verified facts (registry probes 2026-05-20)

GHCR Docker Registry v2 API probe:

| Tag                          | HTTP | Note                                  |
|------------------------------|------|---------------------------------------|
| `server-vulkan-b9049`        | 200  | **current pin in compose template**   |
| `server-vulkan-b9070`        | 200  |                                       |
| `server-vulkan-b9085`        | 200  | digest `sha256:c6e408e0e687…`         |
| `server-vulkan-b9100..b9246` | 404  | UI showed b9246 — registry не имеет   |
| `server-rocm-b9049`          | 200  | ROCm variant того же build            |
| `server-rocm-b9085`          | 200  |                                       |

**Implication:** GitHub Packages UI на странице `pkgs/container/llama.cpp`
показывает теги **за пределами реального registry state** — возможно
прокси cache или async indexing. Always verify через
`https://ghcr.io/v2/<owner>/<image>/manifests/<tag>`.

## Verified model (HuggingFace TheBloke/Llama-2-7B-GGUF)

Public, no gate. Файлы:

| Quant   | Filename                   | Size    | Notes                  |
|---------|----------------------------|---------|------------------------|
| Q4_0    | llama-2-7b.Q4_0.gguf       | 3.83 GB | community baseline    |
| Q4_K_S  | llama-2-7b.Q4_K_S.gguf     | 3.86 GB | smaller, more loss     |
| Q4_K_M  | llama-2-7b.Q4_K_M.gguf     | 4.08 GB | TheBloke recommended   |

Recommendation для baseline reproduction: **Q4_0** — это quant который
использован в community benchmark (llm-tracker.info), числа comparable.

## Community baseline (Strix Halo gfx1151)

Source: <https://llm-tracker.info/_TOORG/Strix-Halo>

### Llama-2-7B Q4_0 (короткий контекст)

| Backend                          | pp512 t/s         | tg128 t/s         |
|----------------------------------|-------------------|-------------------|
| Vulkan RADV                      | 881.71 ± 1.71     | 52.22 ± 0.05      |
| Vulkan + Flash Attention         | 884.20 ± 6.23     | 52.73 ± 0.07      |
| ROCm HIP (default)               | 348.96 ± 0.31     | 48.72 ± 0.01      |
| HIP + FA                         | 331.96 ± 0.41     | 45.78 ± 0.02      |
| HIP + WMMA + FA                  | 343.91 ± 0.60     | 50.88 ± 0.01      |
| **HIP + WMMA + FA + hipBLASLt**  | **986.12 ± 1.44** | 50.58 ± 0.01      |

Key insight: HIP **обгоняет** Vulkan по prefill **только** с правильными
flags. Naïve `docker run ... server-rocm` выдаст 1/3 от Vulkan baseline.

### Qwen3 235B Q3_K_XL (длинный контекст 8192)

| Backend                  | pp8192    | tg8192    |
|--------------------------|-----------|-----------|
| Vulkan + FA              | 490.18    | 32.03     |
| **HIP + WMMA + FA**      | 368.77    | **50.97** |
| ROCm HIP (batch=256)     | 65.34     | 10.55     |

Insight: на large MoE с long context HIP+WMMA выигрывает **по генерации**
(50.97 vs 32.03 t/s). Для production workload с длинными ctx это важно.

## Required environment / flags

### Vulkan (default наш стек)

Уже в `templates/services/llama-llm.yaml`:
```yaml
env:
  AMD_VULKAN_ICD: RADV
  VK_DRIVER_FILES: /usr/share/vulkan/icd.d/radeon_icd.x86_64.json
  GGML_VK_VISIBLE_DEVICES: '0'
```

Можно добавить для +FA test:
```yaml
# Flash Attention flag — server CLI arg, не env
# command: ["--flash-attn", ...]
```

### ROCm (если решим тестить server-rocm)

```yaml
env:
  HSA_OVERRIDE_GFX_VERSION: '11.5.1'   # safety net, gfx1151 native в ROCm 7.2.3
  ROCBLAS_USE_HIPBLASLT: '1'            # +3x prefill для HIP path
  HIP_VISIBLE_DEVICES: '0'
  LLAMA_HIP_UMA: 'ON'                   # уже в Dockerfile.rocm build flag
```

Plus build-time `-DGGML_HIP_ROCWMMA_FATTN=ON` чтобы rocWMMA Flash
Attention был доступен.

## Bench protocol (когда user даст go)

### Шаг 1 — Pull artifacts (~10 GiB)

```bash
# Image (~6 GiB)
docker pull ghcr.io/ggml-org/llama.cpp:server-vulkan-b9049

# Model (3.83 GiB) — Python download для resume / verify
mkdir -p /var/lib/agmind/models
huggingface-cli download TheBloke/Llama-2-7B-GGUF \
  llama-2-7b.Q4_0.gguf \
  --local-dir /var/lib/agmind/models \
  --local-dir-use-symlinks False
```

### Шаг 2 — Smoke (compose up + /health)

```bash
cd /opt/agmind
agmind deploy --apply --profile core --domain lab.example.com  # требует prior agmind setup
docker compose ps llama-llm                                    # должен быть healthy
curl -s http://127.0.0.1:8080/health | jq .
curl -s -X POST http://127.0.0.1:8080/completion \
  -H 'Content-Type: application/json' \
  -d '{"prompt":"The capital of France is","n_predict":16}' | jq .
```

### Шаг 3 — Benchmark (llama-bench внутри container)

```bash
docker compose exec llama-llm llama-bench \
  -m /models/llama-2-7b.Q4_0.gguf \
  -p 512 -n 128 -r 5 \
  --output md > /tmp/bench-vulkan.md
```

Ожидаем pp512 ≈ 800–900, tg128 ≈ 50–55. Если значительно ниже — проверить:

- `vkcube` / `vulkaninfo --summary` — RADV видит GPU?
- `journalctl -u docker` — IOMMU / passthrough warnings?
- `GGML_VK_DEVICE` env — правильный device выбран?
- Vulkan ICD priority — нет AMDVLK леака?

### Шаг 4 — ROCm comparison (optional)

Same flow но image = `server-rocm-b9049`, env vars выше. Получить tps,
сравнить с community.

### Шаг 5 — Documentation

`docs/BENCHMARKS.md`:

| Run | Build | Backend | Model | pp512 | tg128 | Notes |
|-----|-------|---------|-------|-------|-------|-------|
| 1 | b9049 | Vulkan RADV | Llama-2-7B Q4_0 | ?  | ?  | baseline |
| 2 | b9049 | Vulkan + FA | … | ? | ? | flash attention |
| 3 | b9049 | ROCm HIP + WMMA + FA + hipBLASLt | … | ? | ? | best HIP |

Plus delta vs community baseline в %.

## Что блокирует Phase H sage execution

| Item | Status |
|------|--------|
| Pin in compose | ✓ verified `b9049` exists |
| Model availability | ✓ verified TheBloke/Llama-2-7B-GGUF Q4_0 |
| `agmind setup` config | требует CF token + domain — user has? |
| `agmind deploy --apply` ready | ✓ Phase L.B implemented |
| GPU permissions | ✓ user в render+video, setfacl сделан |
| RADV driver active | ✓ doctor: 7 ok / 2 warn / 0 fail после vulkan detect fix |
| Free disk for image+model | TBD: ~10 GiB нужно free на root и `/var/lib/agmind/` |

## Decision

Stop здесь. Phase H execution = explicit user action: запустить
`agmind deploy --apply` + pull model + run bench. Это physical step
который **не должен** выполняться автоматически из dev session — это
загрузит сеть на ~10 GiB и заполнит /var/lib/agmind/.

Когда user даст go — выполнить шаги 1–5 выше последовательно, записать
real tps в `docs/BENCHMARKS.md` + commit.

## Sources

- [llm-tracker.info / Strix Halo](https://llm-tracker.info/_TOORG/Strix-Halo) —
  community benchmark numbers (Llama-2-7B Q4_0, Qwen3 235B)
- [kyuz0/amd-strix-halo-toolboxes](https://kyuz0.github.io/amd-strix-halo-toolboxes/) —
  benchmark grid (JS-loaded, не fetched полностью)
- [hogeheer499-commits/strix-halo-guide](https://github.com/hogeheer499-commits/strix-halo-guide) —
  +25% gain от latest llama.cpp build (MoE only)
- [ggml-org/llama.cpp issue #21284](https://github.com/ggml-org/llama.cpp/issues/21284) —
  inefficient defaults для gfx1151 ROCm prefill (drives ROCBLAS_USE_HIPBLASLT recommendation)
- [GHCR Docker Registry v2 API](https://docs.docker.com/registry/spec/api/) —
  tag manifest probe pattern (verified b9049 ✓, b9246 ✗)
- [TheBloke/Llama-2-7B-GGUF on HF](https://huggingface.co/TheBloke/Llama-2-7B-GGUF) —
  public Q4_0 GGUF (3.83 GB)
