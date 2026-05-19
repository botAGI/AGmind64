---
recon: R-llm-models — GGUF inventory per tier на Strix Halo gfx1151
date: 2026-05-19
status: completed
source_agent: general-purpose (~60 sources, kyuz0/visorcraft/slb350/0xSero/hogeheer499)
related: R3-llama-cpp-vulkan-hip.md, R5-tei-embed-rerank.md
---

# R-LLM/GGUF: Models per tier для Strix Halo

## TL;DR — финальный выбор

| Tier | LLM (primary) | Disk | Strix tg | Status |
|------|---------------|------|----------|--------|
| **S** (16GB) | Qwen3.5-9B UD-Q4_K_XL | 5.97 GB | ~50 inferred | unsloth |
| **M** (32GB) | gemma-4-26B-A4B-it UD-Q4_K_M | 16.9 GB | **52.9** verified (slb350) | unsloth, integrated VLM |
| **L** (64GB) | Qwen3.6-35B-A3B UD-Q4_K_XL | 22.4 GB | **60-70** verified | unsloth, GDN+Gated-Attn |
| **L-strix** | 0xSero/Qwen3.6-35B-A3B-Strix DYNAMIC | 19 GB | **64** tg, **1100** pp | Strix-optimized |
| **XL** (128GB) | gpt-oss-120b MXFP4_MOE | 62.8 GB | **49** verified | native MXFP4 |
| **XXL** (128GB+) | MiniMax M2.5 Q3_K_M | 101.76 GB | **32.8** verified | 228.7B MoE |

**Embed:** bge-m3 Q8_0 (635 MB, R5-verified) primary | Qwen3-Embedding-0.6B Q8_0 (639 MB) A/B
**Rerank:** bge-reranker-v2-m3 Q8_0 (635 MB) primary | Qwen3-Reranker-0.6B Q8_0 (639 MB) A/B
**VLM:** Qwen2.5-VL-7B Q4_K_M + mmproj-f16 (4.7+0.6 GB) primary | Qwen2.5-VL-3B (3.2 GB) light

## Критический update vs предыдущие реконы

**GDN Vulkan shader landed** (issue #20354 closed, ~Mar 2026) — Qwen3.5/3.6 A3B
family теперь **работает на RADV**, не нужен HIP fallback. Требуется
llama.cpp build **≥ b8765**.

Обновить spec §1.2.2 (ROCm): убрать "GDN-family fallback" rationale.
Обновить spec §1.2.6 (selection rules): `model=GDN_family → rocm/llama_cpp`
становится opt-in а не mandatory.

## llama.cpp build requirements

| Component | Min | Recommended |
|-----------|-----|-------------|
| llama.cpp build | **b8765** (GDN shader + Wave32 FA + graphics queue) | **b9049** (verified hogeheer499) |
| llama-cpp-python | 0.3.23 (PyPI 2026-05-11) | latest |
| shaderc | **2025.1** | 2025.1 (НЕ 2025.2 — ломает build, #15344) |

## Antipatterns (12 entries) — кратко

| ID | Pattern | Why broken |
|----|---------|------------|
| GDN_OLD_BUILD | Qwen3.5/3.6 A3B на llama.cpp < b8765 | shader fallback CPU → 11.87 t/s |
| MXFP4_DENSE | dense + MXFP4 | unsloth retired (только MOE-only) |
| DENSE_70B_PLUS | Llama 3.1 70B / Mistral Medium 3.5 128B | tg 5/3 t/s — unfit |
| MINICPM_V_4_GGUF | vision pipeline broken | images ignored (#957) |
| OLLAMA_VENDORED | Ollama llama.cpp | -56% vs standalone (#15601) |
| AMDVLK_ICD | AMDVLK 2 GiB cap | ломает ≥30B dense |
| VULKAN_MMPROJ_DEGRADED | Qwen2.5-VL specific images | corruption vs CUDA (#20081) |
| JINA_V3_NO_GGUF | jina-embeddings-v3 | нет conversion (#9585) |
| VULKAN_DEVICELOST | большие ubatch/ctx | pin -ub 2048 -b 2048 (#20515) |
| HIP_GT_6GB_HANG | HIP models >6 GB | нужен `-dio` flag |
| ROCWMMA_LONG_CTX | long ctx pp + rocWMMA | медленнее standard HIP |
| SHADERC_2025_2 | build flag | используй shaderc 2025.1 |

## Sources

Полный отчёт + URL'ы → см. в `agent task notification` 2026-05-19 либо
agent output `/tmp/claude-1000/.../tasks/a044266897c821f3d.output`.
Главные:
- unsloth GGUF репозитории (Qwen3.5/3.6, gemma-4, GPT-OSS, MiniMax, Mistral)
- gpustack / bbvch-ai (bge-m3, bge-reranker-v2-m3)
- Qwen / ggml-org (Qwen3-Embedding/Reranker GGUF)
- Mungert (Qwen2.5-VL VLM GGUFs)
- 0xSero/Qwen3.6-35B-A3B-GGUF-Strix (DYNAMIC quant)
- kyuz0/visorcraft/slb350/hogeheer499 benches
