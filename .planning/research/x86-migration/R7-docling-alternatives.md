---
recon: R7 — Docling без CUDA, альтернативы document parsing
date: 2026-05-19
status: completed
source_agent: general-purpose (~30 sources)
related: R3, R4, R5, R11
---

# R7: Docling alternatives на Strix Halo / CPU x86_64

## TL;DR — drop-in replacement

**`quay.io/docling-project/docling-serve-cpu:v1.18.0`** — production-ready
drop-in замена для `docling-serve-cu130:v1.16.1`. Same FastAPI API, same
Phase 43 presets (FAST/BALANCED/SCAN) работают без изменений.

CPU throughput на 16C Zen 5:
- **FAST preset:** ~30-40 pages/min
- **BALANCED:** ~12-18 pages/min text PDF, ~3-5 pages/min scan
- **SCAN с VLM remote:** ~2-4 pages/min

## Engine matrix (Strix Halo / CPU)

| Tool | License | CPU support | ROCm | REST API | Russian OCR | M1 fit |
|------|---------|-------------|------|----------|-------------|--------|
| **Docling 2.94 / docling-serve-cpu 1.18.0** | MIT | ✅ default | local build only (6.3) | ✅ docling-serve | EasyOCR cyrillic_g2 | **M1 primary** |
| MinerU 3.1.14 | MinerU OSL | ✅ pipeline backend | not official | mineru-api FastAPI | PaddleOCR PP-OCRv5 (strong cyrillic) | M2 fallback |
| Marker 1.10 (datalab) | OpenRAIL+GPL | ✅ | not native | 3p marker-api | Surya OCR 90+ langs | deferred |
| Unstructured 0.18 | Apache 2.0 | ✅ CPU-first | not native | SaaS + OSS | Tesseract + paddle opt | deferred |
| PyMuPDF + Tesseract | AGPL+Apache | ✅ | n/a | DIY | rus.traineddata | quick fallback |
| **PaddleOCR-VL 1.5** | Apache 2.0 | ✅ OpenVINO | ✅ Day-0 ROCm 7 на MI Instinct | via vLLM serve | 109 langs SOTA | M3 candidate |
| olmOCR-2 (Qwen2.5-VL-7B) | Apache 2.0 | ✅ slow | not native | CLI only | multilingual 82.4 olmOCR-Bench | batch only |
| GROBID 0.8 | Apache 2.0 | ✅ Java | n/a | ✅ /api/processFulltextDocument | **F1=0.09 на Cyrillic** | ❌ disqualified |
| Nougat (Meta) | MIT | ✅ | not native | none | English/Latin only | ❌ **frozen, no 2026 commits** |
| LlamaParse v2 | proprietary SaaS | n/a (cloud) | n/a | managed | 100+ langs | ❌ SaaS, не self-host |

## Performance comparison (per Docling Technical Report)

| Backend | Text PDF | Scan PDF | Notes |
|---------|---------|----------|-------|
| x86 CPU (Zen 3, 8 vCPU) | 3.1 s/page avg | 16.3 s/page p95 | EasyOCR — dominant cost (13 s/page) |
| **16C Zen 5 (Strix Halo)** | **~1.5-2 s/page** | **~6-10 s/page** | extrapolated, AVX-512 |
| M3 Max SoC | 1.27 s/page | n/a | |
| L4 GPU | 0.48 s/page | n/a | ~6× faster than CPU |

## docling-serve-cpu features

- MIT license, models pre-baked в image
- mimalloc preloaded via `LD_PRELOAD` для memory perf
- Both `linux/amd64` и `linux/arm64`
- `/health` endpoint, Kubeflow/Redis Queue orchestration
- Same `/v1/convert/file` API как cu130
- Phase 43 presets (`do_ocr`, `do_table_structure`, `picture_description_api`) работают без изменений

## VLM picture description

Docling поддерживает remote OpenAI-compatible chat-completion для VLM:

```yaml
do_picture_description: true
picture_description_api:
  url: http://llamacpp-vlm:8080/v1/chat/completions
  prompt: "Опиши картинку"
  concurrency: 2  # start conservative на Strix Halo
```

