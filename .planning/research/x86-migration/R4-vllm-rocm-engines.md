---
recon: R4 — vLLM ROCm / SGLang / MLC-LLM / TGI / engines matrix на gfx1151
date: 2026-05-19
status: completed
source_agent: general-purpose (~30 sources)
related: R3-llama-cpp-vulkan-hip.md, R10-strix-halo-bios-uma.md
---

# R4: Inference engines на gfx1151

## TL;DR — финальный выбор для AGmind

**M1 primary stack (минимум):**
- LLM: `llama.cpp` `llama-server` (Vulkan RADV)
- Embed: тот же `llama-server` (`--pooling mean` для BGE-M3)
- Rerank: второй `llama-server` с `bge-reranker-v2-m3` GGUF

**M2 upgrades (когда понадобятся):**
- `kyuz0`-style **vLLM-ROCm patched** — для tool calling / structured outputs /
  speculative decoding (с `--enforce-eager` penalty)
- `michaelf34/infinity` — для production embed+rerank с dynamic batching,
  OpenTelemetry, Prometheus

**Avoid (May 2026):** SGLang, MLC-LLM, TGI, native TEI, Ollama as prod
engine, FP8 anything, GPTQ/Marlin quants, AITER+MoE на RDNA.

## Engine compatibility matrix

