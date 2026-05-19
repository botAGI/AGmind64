---
recon: R5 — HF TEI ROCm + embed/rerank stack для gfx1151, май 2026
date: 2026-05-19
status: completed
source_agent: general-purpose (~30 sources)
related: R3-llama-cpp-vulkan-hip.md, R4-vllm-rocm-engines.md
---

# R5: Embed/Rerank engines для Strix Halo

## TL;DR

1. **TEI ROCm not viable на gfx1151** — PR #853 merged 2026-04-02 но
   только MI200/MI300 tested; PR #860 (Dockerfile-amd) ещё open; нет
   pre-built image для AMD GPU.
2. **llama.cpp `llama-server` (embedding mode) — primary для embed+rerank**
   на gfx1151. Тот же engine что и LLM, OpenAI-compatible API.
3. **Infinity** (michaelf34/infinity) — secondary M2 upgrade. ROCm images
   опубликованы для gfx942/gfx94a/**gfx1100**; для gfx1151 — собирать
   локально.
4. **TEI CPU image** (`cpu-1.9`) — CPU fallback для x86 без AMD GPU.
5. **bge-m3 GGUF Q8_0** + **bge-reranker-v2-m3 GGUF Q8_0** — 1.4 GB
   total, ~1.5 GB VRAM/GTT. Тривиально на Strix Halo.

## Engine matrix (gfx1151)

| Engine | Status | API | Production |
|--------|--------|-----|------------|
| TEI ROCm | ❌ MI200/MI300 only (PR #860 open) | /embed /rerank | NO |
| TEI CPU | ✅ stable | /embed /rerank | M1 fallback |
| **llama-server embedding** | ✅ **works** | /v1/embeddings | **M1 primary** |
| **llama-server reranking** | ✅ works | /v1/rerank | **M1 primary** |
| Infinity | ⚠️ собирать локально (gfx1100 image как reference) | /v1/embeddings | M2 |
| sentence-transformers + torch ROCm | ⚠️ developer wheels | no REST API | offline only |
| fastembed | ❌ CUDA-only GPU support | n/a | CPU-fallback |
| vLLM embed | ⚠️ degraded на gfx1151 | /v1/embeddings | NO |

## bge-m3 GGUF sizes

| Quant | Disk | Inference mem (single) | Batch 32 | Batch 256 |
|-------|------|-------------------------|----------|------------|
| F16 | 1.16 GB | ~1.4 GB | ~2.5 GB | ~5 GB |
| **Q8_0** | **635 MB** | **~0.9 GB** | ~2 GB | ~4 GB |
| Q6_K | 499 MB | ~0.8 GB | ~1.8 GB | ~3.8 GB |
| Q5_K_M | 468 MB | ~0.7 GB | ~1.6 GB | ~3.6 GB |
| Q4_K_M | 438 MB | ~0.6 GB | ~1.5 GB | ~3.5 GB |
| Q2_K | 366 MB | (drift в качестве) | n/a | n/a |

**Recommendation:** Q8_0 (sweet spot quality/size). Ниже Q5_K_M cosine
similarity drift becomes measurable.

bge-reranker-v2-m3 — идентичный footprint (тот же XLM-RoBERTa).

## Service topology recommendation

**Два отдельных `llama-server` instance** на разных портах:

```yaml
embed:
  image: agmind/vulkan:latest
  command: |
    llama-server
    -m /models/bge-m3-Q8_0.gguf
    --embeddings --pooling cls
    --port 8081 -ngl 99 --no-mmap
  ports: ["8081:8081"]

rerank:
  image: agmind/vulkan:latest
  command: |
    llama-server
    -m /models/bge-reranker-v2-m3-Q8_0.gguf
    --reranking
    --port 8082 -ngl 99 --no-mmap
  ports: ["8082:8082"]
```

Причины раздельных процессов:
1. Pooling modes различные (`cls` vs `rank`)
2. Reranker called less frequently → independent scaling
3. Memory isolation: 1.4 GB total trivial на 128 GB UMA
4. Independent restart semantics

## Multilingual / Russian models comparison

| Model | Params | Russian quality | Notes |
|-------|--------|-----------------|-------|
| **BAAI/bge-m3** (primary) | 568 M | High (SOTA on RusBEIR) | Hybrid retrieval dense+sparse+ColBERT |
| Qwen3-Embedding-0.6B | 509 M | High (MTEB-multi 64.33) | Newer, TEI-supported, MRL 32-1024 dim |
| Qwen3-Embedding-4B | 4.02 B | Higher но overkill | "Very Expensive" в TEI docs |
| intfloat/multilingual-e5-large-instruct | 560 M | Good | TEI-supported, instruction-aware |
| nomic-embed-text-v2-moe | 475 M | Decent | MoE arch |
| jina-embeddings-v3 | 570 M | Good | LoRA adapters, TEI-supported |
| ai-forever/ru-en-RoSBERTa | 350 M | Native Russian | RU/EN only, ruMTEB SOTA |

**Decision:** bge-m3 primary, Qwen3-Embedding-0.6B как M2 A/B candidate.

## llama-server endpoint details

**Embeddings:**
- `POST /v1/embeddings` (OpenAI-compatible)
- `POST /embeddings` (native, поддерживает все pooling modes)
- Required: `--embeddings --pooling cls` (BGE-M3) или `--pooling mean`

**Reranking:**
- `POST /reranking`, `/rerank`, `/v1/rerank`
- Payload: `{query, documents[], top_n?}`
- Required: `--reranking` (либо `--embedding --pooling rank`)

**Caveat для non-BGE rerankers (например Qwen3-Reranker):** требуется
правильный маппинг `cls.output.weight` в `convert_hf_to_gguf.py`, иначе
scores → 4.5e-23. **bge-reranker-v2-m3 не affected** (standard XLM-RoBERTa).

## API abstraction в `agmind/compute/backends/rocm.py`

Expose **TEI-compatible API** (POST /v1/embeddings, POST /v1/rerank)
независимо от backing engine. Это:
- production Strix Halo → backed by llama.cpp
- production MI300 → backed by TEI ROCm (когда #860 merged)
- production CPU → backed by TEI CPU image
- production NVIDIA → backed by TEI CUDA image

Минимум coupling: `agmind` always calls OpenAI-compatible REST, engine
swap = config change.

## Fallback layering

1. **Primary на gfx1151:** llama.cpp (verified working)
2. **Future на gfx1151:** TEI ROCm когда #860 + gfx1151 PyTorch stable
   (12-18 месяцев out)
3. **CPU fallback:** TEI CPU image `cpu-1.9` (x86_64 / arm64)
4. **Alternative на gfx1151:** Infinity locally-built для gfx1151
   (PyTorch ROCm path, multi-modal CLIP/CLAP support)

## Anti-recommendations (May 2026)

**НЕ использовать на gfx1151:**
- vLLM embed — fragile (Triton HIP errors), `--enforce-eager` penalty
- onnxruntime-rocm — undocumented gfx1151, silent CPU fallback risk
- TEI ROCm directly — wait for #860 merge + gfx1151 PyTorch stable
- fastembed-gpu — CUDA-only

## Update в AGMIND_MIGRATION_SPEC.md (proposed)

Part 1.3 «Разрешено / предпочтительно»:
```
- llama-cpp-python собранный с GGML_VULKAN=ON или GGML_HIP=ON (primary
  для inference + embed + rerank)
- Infinity (michaelfeil) для production embed+rerank с dynamic batching
  (M2 upgrade)
- sentence-transformers + torch ROCm для offline tooling (low-concurrency)
```

Part 1.3 «Запреты»:
```
| Broken на gfx1151 | TEI custom image (PR #860 not merged); fastembed-gpu;
vLLM embed как primary (degraded `--enforce-eager`); onnxruntime-rocm на
gfx1151 |
```

Part 1.2 add «Default embedding engine»:
```
- bge-m3 GGUF Q8_0 → llama-server `--pooling cls` port 8081
- bge-reranker-v2-m3 GGUF Q8_0 → llama-server `--reranking` port 8082
- Total: 1.4 GB disk, ~1.5 GB VRAM
```

## Sources

- TEI repo + AMD GPU docs + supported_models.md
- TEI PRs #853 (merged 2026-04-02), #856, #860 (open)
- TEI Issue #108 (full thread)
- llama.cpp discussion #20856 (Known-Good Strix Halo Stack)
- llama.cpp server README (embeddings + reranking)
- gpustack/bge-m3-GGUF, bge-reranker-v2-m3-GGUF, lm-kit/bge-m3-gguf
- HF blog Michael Feil — Infinity on AMD
- Infinity GitHub
- ROCm/TheRock discussions #655 (PyTorch ROCm wheels gfx1151)
- scottt/rocm-TheRock v6.5.0rc-pytorch
- TinyComputers ROCm 7.0→7.2 gfx1151
- ruMTEB paper arxiv.org/abs/2408.12503
- ai-forever/ru-en-RoSBERTa
- knightli scoreboard 2026