Запускаем VLM на отдельном `llama-server` с qwen2.5-vl или gemma-3-4b-it.
ROCm/Vulkan ускоряет VLM, docling-serve остаётся CPU.

## Compose update (для M1)

```yaml
# templates/docker-compose.yml (новый)
docling:
  image: quay.io/docling-project/docling-serve-cpu:v1.18.0
  cpus: 12
  mem_limit: 16g  # было 10g — нужно больше для CPU layout+OCR
  environment:
    DOCLING_SERVE_ALLOW_CUSTOM_OCR_CONFIG: "true"
  # Удалить:
  # deploy.resources.reservations.devices: [{driver: nvidia...}]
  # NVIDIA_VISIBLE_DEVICES
```

## Что отложено до M2/M3

- **MinerU** как cyrillic-quality fallback (DSL `agmind-kb-mineru-router.yaml`
  уже в legacy)
- **Marker** для academic PDFs (но OpenRAIL/GPL license complexity)
- **PaddleOCR-VL 1.5** (best AMD-ROCm path но no FastAPI parity yet)
- **olmOCR-2** (batch-only, no REST API)
- **docling-serve-rocm** (build локально, ROCm 6.3 target — устарело vs
  наш ROCm 7.2 stack)

## Update в AGMIND_MIGRATION_SPEC.md (proposed)

Part 1.4 (versions.env equivalent):
```
DOCLING_IMAGE=quay.io/docling-project/docling-serve-cpu:v1.18.0
# (legacy был: docling-serve-cu130:v1.16.1)
```

Part 1.2 add «Document parsing»:
```
- Primary: docling-serve-cpu (Apache 2.0 MIT, Phase 43 presets compatible)
- M2 fallback: MinerU pipeline backend (для cyrillic-heavy scans)
- M3 candidate: PaddleOCR-VL 1.5 (Day-0 ROCm 7 support, MI Instinct only —
  для gfx1151 нужна валидация)
- VLM offload: через picture_description_api к local llama-server
```

## Известные подводные камни

1. **VLM concurrency=8 default** — на Strix Halo aggressive. Начинать
   с concurrency=2 (shared RAM с main LLM).
2. **llama-server для VLM** требует `--mmproj` flag для multimodal model
   (gemma-3-4b-it / qwen2.5-vl).
3. **EasyOCR cyrillic_g2** на CPU = адекватный русский OCR но **MinerU
   PaddleOCR PP-OCRv5 чище** (legacy benchmark: "АКТ № 442" vs
   "AKT Ng 442"). Defer MinerU как M2.
4. **docling-serve-rocm НЕ published** upstream — build только локально,
   target ROCm 6.3 (устарело для нашего 7.2 stack).
5. **gfx1151 для PyTorch имеет open memory-access faults** (ROCm/issues/5824,
   pytorch/issues/171687). ROCm-acceleration document parsing — high risk,
   low reward, defer to M3.

## Sources

- docling-project/docling 2.94.0 (2026-05-18)
- docling-project/docling-serve 1.18.0 (2026-05-07)
- Quay docling-serve-cpu
- Docling Technical Report (arXiv 2408.09869v4)
- Docling Discussion #2679 (llama.cpp OpenAI-compatible)
- opendatalab/MinerU 3.1.14, MinerU changelog
- neka-nat/mineru-api
- MinerU2.5 paper (arXiv 2509.22186)
- marker-pdf PyPI, datalab-to/surya
- Unstructured-IO/unstructured Apache 2.0
- facebookresearch/nougat (frozen)
- PyMuPDF docs
- GROBID Cyrillic F1=0.09 (CEUR-WS Vol-3164)
- PaddleOCR-VL paper (arXiv 2510.14528v1)
- AMD ROCm Day-0 PaddleOCR-VL-1.5
- olmOCR-2 Allen AI
- LlamaParse pricing 2026
- TinyComputers Strix Halo ROCm 7.2 guide
- pytorch #171687, ROCm #5824
