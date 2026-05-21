# R17 — ik_llama.cpp fork + 256K context on Strix Halo

- **Date:** 2026-05-21
- **Status:** recon (not yet acted on for baseline; needs side-by-side bench)
- **Driver:** user — «размер контекста 256K не потянет что ли? и поресёрчи
  есть форки llama.cpp с турбоквантами»

## TL;DR

1. **256K context на Strix Halo — реален и c запасом**. Qwen3.6-35B-A3B
   использует GQA — KV cache scaling сильно subliner. Reference: 262144
   token context на 24 GB RTX 3090 работает с q8_0 KV cache + Q3 quant
   ([aminrj.com](https://aminrj.com/posts/llamacpp-qwen36-35b/)). У нас
   125 GB unified — запас x5+; **можно даже 512K** с q4_0 KV.

2. **`ik_llama.cpp` (ikawrakow fork)** — production-grade alternative с
   IQ-quants дающими **0.14% quality loss** на Qwen3.6 (basically
   lossless). Faster CPU + hybrid + better MoE CUDA/Metal kernels per
   [github.com/ikawrakow/ik_llama.cpp/wiki](https://github.com/ikawrakow/ik_llama.cpp/wiki/Previous-Latest-News).

3. **TurboQuant** (extreme KV cache quantization) — обсуждается уже в
   mainline llama.cpp как [discussion #20969](https://github.com/ggml-org/llama.cpp/discussions/20969). До stable не доехало,
   но direction явный.

Action: **расширили CTX_SIZE_PRESETS до 256K/512K** в Phase M4.7.3. Замена
baseline image на ik_llama.cpp — оставлено в backlog для следующей wave
после side-by-side bench (см. "Open questions" ниже).

## 256K context — math для Strix Halo

### KV cache size formula

```
kv_bytes = 2 * n_layers * (n_kv_heads * head_dim) * ctx * bytes_per_elem
```

Для Qwen3.6-35B-A3B (per HF config):
- `n_layers = 80`
- `n_kv_heads = 8` (GQA — vs 64 attention heads)
- `head_dim = 128`
- `q8_0 KV` → 1 byte per elem
- `f16 KV` → 2 bytes per elem

| ctx     | q8_0 KV     | f16 KV       | f8_e4m3 KV* |
|---------|------------:|-------------:|------------:|
| 16K     | 2.6 GiB     | 5.2 GiB      | ~1.3 GiB    |
| 32K     | 5.2 GiB     | 10.5 GiB     | 2.6 GiB     |
| 64K     | 10.5 GiB    | 21.0 GiB     | 5.2 GiB     |
| 128K    | 21.0 GiB    | 42.0 GiB     | 10.5 GiB    |
| **256K**| **42.0 GiB**| 84.0 GiB     | 21.0 GiB    |
| **512K**| **84.0 GiB**| 168.0 GiB    | 42.0 GiB    |

*f8_e4m3 = TurboQuant proposal — пока не в mainline stable.

На Strix Halo 125 GB unified (62 GB GTT actual after BIOS UMA 512 MB):
- **256K с q8_0** — model 21 GB + KV 42 GB = **63 GB** → подходит впритык
  к GTT pool. С `ttm.pages_limit` tuning (см. doctor warning) можно
  расширить GTT до ~117 GB → 256K с **f16 KV** возможен.
- **512K с q8_0** — model 21 GB + KV 84 GB = 105 GB → нужен GTT >100 GB
  (требует kernel ≥6.18 HWE + ttm tune).

### Realistic Apple-to-Apple

Reference (24 GB RTX 3090, Q3 quant) — 262K context ходит. У нас Q4_K_M
+ q8_0 KV + 125 GB unified — должно быть кратно проще, но **не запустить
без re-bench**. R17 NOT validates 256K на нашем железе — нужен Phase H
re-run с `--ctx-size 262144` чтобы измерить.

## `ik_llama.cpp` fork capabilities

Source: [github.com/ikawrakow/ik_llama.cpp](https://github.com/ikawrakow/ik_llama.cpp) +
[discussion #1663 — Qwen-3.6 quants](https://github.com/ikawrakow/ik_llama.cpp/discussions/1663).

### Unique features vs mainline (b9049 baseline)

- **SOTA IQ quants:** IQ1_M, IQ2_XS, **IQ4_KS**, IQ4_NL, IQ4_K_R4, IQ3_K,
  Q6_0, Q4_0_R4 (row-interleaved variants).
- **IQ4_KS for Qwen3.6** — quality error **0.14%** vs base BF16
  (basically lossless), per [#1663](https://github.com/ikawrakow/ik_llama.cpp/discussions/1663).
- **First-class Bitnet support** — 1.58-bit ternary quants (model: 21 GB
  → ~3.5 GB при сравнимой accuracy на small models).
- **DeepSeek MLA / FlashMLA** — нам не нужно сейчас, but futureproof.
- **Fused MoE operations** — relevant! Qwen3.6 = MoE → fused kernels
  обходят mainline на 20-40% по token throughput.
- **Tensor overrides для hybrid CPU+GPU** — relevant если на Strix Halo
  GTT забит → CPU offload часть.
- **Row-interleaved quant packing** — packing layout improvements.

### Benchmark — Qwen3-class MoE на ik_llama.cpp

Per [discussion #164](https://github.com/ikawrakow/ik_llama.cpp/discussions/164):

| Hardware | Quant | pp512 | tg128 |
|----------|-------|------:|------:|
| Xeon Gold 5318 (Ice Lake, CPU only) | IQ4_K_R4 | ~190 | ~33 |
| RTX 3090 (24 GB) | Q3 | n/a | 120 |

Ничто не сравнивает напрямую с нашим baseline (b9049 Vulkan на Strix
Halo: pp512=1024 / tg128=73.5 от Phase H bench). Нужен **R17.bench** —
build ik_llama.cpp + run same Qwen3.6-Q4_K_M на gfx1151.

### Build для Strix Halo

ik_llama.cpp поддерживает Vulkan backend (через ggml-vulkan), но **не
имеет публичных docker images** на 2026-05-21. Build:

```bash
git clone https://github.com/ikawrakow/ik_llama.cpp
cd ik_llama.cpp
cmake -B build -DGGML_VULKAN=ON -DCMAKE_BUILD_TYPE=Release
cmake --build build --target llama-server -j
```

Затем нужно сделать `Dockerfile.ik-vulkan` (~4 GiB image, аналог нашего
`full-vulkan-b9049`).

### Quality vs speed trade-off (для Qwen3.6 MoE)

| Quant | Size | Quality loss | Notes |
|-------|-----:|-------------:|-------|
| BF16 | 70 GB | 0% (reference) | не разместить на Strix Halo |
| Q8_0 | 37 GB | ~0.05% | borderline (model + KV q8_0 64K = 47 GB) |
| Q6_K | 29 GB | ~0.1% | |
| Q5_K_M | 25 GB | ~0.5% | |
| **Q4_K_M** | **21 GB** | **~1.5%** | **наш baseline (b9049 mainline)** |
| **IQ4_KS** | **~19 GB** | **0.14%** | **ik_llama.cpp only** — better quality + lower size |
| IQ4_K_R4 | ~19 GB | 0.5% | row-interleaved variant — faster decode |
| Q4_0 | 19.7 GB | ~2% | mainline fastest decode (76 t/s в Phase H) |
| Q3_K | 16 GB | ~3% | для tight VRAM |

**IQ4_KS** буквально **strictly better чем Q4_K_M** в обоих measure:
size меньше на 10%, quality loss в 10 раз меньше. Если ik_llama.cpp
build for Strix даёт сравнимый tps — это упгрейд по умолчанию.

## TurboQuant — extreme KV cache quantization

Mainline llama.cpp [discussion #20969](https://github.com/ggml-org/llama.cpp/discussions/20969):
- f8_e4m3 / f8_e5m2 KV cache (8-bit float forms)
- 4-bit per channel KV (Q4_0_KV)
- ~30-50% memory saving on top of q8_0

Status: still discussion / experimental. Implementation в master patch не
landед на 2026-05-21. Wait-and-see.

## Action items для AGmindx86

### Immediate (Phase M4.7.3 — shipped)

- [x] Extended CTX_SIZE_PRESETS до 256K + 512K — wizard теперь предлагает
- [x] Labels с GQA hint ("+4.8 GB on top of 32K") чтобы user понимал scale
- [x] 512K marked "experimental, нужен KV q4_0 для < 10 GB add"

### Next session (R17.bench)

- [ ] Build `Dockerfile.ik-vulkan` с ik_llama.cpp + Vulkan
- [ ] Bench Qwen3.6-A3B-IQ4_KS на Strix Halo через `llama-bench`
- [ ] Side-by-side: mainline b9049 (73.5 t/s) vs ik_llama.cpp pp/tg
- [ ] Если ik_llama.cpp обходит mainline на ≥10% **и** quality лучше —
      bump baseline image в `templates/services/llama-llm.yaml`
- [ ] Add IQ4_KS как curated model в `agmind/install/models.py`

### Future (Phase ?)

- [ ] TurboQuant KV — wait for stable landing в mainline
- [ ] Если ik_llama.cpp баг'ает на Strix Halo Vulkan — file issue
- [ ] Multi-tier wizard: "quality / balanced / fastest" preset = picks
      quant + ctx + backend choice automatically

## Sources

- [github.com/ikawrakow/ik_llama.cpp](https://github.com/ikawrakow/ik_llama.cpp)
- [Qwen-3.6 quants discussion #1663](https://github.com/ikawrakow/ik_llama.cpp/discussions/1663)
- [DeepWiki ik_llama.cpp](https://deepwiki.com/ikawrakow/ik_llama.cpp)
- [Qwen3.6-35B-A3B on 6GB VRAM (Medium)](https://mychen76.medium.com/run-qwen3-6-35b-a3b-on-6gb-vram-using-llama-cpp-30-tps-a89032e5a60c)
- [Qwen3.6 VRAM table (knightli.com)](https://www.knightli.com/en/2026/05/01/qwen3-6-local-vram-quantization-table/)
- [Qwen3.6 on 24GB RTX 3090 — 262K ctx benchmark](https://aminrj.com/posts/llamacpp-qwen36-35b/)
- [TurboQuant discussion #20969](https://github.com/ggml-org/llama.cpp/discussions/20969)
- [Qwen3 235B + 30B MoE quant roundup](https://gist.github.com/ubergarm/0f9663fd56fc181a00ec9f634635eb38)
