# AGmind benchmarks — Strix Halo (gfx1151, RDNA 3.5)

> **Status:** Phase H run in progress. Reference table заполнена из community
> recon (R3/R4/R15/R16); local Strix Halo numbers — section "Phase H —
> Qwen3.6-35B-A3B run" ниже (пополняется по мере завершения bench'ей).
>
> Контекст: AMD Ryzen AI Max+ 395 (Strix Halo) — Zen 5 16C/32T + Radeon
> 8060S (gfx1151, RDNA 3.5, 40 CU), 128 GB unified LPDDR5X.

## Методология

Все измерения — через `llama-bench` (часть llama.cpp) на одинаковых
моделях / промптах. **Pre-conditions** (verify через `agmind doctor`):
- Kernel ≥ 6.18.4 mainline / 6.17.0-19 HWE
- Mesa ≥ 26.0.0 (kisak PPA)
- ROCm 7.2 (для HIP runs)
- BIOS UMA = 512 MB (минимум); `ttm.pages_limit ≥ 94% RAM`
- AMDVLK absent
- linux-firmware ≥ 20260110

## Baseline (для reference из R3/R4 recons, не верифицировано локально)

| Model | Quant | Backend | pp512 (t/s) | tg128 (t/s) | Source |
|-------|-------|---------|------------:|-------------:|--------|
| Llama 2 7B | Q4_0 | Vulkan RADV | 881 | 52.8 | llama.cpp#13565 |
| Llama 2 7B | Q4_0 | HIP +FA | 366 | 49.0 | knightli scoreboard |
| Qwen3-Coder 30B | UD-Q4_K_XL | Vulkan RADV (b9049) | 1321 | 96.8 | hogeheer499 |
| Qwen3 30B-A3B | UD-Q4_K_XL | Vulkan RADV | 755 | 85.1 | strixhalo.wiki |
| Qwen3 30B-A3B | UD-Q4_K_XL | HIP rocWMMA | 651 | 64.2 | strixhalo.wiki |
| Gemma 4 26B | Q4_K_XL (b8765) | Vulkan | n/a | 52.3 | ollama#15601 |
| GPT-OSS 120B | MXFP4 (MoE) | Vulkan RADV | 339 | 49.0 | blog.yifei.sg |
| Llama 3.1 70B | Q4_K_M | Vulkan/HIP | n/a | 5-12 | varies |
| MiniMax M2.5 228B | Q3_K_M | Vulkan RADV | n/a | 32.8 | visorcraft |

## DoD targets для Phase D

Smoke benchmark (verify on local hardware):

| Model | Backend | Min decode t/s | Min prefill t/s |
|-------|---------|----------------|------------------|
| Llama 2 7B Q4_0 | Vulkan | ≥45 | ≥800 |
| Llama 2 7B Q4_0 | HIP | ≥45 | ≥350 |
| Qwen3 30B-A3B Q4_K_XL | Vulkan | ≥75 | ≥700 |
| BGE-M3 Q8_0 (embed) | Vulkan | n/a | ≥100 embed/sec @ 1K tokens |
| bge-reranker-v2-m3 Q8_0 | Vulkan | p99 ≤ 200 ms | n/a |

## Comparison with NVIDIA GB10 (для contextual baseline)

| Workload | GB10 (DGX Spark) | Strix Halo | Delta |
|----------|------------------|------------|-------|
| Memory bandwidth | 273 GB/s | 256 GB/s theoretical / ~215 measured | -6% / -21% |
| tg 70B Q4 | ~5 t/s | ~5-12 t/s | паритет |
| pp 70B Q4 | ~75-105 t/s | ~25-35 t/s | **-2.5-3× медленнее** |
| Tensor cores | Blackwell FP4 | RDNA 3.5 (no FP8) | CDNA-style ops missing |

## Methodology details

### Vulkan run

```bash
docker run --rm \
  --device=/dev/dri --group-add video --group-add render \
  --security-opt seccomp=unconfined \
  -v /var/lib/agmind/models:/models:ro \
  agmind-vulkan:dev \
  llama-bench -m /models/llama-2-7b.Q4_0.gguf \
    --backend vulkan -ngl 99 \
    --flash-attn 1 --no-mmap \
    --ubatch-size 256 -p 512 -n 128
```

