---
recon: R10 — Strix Halo BIOS UMA frame buffer + Linux memory management
date: 2026-05-19
status: completed
source_agent: general-purpose (WebSearch + WebFetch, ~40 sources)
related: AGMIND_MIGRATION_SPEC.md, MIGRATION_PLAN.md §5 (Risks)
---

# R10: Strix Halo BIOS UMA + unified memory management

## TL;DR — критические открытия для миграции

1. **На Linux: BIOS UMA=512 MB (минимум), GTT через `ttm.pages_limit`** —
   counter-intuitive, но **all sources agree**. Большой BIOS UMA только
   отнимает CPU-usable RAM, не даёт GPU быстрее.
2. **Memory pool в коде = `/sys/class/drm/cardN/device/mem_info_gtt_total`**,
   НЕ `mem_info_vram_total`. На правильно настроенной Strix Halo ~120 GiB
   — эквивалент GB10 121 GiB.
3. **Vulkan быстрее ROCm/HIP на pp512** на gfx1151 в 2026. Default backend
   на Strix Halo должен быть Vulkan, не ROCm.
4. **Kernel ≥ 6.18.4 mainline / 6.17.0-19 HWE** обязательно — старые
   видят только ~15.5 GiB VRAM (ROCm/issues/5444).
5. **Suspend with large GTT ломается** — unload модель до suspend.
6. **amd-smi не работает на gfx1151** (ROCm/issues/6035) — использовать
   rocm-smi + sysfs.

## Ключевые цифры

| Метрика | Значение |
|---------|----------|
| Theoretical bandwidth | 256 GB/s (LPDDR5X-8000, 256-bit) |
| Measured (rocm_bandwidth_test) | ~212 GB/s |
| Real-world inference | ~215 GB/s effective |
| CPU↔GPU memcpy | ~84 GB/s |
| GB10 для сравнения | 273 GB/s theoretical |
| tg parity vs GB10 | ~5 t/s на 70B Q4 — паритет |
| pp vs GB10 | **2-3× медленнее** Strix Halo (matrix engine gap) |

## Sysfs paths для детекции

```python
# agmind/compute/detect.py — Strix Halo memory detection

GIB = 1024**3
STRIX_HALO_PCI_IDS = {0x1586, 0x150e}  # gfx1151 variants

def find_amd_apu_card() -> Path | None:
    """Return /sys/class/drm/cardN/ for the Strix Halo iGPU."""
    for card in sorted(Path("/sys/class/drm").glob("card[0-9]*")):
        vendor = _read_int(str(card / "device/vendor"))
        if vendor != 0x1002:
            continue
        dev_id = _read_int(str(card / "device/device"))
        if dev_id in STRIX_HALO_PCI_IDS:
            return card
    return None

def detect_strix_halo_memory() -> dict:
    card = find_amd_apu_card()
    if not card:
        return {"detected": False}

    return {
        "detected": True,
        "card_path": str(card),
        "bios_uma_gib": _read_int(str(card / "device/mem_info_vram_total")) / GIB,
        "gtt_total_gib": _read_int(str(card / "device/mem_info_gtt_total")) / GIB,
        "gtt_used_gib": _read_int(str(card / "device/mem_info_gtt_used")) / GIB,
        "ttm_pages_limit_gib": _read_int("/sys/module/ttm/parameters/pages_limit") * 4096 / GIB,
        "effective_pool_gib": max(vram_total, gtt_total) / GIB,
        "system_ram_gib": _meminfo_total_gib(),
    }
```

## Рекомендуемая конфигурация хоста

```bash
# /etc/default/grub.d/99-strixhalo-llm.cfg
GRUB_CMDLINE_LINUX_DEFAULT="amd_iommu=off ttm.pages_limit=31457280 ttm.page_pool_size=31457280"

# 31457280 pages * 4KB = 120 GiB на 128 GB системе
# Scale для других объёмов RAM аналогично
```

```bash
# /etc/sysctl.d/99-strixhalo-llm.conf
vm.swappiness=10
vm.overcommit_memory=1
vm.max_map_count=1048576
```

```bash
# Disable zswap, enable small zram
echo N | sudo tee /sys/module/zswap/parameters/enabled

# /etc/systemd/zram-generator.conf.d/zram.conf
[zram0]
zram-size = ram / 8
compression-algorithm = zstd
swap-priority = 100
```

## BIOS settings per vendor

| Vendor | Menu path |
|--------|-----------|
| Framework Desktop | `BIOS > Advanced > AMD CBS > NBIO > GFX Configuration > UMA Frame Buffer Size` |
| GMKtec EVO-X2 | `Advanced > GFX Configuration > iGPU Configuration > UMA Mode = UMA_SPECIFIED` (BIOS 1.04+) |
| HP ZBook Ultra G1a | `Advanced BIOS > Graphics Options > UMA Frame Buffer Size` (default 32 GB, max 112 GB) |
| Beelink GTR9 Pro | AMI BIOS GTRP110 (2025-12-18), стандартное AMD CBS меню |

**Все vendors:** для Linux ставить **минимум** (обычно 512 MB).

## Tier-based model selection для agmind/profiles/estimate.py

| Tier | Model | Q4 size | Min RAM | Min effective_pool_gib |
|------|-------|---------|---------|--------------------------|
| S | 7B | ~4 GB | 16 GB | 8 GB |
| M | 13-14B | ~8 GB | 16 GB | 16 GB |
| L | 30-32B | ~18-20 GB | 32 GB | 32 GB |
| XL | 70B | ~42 GB | 64 GB | 64 GB |
| XXL | 120B MoE | ~70 GB | 128 GB | 110 GB |