| Engine | gfx1151 status | Patches required | Production maturity | M-stage |
|--------|---------------|------------------|---------------------|---------|
| **llama.cpp Vulkan** | ✅ works out-of-box | 0 | High | M1 primary |
| **llama.cpp HIP** | ✅ works with flags | 0 (build flags) | High | M1 fallback |
| vLLM upstream | ❌ "not planned" (#16621) | n/a | n/a | — |
| **vLLM ROCm patched** | ⚠️ works with 3-12 patches | TheRock + patches | Medium (community) | M2 |
| SGLang | ❌ Instinct-only, no port | n/a | None on gfx1151 | — |
| MLC-LLM | ⚠️ Vulkan path works | per-model TVM compile | Low (no benchmarks) | — |
| TGI | ❌ Instinct-only | n/a | None | — |
| Ollama | ⚠️ stale vendored llama.cpp (-56%) | n/a | Medium (dev only) | — |
| LM Studio | ⚠️ GUI tool, not for server | n/a | Desktop only | — |
| TEI | ❌ broken на consumer AMD | PR #295 stalled | None | — |
| **Infinity** | ⚠️ works likely (PyTorch ROCm) | HSA override | Medium | M2 embed |
| sentence-transformers + torch ROCm | ✅ works | HSA override | Low-medium | offline only |
| vLLM as embed server | ⚠️ degraded (V1 instability) | --enforce-eager | Low | — |

## Ключевые числа

### Chat completion 30B на gfx1151

| Engine | Quant | Decode t/s | Prefill t/s |
|--------|-------|-----------|-------------|
| **llama.cpp Vulkan RADV** | Q4_K_XL Qwen3-Coder 30B | **~97** | ~360 |
| llama.cpp HIP (rocWMMA+hipBLASLt) | Q4_K_XL | ~48-51 | **~986** |
| vLLM-ROCm patched (kyuz0/hec-ovi) | BF16 Qwen3.6-27B | ~4.3 | ~38 |
| Ollama (vendored stale) | Q4 | ~40 | — |

**Вывод:** llama.cpp Vulkan — primary для decode. HIP — secondary для
prefill-bound (RAG long-context). vLLM-ROCm — 20x медленнее, оправдан
только за tool-calling / structured generation features.

### Embeddings BGE-M3

| Engine | Status | Throughput estimate | Notes |
|--------|--------|---------------------|-------|
| **llama-server embedding mode** | ✅ works | hundreds embed/sec | same engine as LLM |
| Infinity (PyTorch+ONNX) | ✅ likely | hundreds → low thousands | dynamic batching |
| TEI | ❌ broken | n/a | PR #295 stalled, не для consumer AMD |
| sentence-transformers | ⚠️ works no batching | ~47ms latency | offline tooling |
| vLLM embed | ⚠️ degraded | lower than peers | --enforce-eager penalty |

## vLLM-ROCm patched detail

3 active community forks (все требуют TheRock ROCm nightlies):

| Repo | Status May 2026 | Patches | Reference model |
|------|----------------|---------|------------------|
| `kyuz0/amd-strix-halo-vllm-toolboxes` | Active | Toolbx/Podman + patches | various |
| `hec-ovi/vllm-qwen` | Active | 12 patches | Qwen3.6-27B BF16 4.3 t/s |
| `epheo/notes/strix-halo` | Recipe | 3 patches | guide-style |

**Минимальный набор патчей:**
1. Disable `amdsmi` probing
2. Hardcode gfx1151 detection
3. Update CMakeLists `AMDGPU_TARGETS`
4. `LD_PRELOAD=libtcmalloc_minimal.so.4` — fixes shutdown double-free
5. Compile vLLM C++ extensions with ROCm Clang, не host GCC

**Runtime env (vLLM-ROCm на gfx1151):**
```bash
export HSA_OVERRIDE_GFX_VERSION=11.5.1
export TORCH_ROCM_AOTRITON_ENABLE_EXPERIMENTAL=1
export HIP_FORCE_DEV_KERNARG=1
```
Kernel cmdline: `ttm.pages_limit=30408704 amdgpu.gtt_size=128`.

**Quantization gfx1151:**
- BF16 — works (primary path)
- FP16 — works
- **FP8 — broken** (RDNA 3.5 lacks hardware, emulates at BF16 speed)
- AWQ 4/8-bit — works но требует `--enforce-eager`
- **GPTQ/Marlin — broken** (CUDA-only kernels)
- **MXFP4 — broken** (требует CDNA3/CDNA4)
- INT4 bitsandbytes — partial, 49% bandwidth efficiency

**Critical bugs (vLLM issue #32180):**
- V1 engine instability → must use `--enforce-eager` (теряем V1 perf)
- 128 MB L3 cache не используется для KV management
- RCCL/NCCL ships без gfx1151 kernels (kyuz0 patched 2026-02-02)
- AITER не работает с MoE (CDNA-specific assumptions)

## Implications для ADR-0002

ADR-0002 (compute backend abstraction) был написан с предположением что
backends = {cpu, vulkan, rocm, npu_stub}. Новые данные показывают что
этого недостаточно — внутри **vulkan** и **rocm** есть выбор engine:

```python
# agmind/compute/backends/rocm.py
class ROCmBackend(Backend):
    """Abstract ROCm backend; concrete engine via env or auto-detect."""

    @classmethod
    def make(cls, engine: str = "auto") -> "ROCmBackend":
        if engine == "auto":
            engine = _select_rocm_engine()
        return {
            "llama_cpp": LlamaCppHIPBackend,
            "vllm": VLLMROCmBackend,    # M2 only
            "infinity": InfinityBackend,  # M2 embed only
        }[engine]()
```

Аналогично для Vulkan (llama.cpp primary, в будущем возможно MLC если
он стабилизируется).

## Что положить в спеку (требует апрува)

**Update в AGMIND_MIGRATION_SPEC.md Part 1.3 «Разрешено / предпочтительно»:**

```
- llama-cpp-python собранный с GGML_VULKAN=ON или GGML_HIP=ON (primary)
- vLLM версии с ROCm support через community fork (kyuz0-style, secondary M2)
- Infinity (michaelfeil) для production embed+rerank (M2)
- sentence-transformers + torch ROCm для offline tooling (low-concurrency only)
```

**Update в AGMIND_MIGRATION_SPEC.md Part 1.3 «Запреты»:**

Добавить категорию:
```
| **Broken на gfx1151** | TEI (PR #295 stalled); GPTQ/Marlin quants; MXFP4;
FP8; AITER+MoE на RDNA; SGLang; MLC-LLM (no benchmarks); TGI; Ollama-as-prod
(vendored stale 56%); FlashInfer FP8 |
```

## Deferred items для migration_progress.json

- `DEF-013`: backend engine selection logic — env `AGMIND_BACKEND_ENGINE`
  (default `llama_cpp`) → auto на основании model size + workload type
- `DEF-014`: M2 upgrade gate — когда добавить VLLMROCmBackend + InfinityBackend
- `DEF-015`: model registry tags — какие модели broken на каких engine
  (GDN-family → fallback HIP; FP8/MXFP4 → reject)
- `DEF-016`: smoke benchmark targets:
  - LLM: Qwen3-Coder 30B Q4_K_XL via llama.cpp Vulkan — decode ≥85 t/s
  - Embed: BGE-M3 GGUF via llama-server — ≥100 embed/sec @ 1024 tokens
  - Rerank: bge-reranker-v2-m3 — latency p99 ≤200ms на single query

## Sources

- vLLM issue #16621 (gfx1151 not planned), #32180 (V1 instability)
- vLLM blog 2026-02-27 (ROCm attention)
- ROCm 7.13 preview docs + RDNA3.5 system optimization
- kyuz0/amd-strix-halo-vllm-toolboxes + DeepWiki
- hec-ovi/vllm-qwen + openclaw-strix-embed
- blog.epheo.eu (Running vLLM on Strix Halo)
- llm-tracker.info Strix Halo
- kyuz0/amd-strix-halo-toolboxes (llama.cpp grid)
- hardware-corner.net + slb350/strix-benchmarks
- SGLang AMD GPU docs + 2026 Q2 roadmap (#23494)
- MLC-LLM GPU drivers docs
- TGI ROCm docs (Instinct-only)
- HF blog — Infinity on AMD (gfx942/gfx94a/gfx1100 listed)
- TEI repo + PR #295 (stalled, MI200/MI300 only)
- IDFS AI — AMD GPUs 2026
- Red Hat developer blog — speculative decoding vLLM
- tinycomputers.io — Upgrading ROCm 7.0→7.2 on gfx1151