### HIP run

```bash
docker run --rm \
  --device=/dev/kfd --device=/dev/dri \
  --group-add video --group-add render \
  --security-opt seccomp=unconfined --cap-add=SYS_PTRACE \
  --ipc=host --shm-size=16G \
  -v /var/lib/agmind/models:/models:ro \
  agmind-rocm:dev \
  llama-bench -m /models/llama-2-7b.Q4_0.gguf \
    --backend hip -ngl 99 \
    --flash-attn 1 --no-mmap \
    --ubatch-size 512 -p 512 -n 128
```

### CPU fallback

```bash
docker run --rm \
  -v /var/lib/agmind/models:/models:ro \
  agmind-cpu:dev \
  llama-bench -m /models/llama-2-7b.Q4_0.gguf \
    --backend cpu \
    --threads 16 -p 512 -n 128
```

## Phase H — Qwen3.6-35B-A3B run (this repo, 2026-05-20)

Driver: user перешёл с DGX Spark на Strix Halo, нужно прямое сравнение
архитектур на той же модели которую он гонял на Spark. Modus:
**llama.cpp Vulkan b9049 + Q4_K_M GGUF**.

### DGX Spark baseline (user's prior data, source [habr 1033342](https://habr.com/ru/articles/1033342/))

| Engine                | Quant       | tg single (t/s) | Notes                       |
|-----------------------|-------------|-----------------|-----------------------------|
| vLLM cu130-nightly    | FP8 native  | 51.0–52.5       | Default config              |
| vLLM cu130-nightly    | NVFP4 4-bit | 40.9            | NVIDIA's experimental quant |
| vLLM fork (AEON-7 DFlash) | FP8     | 69.7 avg / 107 peak | community fork, DFlash kernels |

Aggregate at 32 concurrent requests: 498.6 tok/s (AEON-7 DFlash).

### Strix Halo community baseline (source [0xSero/HF](https://huggingface.co/0xSero/Qwen3.6-35B-A3B-GGUF-Strix))

Tested на Framework Desktop (AMD Ryzen AI MAX+ 395 / Radeon 8060S / 128 GB
unified) via llama.cpp Vulkan:

| Quant       | Size    | pp512 (t/s) | tg128 (t/s) | Note                |
|-------------|---------|-------------|-------------|---------------------|
| Q4_K_M      | 21.2 GB | 1021        | **70.2**    | production sweet    |
| Q4_0        | 19.7 GB | n/a         | **76.5**    | fastest decode      |
| DYNAMIC mix | 19 GB   | **1100**    | 64.0        | fastest prefill     |

### Strix Halo (this repo) — measured

Hardware: Framework Desktop, AMD Ryzen AI Max+ 395 (Strix Halo, gfx1151,
Radeon 8060S RADV), 125 GiB unified LPDDR5X, Ubuntu 26.04, kernel 6.17.0-29,
Mesa 25.2.8 (RADV), `agmind doctor` 7 ok / 2 warn / 0 fail.

Build: `ghcr.io/ggml-org/llama.cpp:full-vulkan-b9049` (build 2496f9c14).
GPU init: `Found 1 Vulkan devices: Radeon 8060S Graphics (RADV STRIX_HALO)
(radv) | uma: 1 | fp16: 1 | bf16: 0 | warp size: 64 | shared memory: 65536
| int dot: 1 | matrix cores: KHR_coopmat`.

| Run | Quant   | Build | Backend | Flags                                                     | pp512 (t/s)         | tg128 (t/s)       | Date       |
|-----|---------|-------|---------|-----------------------------------------------------------|---------------------|-------------------|------------|
| H.1 | Q4_K_M  | b9049 | Vulkan  | `-fa 1 -ctk q8_0 -ctv q8_0 -ub 2048 -b 2048 -mmp 0 -ngl 999` | **1023.57 ± 16.97** | **73.47 ± 0.14** | 2026-05-20 |

Comparison row vs baselines:

