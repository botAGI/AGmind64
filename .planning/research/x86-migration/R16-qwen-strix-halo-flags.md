# R16 — Qwen3.6-35B-A3B (MoE) on Strix Halo: model + flags + comparison

- **Date:** 2026-05-20
- **Status:** pre-bench recon (run pending — model still downloading)
- **Driver:** user gave specific architecture comparison target:
  DGX Spark vLLM FP8 (from [habr 1033342](https://habr.com/ru/articles/1033342/))
  → Strix Halo llama.cpp Vulkan на той же модели

## Что за модель

`Qwen/Qwen3.6-35B-A3B` — MoE architecture (Mixture of Experts):

- **35B total parameters**, **3B active per token** (A3B = activated 3B)
- Hybrid Gated DeltaNet + Gated Attention layers (новая arch)
- Context: 260K tokens native (extended via RoPE)
- Optimal for: chat, coding, long-context reasoning
- Не путать с `Qwen3-30B-A3B` (предыдущая generation MoE)

Native quantizations from Qwen:
- FP8 (используется в DGX Spark vLLM)
- BF16 (reference)

## Почему Strix Halo bench != Spark bench (методологический disclaimer)

| Axis           | DGX Spark (user's prior)        | Strix Halo (this)                |
|----------------|----------------------------------|-----------------------------------|
| Hardware       | NVIDIA GB10 (Blackwell)         | AMD gfx1151 (RDNA 3.5)            |
| Engine         | vLLM cu130-nightly              | llama.cpp Vulkan b9049            |
| Quantization   | FP8 native (Qwen's release)     | GGUF Q4_K_M (community converted) |
| KV cache       | FP16                            | Q8_0 (recommended для Strix)      |
| Batching       | vLLM continuous batch           | llama-server serial (default)     |

**End-user tps comparable**, но это не apples-to-apples. Quality loss
Q4_K_M vs FP8 на 35B MoE — порядка 2-3% MMLU per community evals (см.
0xSero/HF README discussion). Для interactive chat workload неощутимо.

vLLM ROCm для gfx1151 пока не работает (vLLM 0.9 dropped non-CUDA, vLLM
0.10+ ROCm path не covers gfx1151 — see vllm-project/vllm#16934). llama.cpp
Vulkan — единственный production-ready путь на сейчас.

## DGX Spark baseline (verified from habr article)

Source: [habr.com/ru/articles/1033342](https://habr.com/ru/articles/1033342/)

Hardware: DGX Spark, GB10 Blackwell SM_121, 128 GiB unified LPDDR5x, 273 GB/s.

| Engine                                          | Quant       | tg t/s (single) | Notes                |
|-------------------------------------------------|-------------|-----------------|----------------------|
| `vllm/vllm-openai:cu130-nightly`                | FP8 native  | 51.0–52.5       | Default Qwen release |
| `vllm/vllm-openai:cu130-nightly`                | NVFP4 4-bit | 40.9            | NVIDIA experimental  |
| `ghcr.io/aeon-7/vllm-spark-omni-q36:v1.2`       | FP8 + DFlash kernels | 69.7 avg / 107 peak | community fork |

Methodology (from article): "Five single runs with different prompts
(T=0.7, up to 300 tokens per response), TTFT via streaming, parallel
4/8/16/32 requests, heavy prompt with 2K input and 400 response".

Parallel throughput at 32 concurrent requests: AEON-7 = 498.6 tok/s total.

## Strix Halo community baseline (verified)

Source: [0xSero/Qwen3.6-35B-A3B-GGUF-Strix on HF](https://huggingface.co/0xSero/Qwen3.6-35B-A3B-GGUF-Strix)

Hardware: Framework Desktop, AMD Ryzen AI MAX+ 395 / Radeon 8060S /
128 GB unified. Same SoC family as our box.

| Quant       | File size | pp512 (t/s) | tg128 (t/s) | Recommendation             |
|-------------|-----------|-------------|-------------|----------------------------|
| Q8_0        | 36.9 GB   | n/a         | n/a         | Near-lossless reference    |
| Q6_K        | 28.5 GB   | n/a         | n/a         | Balanced quality/size      |
| Q5_K_M      | 24.7 GB   | n/a         | n/a         | Good compression           |
| **Q4_K_M**  | 21.2 GB   | **1021**    | **70.2**    | **Production sweet spot**  |
| Q4_0        | 19.7 GB   | n/a         | **76.5**    | Fastest decode             |
| IQ4_NL      | 19.9 GB   | n/a         | n/a         | Quality-focused 4-bit      |
| DYNAMIC mix | 19 GB     | **1100**    | 64.0        | Fastest prefill, mixed     |

**Our pick:** `Qwen3.6-35B-A3B-Q4_K_M.gguf` (21.2 GB) — community
recommended baseline, имеет full pp+tg numbers для прямого сравнения.

## Recommended llama-server flags (community + recon synthesis)

From kyuz0/amd-strix-halo-toolboxes + 0xSero README + community Discord:

```
--host 0.0.0.0
--port 8080
--model /models/Qwen3.6-35B-A3B-Q4_K_M.gguf
-ngl 999                  # все GPU layers (offload)
--flash-attn              # обязательно для MoE на Strix (без него crash)
--cache-type-k q8_0       # KV cache quant — экономия ~50% VRAM
--cache-type-v q8_0
--ubatch-size 2048        # -ub 2048 (community optimum)
--batch-size 2048         # -b 2048
--no-mmap                 # mmap path не оптимизирован для GTT
--ctx-size 16384          # 16K разумно для bench; до 260K возможен
```

Optional опт:
- `--parallel N` для multi-stream serving (default 1 = serial)
- `--cont-batching` (continuous batching like vLLM)
- `--threads 8` (we have CPUs=8 в descriptor)

## Architecture notes (хитрости gfx1151 MoE)

1. **`--flash-attn` критично**: MoE без FA проваливается в naive
   attention pass и crash'ит / даёт 3× меньше tps. Это документировано
   в ggerganov/llama.cpp issue tracker.
2. **`--no-mmap`**: llama.cpp memory-maps model file by default. На GTT
   memory pool Strix Halo это работает плохо — mmap pages не оседают
   в GPU-visible address space. Explicit load = быстрее cold start, не
   медленнее в steady state.
3. **`-ub 2048 -b 2048`**: default `-b 512 -ub 512` оставляет много
   FMUL idle на 40-CU RDNA3.5. 2048 sweet spot per benchmark grid.
4. **KV q8_0**: для context до 16K — ОК. На 100K+ нужно вернуть к
   f16 (q8_0 KV дает quality drop на длинных контекстах per Apple
   research note).
5. **Не использовать `-DGGML_HIP_ROCWMMA_FATTN=ON`** в Vulkan path —
   это HIP-only флаг, в Vulkan image отсутствует.

## Verification matrix (что blocked / ready)

| Item                                               | Status |
|----------------------------------------------------|--------|
| Image tag `server-vulkan-b9049` exists в реестре   | ✓ verified R15 |
| Model `Qwen3.6-35B-A3B-Q4_K_M.gguf` exists on HF  | ✓ verified (3.7% downloaded at time of writing) |
| Strix Halo doctor: vulkan-tooling OK              | ✓ Vulkan RADV + Mesa 25.2.8 |
| User в group `docker render video`                | ✓ /etc/group confirmed |
| Free disk ≥ 30 GiB                                | ✓ 1.7 TiB free на nvme0n1p2 |
| docker socket accessible через `sg docker -c`     | ✓ pulls succeed |
| Community baseline available для direct compare   | ✓ 0xSero numbers + habr numbers |
| Pre-bench compose template update with flags      | ✓ committed Phase H prep |
| Phase H execution                                 | ⏳ downloads in progress |

## Expected outcome (готовлю prediction)

Если Strix Halo железо работает по community baseline и наша конфигурация
ничего не ломает:

- **tg128 ≥ 65 t/s** (within 7% of 0xSero's 70.2)
- **pp512 ≥ 950 t/s** (within 7% of 0xSero's 1021)

Если получим > 70 t/s tg — это будет означать что наш стек выходит на
production-quality уровень на этой модели **и обгоняет DGX Spark FP8** на
35% (Spark = 51-52 t/s).

Если < 60 t/s tg — это red flag:
- Проверить что image использует RADV (а не llvmpipe — мы это исправили
  Phase L.D follow-up, но verify через container env)
- Проверить что `-ngl 999` реально offload'ит все слои
- Проверить что `--no-mmap` применился
- Возможно нужен kernel HWE upgrade (см. doctor warn)

## Sources

- [habr 1033342 — DGX Spark Qwen 3.6 35B A3B FP8 benchmark](https://habr.com/ru/articles/1033342/)
- [0xSero/Qwen3.6-35B-A3B-GGUF-Strix on HF](https://huggingface.co/0xSero/Qwen3.6-35B-A3B-GGUF-Strix) — Strix Halo numbers
- [kyuz0/amd-strix-halo-toolboxes](https://kyuz0.github.io/amd-strix-halo-toolboxes/) — flag recommendations
- [strix-halo-guide](https://github.com/hogeheer499-commits/strix-halo-guide) — +25% from updates
- [llama.cpp issue tracker — gfx1151 MoE FA](https://github.com/ggml-org/llama.cpp/issues/21284) — context for prefill
- AGmind R3 / R4 / R15 — internal recon precursors
