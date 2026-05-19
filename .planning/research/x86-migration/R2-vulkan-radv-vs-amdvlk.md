---
recon: R2 — Vulkan RADV vs AMDVLK на gfx1151, май 2026
date: 2026-05-19
status: completed
source_agent: general-purpose (~25 sources)
related: R3-llama-cpp-vulkan-hip.md, AGMIND_MIGRATION_SPEC.md Part 1.2
---

# R2: Vulkan RADV vs AMDVLK на Strix Halo

## TL;DR — спека была права, стала ещё более right

1. **AMDVLK officially discontinued 15 сентября 2025** (Phoronix, GitHub
   Discussion #416). Последний релиз v-2025.Q2.1 (апр 2025). AMD: «full
   support behind RADV».
2. **AMDVLK имеет hard 2 GiB cap на VkDeviceMemory allocation** — любая
   LLM с одним compute buffer >2 GiB (Gemma 3 27B BF16, многие dense
   ≥30B) **физически не загружается**. RADV ограничения не имеет.
3. **RADV +63% PP / +1.2% TG** vs AMDVLK на свежих llama.cpp; на long
   context 130560: RADV 17.24 vs AMDVLK 10.75 t/s.
4. **AMDVLK устанавливает implicit_layer.d/amd_icd64.json** — перехватывает
   Vulkan loader даже когда AMD_VULKAN_ICD=RADV в некоторых конфигах.
5. **AMD_VULKAN_ICD=RADV alone unreliable** (GPUOpen-Drivers issue #222).
   Use `VK_DRIVER_FILES=/usr/share/vulkan/icd.d/radeon_icd.x86_64.json` —
   единственный 100% надёжный способ.

## RADV Mesa version timeline

| Mesa | Date | Strix Halo support |
|------|------|---------------------|
| 24.1 | 2024-06 | initial enablement |
| 25.0 | 2025-02-19 | Vulkan 1.4, RDNA 3.5 stable |
| 25.1 | 2025-05-07 | CoopMat fixes |
| **25.2.8** | (in production) | **stable production minimum** |
| 25.2 | 2025-08-06 | RT improvements RDNA 3/3.5 |
| 26.0 | 2026-02 | Vulkan 1.4 advance, RT overhead reduction |
| 26.0.6 | Q1 2026 | "+9% prompt eval" Strix Halo (strix-halo-guide) |
| 26.1.0 | 2026-05-16 | latest kisak-mesa fresh PPA |

**Минимум абсолютный:** 25.0.x (default Ubuntu 24.04 HWE).
**Минимум рекомендуемый:** 25.2.8.
**Sweet spot:** 26.0+ (через `ppa:kisak/kisak-mesa`).

## RADV known issues (gfx1151)

- llama.cpp #20515: `DeviceLostError` при ubatch >2048 в зоне 65k–80k
  токенов → `--ubatch-size 256-512`
- llama.cpp #18725: Qwen3-Next PP halves при ubatch >512
- llama.cpp #18741: "unexpectedly reached end of file" → `--no-direct-io`
- llama.cpp #14854: slow model loading >64GB при 32→40 layers boundary

## AMDVLK критические проблемы

| Issue | Impact |
|-------|--------|
| **`maxMemoryAllocationSize=0x80000000`** (2 GiB cap) | LLM >2 GiB compute buffer не загружается |
| Officially discontinued 2025-09-15 | Никаких новых релизов |
| ICD installation conflict с RADV | Перехватывает loader через implicit layer |
| `--usecase=vulkan` в amdgpu-install ставит AMDVLK | Опасный default при ROCm install |

## Vulkan extensions для llama.cpp (Required)

1. `VK_KHR_cooperative_matrix` — **CRITICAL** (без него PP в 2× медленнее, scalar fallback)
2. `VK_KHR_shader_float16_int8` — FP16 шейдеры
3. `VK_KHR_shader_integer_dot_product` — quantized matmul ускорение
4. `VK_KHR_buffer_device_address` (core 1.2) — pointer arithmetic
5. `VK_EXT_external_memory_host` — UMA zero-copy
6. `VK_KHR_maintenance4` / `VK_KHR_synchronization2` (core 1.3)

Все 6 присутствуют на RADV gfx1151 в Mesa ≥ 25.0.

## Health-check (Python pseudocode для agmind/compute/backends/vulkan.py)

```python
REQUIRED_EXTENSIONS = (
    "VK_KHR_cooperative_matrix",
    "VK_KHR_shader_float16_int8",
    "VK_KHR_shader_integer_dot_product",
    "VK_EXT_external_memory_host",
    "VK_KHR_buffer_device_address",
)
MIN_MESA = (25, 2, 8)
RECOMMENDED_MESA = (26, 0, 0)

AMDVLK_FILES = (
    "/etc/vulkan/icd.d/amd_icd64.json",
    "/etc/vulkan/icd.d/amd_icd32.json",
    "/etc/vulkan/implicit_layer.d/amd_icd64.json",
    "/etc/vulkan/implicit_layer.d/amd_icd32.json",
)

def assert_no_amdvlk():
    leftovers = [f for f in AMDVLK_FILES if Path(f).exists()]
    if leftovers:
        raise RuntimeError(
            f"AMDVLK detected: {leftovers}. Run: "
            "sudo apt remove --purge amdvlk && sudo rm -f " + " ".join(AMDVLK_FILES)
        )

def force_radv_env() -> dict:
    return {
        "AMD_VULKAN_ICD": "RADV",
        "VK_DRIVER_FILES": "/usr/share/vulkan/icd.d/radeon_icd.x86_64.json",
        "VK_ICD_FILENAMES": "/usr/share/vulkan/icd.d/radeon_icd.x86_64.json",
        "GGML_VK_VISIBLE_DEVICES": "0",
    }

def healthcheck() -> VulkanInfo:
    assert_no_amdvlk()
    info = probe_vulkan_summary()
    assert info.driver_id == "DRIVER_ID_MESA_RADV"
    assert info.device_type == "INTEGRATED_GPU"
    assert "GFX1151" in info.device_name or "STRIX_HALO" in info.device_name
    assert info.mesa_version >= MIN_MESA
    assert all(ext in info.extensions for ext in REQUIRED_EXTENSIONS)
    return info
```

## Docker recipe (Vulkan-only, без ROCm)

```bash
docker run --rm \
  --device /dev/dri \
  --group-add video \
  --group-add render \
  --security-opt seccomp=unconfined \
  -e AMD_VULKAN_ICD=RADV \
  -e VK_DRIVER_FILES=/usr/share/vulkan/icd.d/radeon_icd.x86_64.json \
  agmind/vulkan:latest
```

`/dev/kfd` НЕ нужен для Vulkan-only (только для ROCm).

## llama-server recommended flags (Strix Halo)

```
--flash-attn 1           # Wave32 FA после PR #19625
--no-mmap                # critical, иначе hangs / OOM
--ubatch-size 256        # safe для DeviceLostError на long ctx
--batch-size 512
-ngl 99                  # offload everything
-np 8                    # continuous batching, 2.7× throughput vs np=1
```

## Multi-process предупреждение

llama.cpp Vulkan **НЕ перечитывает веса в горячий пул** при VRAM
eviction (issue #5380). При двух llama-server на одной gfx1151 второй
вытесняет первого — speedup падает с 10 t/s → 1 t/s.

**Правильное решение:** один `llama-server -np 8` (continuous batching),
не N процессов.

## Update в AGMIND_MIGRATION_SPEC.md (proposed)

Part 1.2 (Vulkan section):
- Mesa minimum **25.2.8** (не указано в текущей версии).
- Kernel minimum **6.18.4** mainline / **6.17.0-19** HWE.
- AMDVLK: добавить prohibition + cleanup-инструкция.
- Kernel: упомянуть `amd_iommu=off`, `amdgpu.gttsize=131072`, `ttm.pages_limit=31457280`.

Part 5.7 (Dockerfile.vulkan):
- Добавить explicit RM AMDVLK files на образ-этапе.
- Mesa 26+ через kisak PPA.
- llama.cpp pin ≥ post-March-2026 commit (PR #19625, #20551).
- Healthcheck Python пробник.

## Sources

- Phoronix RADV vs AMDVLK Strix Halo Radeon 8060S
- Phoronix AMDVLK Discontinued 2025-09-15
- GPUOpen-Drivers/AMDVLK Discussion #416, Issue #222
- llama.cpp #15054 (AMDVLK 2 GiB cap), #5380 (multi-process VRAM eviction),
  #20515, #18725, #18741, #14854
- llama.cpp Discussion #10879 (Vulkan + RADV_PERFTEST), #16138 (Docker)
- Mesa docs (RADV, env vars), Mesa 25.0/25.1/25.2/26.0 release notes
- kisak-mesa fresh PPA (Mesa 26.1.0 as of 2026-05-16)
- ROCm docs Strix Halo system optimization
- Vulkan-Loader LoaderDriverInterface.md
- kyuz0/amd-strix-halo-toolboxes, strixhalo.wiki/AI
- hogeheer499-commits/strix-halo-guide
- Ollama #15601 (vendored 56% gap)