| Source           | Engine + quant         | pp512 | tg128 | Δ vs us (tg) |
|------------------|------------------------|-------|-------|--------------|
| **Us (H.1)**     | llama.cpp Vulkan Q4_K_M | 1024  | **73.5** | —             |
| 0xSero community | llama.cpp Vulkan Q4_K_M | 1021  | 70.2 | **+4.6 %**    |
| DGX Spark (habr) | vLLM cu130 FP8 native   | n/a   | 51-52.5 | **+41 %**     |
| DGX Spark (DFlash fork) | vLLM-fork FP8 + DFlash | n/a   | 69.7 avg | **+5.5 %**    |

**Phase H DoD: passed.** Migration с GB10/Spark на Strix Halo/Vulkan
сохраняет (и превышает) baseline performance на той же модели.

Reproduce — standalone (no agmind deploy required):

```bash
docker run --rm \
  -v ~/.local/share/agmind/models:/models:ro \
  --device /dev/dri \
  --group-add video --group-add render \
  --security-opt seccomp=unconfined \
  -e AMD_VULKAN_ICD=RADV \
  -e GGML_VK_VISIBLE_DEVICES=0 \
  --entrypoint /app/llama-bench \
  ghcr.io/ggml-org/llama.cpp:full-vulkan-b9049 \
  -m /models/Qwen3.6-35B-A3B-Q4_K_M.gguf \
  -p 512 -n 128 -r 5 \
  -fa 1 -ctk q8_0 -ctv q8_0 -ub 2048 -b 2048 \
  --no-mmap -ngl 999
```

Note: используется `full-vulkan-b9049` (не `server-vulkan-b9049`) — у
`server-vulkan` образа только `llama-server`, без `llama-bench`. Pre-prod
deploy через AGmind compose template продолжает использовать
`server-vulkan-b9049` (template `templates/services/llama-llm.yaml`).

### Architecture-comparison takeaways

- DGX Spark (FP8 native vLLM) vs Strix Halo (Q4_K_M llama.cpp Vulkan):
  не apples-to-apples из-за разного engine + quantization, но end-user
  tps сопоставимы.
- Strix Halo Q4_K_M tg128 ≈ 70 t/s **обходит** DGX Spark FP8 (51–52 t/s)
  на ~35%, и **обгоняет** даже AEON-7 DFlash (69.7 avg) на бумаге.
- Quality difference Q4_K_M vs FP8 на 35B MoE — ~2–3% MMLU per community
  evals; для interactive chat workload разница неощутима.
- Phase H DoD: numerical proof что переезд с GB10 на Strix Halo не теряет
  performance для main inference workload (chat / MoE). При confirmed tg ≥
  community baseline ±5% — migration validated.

## Local run results (legacy section, Llama-2-7B baseline)

_Заполняется при первом полном D/G прогоне на оборудовании с
установленными vulkaninfo + rocminfo. См. `agmind doctor` для статуса
host setup._

### Hardware snapshot (текущий dev host, 2026-05-19)

```
CPU: AMD RYZEN AI MAX+ 395 w/ Radeon 8060S (Zen 5, 32C/32T)
RAM: 124.9 GiB LPDDR5X unified
Kernel: 6.17.0-29-generic
GPU: AMD Radeon 8060S (Strix Halo, gfx1151, PCI 1002:1586)
  BIOS UMA: 0.50 GiB (optimal)
  GTT pool: 62.5 GiB  ⚠ sub-optimal — recommended 117 GiB
Vulkan: NOT installed (vulkan-tools missing)
ROCm: NOT installed (rocminfo missing)
```

**Action items (для пользователя через `agmind doctor` hints):**
1. `sudo apt install vulkan-tools mesa-vulkan-drivers libvulkan1`
2. ROCm 7.2 install (см. `docs/HARDWARE.md`)
3. Add to GRUB cmdline: `ttm.pages_limit=30788044` + reboot
4. `sudo usermod -aG video,render beelinknode && newgrp render`
5. Rebuild llama-cpp-python с `CMAKE_ARGS='-DGGML_VULKAN=ON ...'`
   (см. `docker/Dockerfile.vulkan`)

После этих шагов запустить:
```bash
make docker-vulkan
docker run --rm --device=/dev/dri --group-add video --group-add render \
  agmind-vulkan:dev python -m agmind status --json
```
