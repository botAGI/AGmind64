# AGmind benchmarks — Strix Halo (gfx1151, RDNA 3.5)

> **Status:** skeleton. Будет заполнен после Phase D полного тестирования
> на реальном железе (текущая dev-машина имеет gfx1151 но без установленных
> vulkaninfo/rocminfo — см. `agmind doctor`).
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
  agmind-vulkan:latest \
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
  agmind-rocm:latest \
  llama-bench -m /models/llama-2-7b.Q4_0.gguf \
    --backend hip -ngl 99 \
    --flash-attn 1 --no-mmap \
    --ubatch-size 512 -p 512 -n 128
```

### CPU fallback

```bash
docker run --rm \
  -v /var/lib/agmind/models:/models:ro \
  agmind-cpu:latest \
  llama-bench -m /models/llama-2-7b.Q4_0.gguf \
    --backend cpu \
    --threads 16 -p 512 -n 128
```

## Local run results

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
  agmind-vulkan:latest python -m agmind status --json
```
