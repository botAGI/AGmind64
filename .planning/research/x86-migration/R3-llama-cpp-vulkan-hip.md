---
recon: R3 — llama.cpp Vulkan + HIP backend на Strix Halo gfx1151
date: 2026-05-19
status: completed
source_agent: general-purpose (WebSearch + WebFetch, ~30 sources)
related: R10-strix-halo-bios-uma.md, AGMIND_MIGRATION_SPEC.md Part 1.2, 5.7, 5.8
---

# R3: llama.cpp Vulkan/HIP на gfx1151

## TL;DR

1. **Vulkan RADV — primary backend** на gfx1151 (~25-30% быстрее tg чем
   HIP на коротком/среднем контексте, паритет на длинном).
2. **HIP secondary** — для GDN-моделей (Qwen3-Next family) где Vulkan
   shader отсутствует (#20354), для batch embeddings, для long-context pp.
3. **Точные требования**: kernel ≥ 6.17.0-19.19~24.04.2 (HWE) / 6.18.4
   (mainline), Mesa ≥ 26.0.2, ROCm 7.2.x, llama-cpp-python ≥ 0.3.23
   (PyPI 2026-05-11), llama.cpp upstream ≥ b8765.
4. **Два венва раздельно** — agmind-vulkan и agmind-rocm. Один wheel с
   обоими бэкендами технически возможен, но в практике mess.
5. **НЕ использовать Ollama для production** — vendored llama.cpp
   отстаёт ~56% от upstream на Vulkan (issue #15601).

## Бенчмарки (Vulkan RADV, gfx1151)

| Модель | Quant | pp512 (t/s) | tg128 (t/s) | Источник |
|--------|-------|-------------|--------------|----------|
| Llama 2 7B | Q4_0 | 881 | 52.8 | issue #13565 |
| Qwen3 30B-A3B (MoE) | UD-Q4_K_XL | 755 | 85.1 | strixhalo.wiki |
| Qwen3-Coder 30B | UD-Q4_K_XL (b9049) | 1321 | 96.8 | hogeheer499 |
| Qwen3.6 35B-A3B (MoE) | UD-Q4_K_XL | 1029 | 60 | slb350 (b9029) |
| Qwen3.6 27B (dense) | UD-Q4_K_XL | 322 | 12.0 | slb350 |
| Gemma 4 26B | Q4_K_XL (b8765) | n/a | 52.3 | ollama #15601 |
| Gemma-4-31B | UD-Q4_K_XL | 261 | 11.1 | slb350 |
| GPT-OSS 120B (MoE) | MXFP4 | 339 | 48.95 | blog.yifei.sg |
| Llama 3.1 70B | Q4_K_M | n/a | 5.1-12 | varies |
| MiniMax M2.5 228B | Q3_K_M | n/a | 32.8 | visorcraft |

## HIP бенчмарки (gfx1151)

| Модель | Quant | pp512 | tg128 |
|--------|-------|-------|-------|
| Llama 2 7B | Q4_0 (no FA) | 351 | 47.97 |
| Llama 2 7B | Q4_0 (+FA) | 366 | 48.97 |
| Qwen3 30B-A3B | Q4_K_XL (rocWMMA) | 651 | 64.2 |
| Qwen3 30B-A3B | Q4_K_XL (hipBLASLt) | 652 | 63.95 |
| Qwen3.5-35B-A3B | Q4_K_M (16 parallel) | n/a | 168 aggregate |

## CMAKE_ARGS финальные

### Vulkan build
```bash
CMAKE_ARGS="-DGGML_VULKAN=ON -DGGML_NATIVE=OFF -DCMAKE_BUILD_TYPE=Release" \
    pip install --no-binary llama-cpp-python llama-cpp-python --verbose
```

Apt deps:
```bash
sudo apt install -y libvulkan1 vulkan-tools mesa-vulkan-drivers \
    glslang-tools spirv-tools libvulkan-dev spirv-headers
```

### HIP build
```bash
HIPCXX="$(hipconfig -l)/clang" \
HIP_PATH="$(hipconfig -R)" \
CMAKE_ARGS="-DGGML_HIP=ON \
            -DAMDGPU_TARGETS=gfx1151 \
            -DGPU_TARGETS=gfx1151 \
            -DGGML_HIP_NO_VMM=ON \
            -DGGML_HIP_ROCWMMA_FATTN=ON \
            -DGGML_HIP_MMQ_MFMA=ON \
            -DGGML_NATIVE=OFF \
            -DCMAKE_BUILD_TYPE=Release" \
    pip install --no-binary llama-cpp-python llama-cpp-python --verbose
```

## Runtime envs (HIP)

```bash
export HSA_OVERRIDE_GFX_VERSION=11.5.1
export HSA_ENABLE_SDMA=0
export ROCBLAS_USE_HIPBLASLT=1  # ~+15% pp
```

## Vulkan: AMD_VULKAN_ICD policy

| Mode | pp | tg | Replicable |
|------|----|----|------------|
| RADV (default) | ✅ best на pp | ✅ паритет | Mesa из коробки |
| AMDVLK | -2% pp | -4% tg | AMDVLK packages; имеет limit ≤2 GiB single allocation — непригоден для больших моделей |

**Recommendation**: жёстко `AMD_VULKAN_ICD=RADV` (как в спеке Part 1.2).
Удалить AMDVLK ICD JSON если установлен.

## Известные подводные камни

### Vulkan
- **GATED_DELTA_NET shader отсутствует** (issue #20354, мар 2026) —
  Qwen3-Next family деградирует до 11.87 t/s. **Workaround:** fallback
  на HIP для GDN-моделей.
- **Mesa shader cache** в `~/.cache/mesa_shader_cache*` и `~/.cache/radv_*`
  — чистить при upgrade Mesa.
- **shaderc v2025.2 ломает build** (issue #15344) — пинить shaderc 2025.1.

### HIP
- **HIP VMM нестабилен** на gfx1151 → `-DGGML_HIP_NO_VMM=ON` обязателен.
- **Models >6 GB hang at runtime** → `-dio` flag в llama-server / `mmap=False`.
- **rocWMMA медленнее на long context** в ROCm 7.0.2+ — на long
  использовать стандартный HIP без rocWMMA.
- **ROCm 7.0.2 падает на gfx1151 + latest llama.cpp** (ROCm/#5534) —
  использовать 7.2.x.
- **Low HIP compute utilization** ~8.9-40% при 70-73% mem bw —
  архитектурно/driver-level, fix не в нашей власти.

## Декision matrix: Vulkan vs HIP

| Сценарий | Backend | Reason |
|----------|---------|--------|
| Single-user chat short ctx | **Vulkan** | tg +25-30% |
| Single-user chat long ctx (≥130K) | **Vulkan** | tg 12.5 vs 5.0 |
| Long context pp-bound | **HIP rocWMMA** | pp 40.6 vs 17.2 |
| Concurrent batch ≥16 | **HIP** | aggregate 168 t/s |
| Embeddings batch ≥4 | **HIP (inferred)** | scale by batch |
| GDN-модели | **HIP** | Vulkan shader отсутствует |
| Stability при долгой нагрузке | **HIP** | Vulkan может hang |

## Системные требования финальные

| Component | Min version |
|-----------|-------------|
| Ubuntu | 24.04 LTS |
| Kernel HWE | 6.17.0-19.19~24.04.2 |
| Kernel mainline | 6.18.4 |
| Mesa/RADV | 26.0.2 (через kisak PPA если HWE даёт ≤) |
| linux-firmware | 20260110 |
| ROCm | 7.2.0 (НЕ 7.0.2 — падает на gfx1151) |
| llama-cpp-python | 0.3.23 (PyPI 2026-05-11) |
| llama.cpp upstream | b8765 (с PR #19625, #20551) |

## Применение к нашей миграции

### Update AGMIND_MIGRATION_SPEC.md

**Заменить в Part 1.2:**

```
2. **ROCm/HIP 7.x** — опциональный, для prompt-heavy и batch (RAG ingest,
   embeddings, fine-tune). На gfx1151 ROCm лидирует по prompt processing
   (~500+ pp t/s). Требует:
```

на актуальные данные:

```
2. **ROCm/HIP 7.2.x** (минимум 7.2.0, не 7.0.x — крашится на gfx1151,
   ROCm/issues/5534) — secondary backend. На gfx1151 HIP лидирует по
   prompt-bound long-context (40.6 pp t/s на depth 130K vs 17.2 RADV),
   GDN-моделям (Qwen3-Next family где RADV shader отсутствует),
   concurrent batch ≥16 (~168 t/s aggregate vs ~85 single). Требует:
   ...
```

**Заменить в Part 1.4:**

`rocm/dev-ubuntu-24.04:7.0-complete` → `rocm/dev-ubuntu-24.04:7.2-complete`

**Заменить в Part 5.7 / 5.8 / 5.10:**

CMAKE_ARGS строки расширить под gfx1151-specific (см. выше).

**Добавить новый раздел** в Part 1.2: «Selection rules» с decision matrix
выше.

### Migration_progress.json deferred items

- `DEF-009`: kernel/Mesa/ROCm/llama-cpp-python минимальные версии в
  agmind/compute/{vulkan,rocm}.py с warning если ниже.
- `DEF-010`: GDN-model detection list в agmind/models.py → fallback на HIP.
- `DEF-011`: Mesa shader cache cleanup hook при upgrade detection.
- `DEF-012`: smoke benchmark Llama 2 7B Q4_0 как DoD для backend C/D —
  Vulkan tg ≥45 t/s, pp ≥800; HIP tg ≥45, pp ≥350.

## Sources

- visorcraft/strix-halo-llm-perf
- hogeheer499-commits/strix-halo-guide
- strixhalo.wiki/AI/llamacpp-performance + AI/llamacpp-with-ROCm
- llama.cpp discussion #20856 (Known-Good Strix Halo Stack)
- llama.cpp issue #13565 (HIP backend poor perf gfx1151)
- llama.cpp issue #20354 (GDN shader missing Vulkan)
- ollama issue #15601 (vendored llama.cpp lag 56%)
- ollama issue #14855 (ROCm Working Guide)
- AMD ROCm docs RDNA3.5 system optimization
- blog.yifei.sg Strix Halo matrix cores
- knightli scoreboard, slb350/strix-benchmarks, kyuz0/amd-strix-halo-toolboxes
- llm-tracker.info
- PyPI llama-cpp-python 0.3.23 (2026-05-11)