## llama.cpp benchmark snapshot (kyuz0/hardware-corner)

| Model | Backend | pp512 (t/s) | tg128 (t/s) |
|-------|---------|-------------|--------------|
| Llama-2-7B Q4_0 | **Vulkan** | **884** | 52.7 |
| Llama-2-7B Q4_0 | HIP+WMMA+FA | 369 | 51.0 |
| Qwen3-30B-A3B (MoE) | **Vulkan** | 119 | **74.8** |
| Llama-4-Scout 109B (MoE) | Vulkan | 103 | 20.2 |
| Llama-3.1-70B Q4_K_M | HIP/Vulkan | ~25-35 | ~5 |

**Conclusion:** Vulkan dominates pp + parity на tg. Default backend = Vulkan.

## Known broken / pitfalls

- **`amd-smi`** на gfx1151 — все метрики N/A (ROCm/issues/6035). НЕ
  использовать в production monitoring.
- **`rocm-smi`** integer underflow для VRAM на gfx1151 + ROCm 7.1 +
  kernel 6.14 (ROCm/issues/5750). Использовать sysfs.
- **Kernel ≤ 6.15** ROCm видит только ~15.5 GiB VRAM (ROCm/issues/5444).
  Fixed in 6.17.0-19 HWE / 6.18.4+ mainline.
- **`amdgpu.gttsize`** deprecated, использовать **`ttm.pages_limit`**.
- **На Strix Halo** модуль **`ttm`**, не `amdttm`. `amdttm.pages_limit=`
  не работает.
- **Suspend with model loaded → GPU hang** на resume. Unload до suspend.
- **`amdgpu.cwsr_enable=0`** cmdline workaround для wave save/restore bugs.

## Сравнение с GB10

| | GB10 (Spark) | Strix Halo |
|---|---|---|
| Theoretical bandwidth | 273 GB/s | 256 GB/s |
| tg 70B Q4 | ~5 t/s | ~5 t/s ✅ паритет |
| pp 70B Q4 | ~75-105 t/s | **~25-35 t/s** 🔴 2.5-3× медленнее |
| Unified RAM | 128 GB | 128 GB |
| Цена | ~$3-4k | ~$1.7-2.3k |
| Matrix engine | Blackwell TC | RDNA 3.5 (CDNA-lite) |

## Sources (топ-20)

ROCm / kernel:
- https://rocm.docs.amd.com/en/latest/how-to/system-optimization/strixhalo.html
- https://docs.kernel.org/gpu/amdgpu/driver-misc.html
- https://github.com/ROCm/ROCm/issues/5444 (VRAM cap kernel <6.17)
- https://github.com/ROCm/ROCm/issues/5595 (GTT misreporting)
- https://github.com/ROCm/ROCm/issues/5750 (rocm-smi underflow gfx1151)
- https://github.com/ROCm/ROCm/issues/6035 (amd-smi N/A gfx1151)

Benchmarks:
- https://kyuz0.github.io/amd-strix-halo-toolboxes/
- https://llm-tracker.info/AMD-Strix-Halo-(Ryzen-AI-Max+-395)-GPU-Performance
- https://www.hardware-corner.net/strix-halo-llm-optimization/

Tuning guides:
- https://blog.linux-ng.de/2025/07/13/getting-information-about-amd-apus/
- https://dev.webonomic.nl/setting-up-unified-memory-for-strix-halo-correctly-on-ubuntu-25-04-or-25-10
- https://www.jeffgeerling.com/blog/2025/increasing-vram-allocation-on-amd-ai-apus-under-linux/
- https://brian.th3rogers.com/posts/strixhalo-cachyos/
- https://www.jdhodges.com/blog/amd-strix-halo-vram-allocation-ryzen-ai-max-395/

Vendor BIOS:
- https://knowledgebase.frame.work/changing-memory-allocation-amd-ryzen-ai-max-300-series-By1LG5Yrll
- https://www.gmktec.com/pages/evo-x2-bios-vram-size-adjustment-guide
- https://aecmag.com/workstations/review-hp-zbook-ultra-g1a-amd-ryzen-ai-max-pro/
- https://strixhalo.wiki/Hardware/PCs/Beelink_GTR9_Pro

Comparisons GB10:
- https://www.theregister.com/2025/12/25/amd_strix_halo_nvidia_spark/
- https://www.tomshardware.com/pc-components/gpus/nvidia-dgx-spark-review

## Применение к нашей миграции

**Зафиксировать в спеке (PR-A8 или новый ADR-0003 «memory budgeting»):**

1. Default Strix Halo backend = **Vulkan** (не ROCm/HIP).
2. Memory pool source = `mem_info_gtt_total`, не `mem_info_vram_total`.
3. BIOS UMA = 512 MB на Linux, GTT через `ttm.pages_limit`.
4. Min kernel: 6.18.4 mainline / 6.17.0-19 HWE.
5. Diagnostics через sysfs + rocm-smi, **не** amd-smi.
6. `docs/HARDWARE.md` обязательно для пользователей с инструкциями BIOS + kernel cmdline + sysctl.
7. Pre-suspend hook unload models.
8. `lib/estimate.sh` → `agmind/profiles/estimate.py`: пересчитать
   tier-budgets с base `effective_pool_gib` runtime detection.

**Перенести в migration_progress.json deferred:**

- `DEF-006`: kernel version detection + hard warning if < 6.18.4
- `DEF-007`: pre-suspend hook unload models (systemd-suspend.d/)
- `DEF-008`: docs/HARDWARE.md с BIOS + kernel + sysctl инструкциями
